import Darwin
import Foundation

enum NativeClientError: LocalizedError {
    case socket(String)
    case protocolError(String)
    case timeout(String)
    case rejected(NativeRequestFailure)
    var errorDescription: String? {
        switch self {
        case .socket(let value), .protocolError(let value), .timeout(let value): value
        case .rejected(let failure): failure.message
        }
    }
}

func connectNativeSocket(path: String) throws -> Int32 {
    let descriptor = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)
    guard descriptor >= 0 else { throw NativeClientError.socket("Could not create the local socket") }
    var connected = false
    defer { if !connected { Darwin.close(descriptor) } }

    // Runtime replacement can close a peer mid-write. Return EPIPE to callers.
    var noSignal: Int32 = 1
    guard setsockopt(descriptor, SOL_SOCKET, SO_NOSIGPIPE, &noSignal, socklen_t(MemoryLayout<Int32>.size)) == 0 else {
        throw NativeClientError.socket("Could not protect the local socket from disconnected writes")
    }
    var address = sockaddr_un()
    address.sun_family = sa_family_t(AF_UNIX)
    let bytes = Array(path.utf8CString)
    guard bytes.count <= MemoryLayout.size(ofValue: address.sun_path) else {
        throw NativeClientError.socket("Local socket path is too long")
    }
    withUnsafeMutableBytes(of: &address.sun_path) { target in
        target.initializeMemory(as: UInt8.self, repeating: 0)
        bytes.withUnsafeBytes { source in target.copyBytes(from: source) }
    }
    let length = socklen_t(MemoryLayout<sa_family_t>.size + bytes.count)
    connected = withUnsafePointer(to: &address) {
        $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { Darwin.connect(descriptor, $0, length) == 0 }
    }
    guard connected else { throw NativeClientError.socket("MacBot services are not reachable") }
    return descriptor
}

struct NativeRequestFailure: Decodable, Sendable {
    let code: String
    let message: String
    let retryable: Bool
    let failureClass: FailureClass
}

actor NativeClient {
    let socketPath: String
    let token: String

    init(socketPath: String, token: String) {
        self.socketPath = socketPath
        self.token = token
    }

    func request(_ payload: JSONPayload, timeout: TimeInterval = 30) throws -> JSONPayload {
        var body = payload.value
        body["protocol_version"] = TaskProtocolV3.version
        let requestID = UUID().uuidString
        body["request_id"] = requestID
        let descriptor = try connectNativeSocket(path: socketPath)
        defer { Darwin.close(descriptor) }
        try configureTimeout(timeout, on: descriptor)
        try write([
            "op": "authenticate", "token": token,
            "protocol_version": TaskProtocolV3.version,
        ], to: descriptor)
        let hello = try read(from: descriptor)
        guard hello["ok"] as? Bool == true else { throw NativeClientError.protocolError("Native authentication failed") }
        try write(body, to: descriptor)
        let response = try read(from: descriptor)
        guard response["request_id"] as? String == requestID else {
            throw NativeClientError.protocolError("The local response did not match its request")
        }
        guard response["ok"] as? Bool == true else {
            if let failure = response["failure"] as? [String: Any],
               let data = try? JSONSerialization.data(withJSONObject: failure) {
                let decoder = JSONDecoder()
                decoder.keyDecodingStrategy = .convertFromSnakeCase
                if let rejection = try? decoder.decode(NativeRequestFailure.self, from: data) {
                    throw NativeClientError.rejected(rejection)
                }
            }
            throw NativeClientError.protocolError(response["message"] as? String ?? "Native request failed")
        }
        return JSONPayload(response)
    }

    private func configureTimeout(_ seconds: TimeInterval, on descriptor: Int32) throws {
        let bounded = max(1, seconds)
        var value = timeval(
            tv_sec: Int(bounded),
            tv_usec: Int32((bounded - floor(bounded)) * 1_000_000)
        )
        let size = socklen_t(MemoryLayout<timeval>.size)
        guard setsockopt(descriptor, SOL_SOCKET, SO_RCVTIMEO, &value, size) == 0,
              setsockopt(descriptor, SOL_SOCKET, SO_SNDTIMEO, &value, size) == 0 else {
            throw NativeClientError.socket("Could not configure the local request deadline")
        }
    }

    private func write(_ value: [String: Any], to descriptor: Int32) throws {
        try NativeFrame.write(JSONSerialization.data(withJSONObject: value), to: descriptor, limit: Self.limit)
    }

    private func read(from descriptor: Int32) throws -> [String: Any] {
        let payload = try NativeFrame.read(from: descriptor, limit: Self.limit)
        guard let value = try JSONSerialization.jsonObject(with: payload) as? [String: Any] else {
            throw NativeClientError.protocolError("Invalid response")
        }
        return value
    }

    private static let limit = 12 * 1024 * 1024
}

enum NativeFrame {
    static func write(_ payload: Data, to socket: Int32, limit: Int) throws {
        guard socket >= 0 else { throw NativeClientError.socket("Local socket is closed") }
        guard !payload.isEmpty, payload.count <= limit else {
            throw NativeClientError.protocolError("Invalid local frame size")
        }
        var length = UInt32(payload.count).bigEndian
        try withUnsafeBytes(of: &length) { try writeAll($0, to: socket) }
        try payload.withUnsafeBytes { try writeAll($0, to: socket) }
    }

    private static func writeAll(_ bytes: UnsafeRawBufferPointer, to socket: Int32) throws {
        guard let base = bytes.baseAddress else { return }
        var offset = 0
        while offset < bytes.count {
            let count = Darwin.send(socket, base.advanced(by: offset), bytes.count - offset, 0)
            if count < 0 && errno == EINTR { continue }
            guard count > 0 else { throw socketFailure("Local socket write failed") }
            offset += count
        }
    }

    static func read(from socket: Int32, limit: Int) throws -> Data {
        let header = try readExact(4, from: socket)
        let size = header.reduce(UInt32(0)) { ($0 << 8) | UInt32($1) }
        guard size > 0 && size <= limit else {
            throw NativeClientError.protocolError("Invalid local frame size")
        }
        return try readExact(Int(size), from: socket)
    }

    private static func readExact(_ count: Int, from socket: Int32) throws -> Data {
        var data = Data(count: count)
        var offset = 0
        try data.withUnsafeMutableBytes { raw in
            while offset < count {
                let received = Darwin.recv(socket, raw.baseAddress!.advanced(by: offset), count - offset, 0)
                if received < 0 && errno == EINTR { continue }
                guard received > 0 else { throw socketFailure("Local socket closed") }
                offset += received
            }
        }
        return data
    }

    private static func socketFailure(_ message: String) -> NativeClientError {
        if errno == EAGAIN || errno == EWOULDBLOCK || errno == ETIMEDOUT {
            return .timeout("\(message) before its deadline")
        }
        return .socket(message)
    }
}

struct JSONPayload: @unchecked Sendable {
    let value: [String: Any]
    init(_ value: [String: Any]) { self.value = value }
}
