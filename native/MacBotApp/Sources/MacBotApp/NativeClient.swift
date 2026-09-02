import Darwin
import Foundation

enum NativeClientError: LocalizedError {
    case socket(String)
    case protocolError(String)
    case timeout(String)
    var errorDescription: String? {
        switch self {
        case .socket(let value), .protocolError(let value), .timeout(let value): value
        }
    }
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
        let descriptor = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)
        guard descriptor >= 0 else { throw NativeClientError.socket("Could not create the local socket") }
        defer { Darwin.close(descriptor) }
        try configureTimeout(timeout, on: descriptor)
        var address = sockaddr_un()
        address.sun_family = sa_family_t(AF_UNIX)
        let pathBytes = Array(socketPath.utf8CString)
        guard pathBytes.count <= MemoryLayout.size(ofValue: address.sun_path) else {
            throw NativeClientError.socket("Local socket path is too long")
        }
        withUnsafeMutableBytes(of: &address.sun_path) { target in
            target.initializeMemory(as: UInt8.self, repeating: 0)
            pathBytes.withUnsafeBytes { source in target.copyBytes(from: source) }
        }
        let length = socklen_t(MemoryLayout<sa_family_t>.size + pathBytes.count)
        let connected = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.connect(descriptor, $0, length)
            }
        }
        guard connected == 0 else { throw socketFailure("MacBot services are not reachable") }
        try write([
            "op": "authenticate", "token": token,
            "protocol_version": TaskProtocolV3.version,
        ], to: descriptor)
        let hello = try read(from: descriptor)
        guard hello["ok"] as? Bool == true else { throw NativeClientError.protocolError("Native authentication failed") }
        try write(body, to: descriptor)
        let response = try read(from: descriptor)
        guard response["ok"] as? Bool == true else {
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

    private func socketFailure(_ message: String) -> NativeClientError {
        if errno == EAGAIN || errno == EWOULDBLOCK || errno == ETIMEDOUT {
            return .timeout("\(message) before its deadline")
        }
        return .socket(message)
    }

    private func write(_ value: [String: Any], to descriptor: Int32) throws {
        let payload = try JSONSerialization.data(withJSONObject: value)
        guard payload.count <= 12 * 1024 * 1024 else { throw NativeClientError.protocolError("Request is too large") }
        var size = UInt32(payload.count).bigEndian
        var frame = Data(bytes: &size, count: 4)
        frame.append(payload)
        try frame.withUnsafeBytes { raw in
            var sent = 0
            while sent < raw.count {
                let count = Darwin.send(descriptor, raw.baseAddress!.advanced(by: sent), raw.count - sent, 0)
                guard count > 0 else { throw socketFailure("Local socket write failed") }
                sent += count
            }
        }
    }

    private func read(from descriptor: Int32) throws -> [String: Any] {
        let header = try readExact(4, from: descriptor)
        let size = header.reduce(UInt32(0)) { ($0 << 8) | UInt32($1) }
        guard size > 0 && size <= 12 * 1024 * 1024 else { throw NativeClientError.protocolError("Invalid response size") }
        let payload = try readExact(Int(size), from: descriptor)
        guard let value = try JSONSerialization.jsonObject(with: payload) as? [String: Any] else {
            throw NativeClientError.protocolError("Invalid response")
        }
        return value
    }

    private func readExact(_ count: Int, from descriptor: Int32) throws -> Data {
        var data = Data(count: count)
        var offset = 0
        try data.withUnsafeMutableBytes { raw in
            while offset < count {
                let received = Darwin.recv(descriptor, raw.baseAddress!.advanced(by: offset), count - offset, 0)
                guard received > 0 else { throw socketFailure("Local socket closed") }
                offset += received
            }
        }
        return data
    }
}

struct JSONPayload: @unchecked Sendable {
    let value: [String: Any]
    init(_ value: [String: Any]) { self.value = value }
}
