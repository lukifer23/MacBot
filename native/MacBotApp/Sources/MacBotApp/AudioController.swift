@preconcurrency import AVFoundation
import Darwin
import Foundation

final class AudioController: @unchecked Sendable {
    private final class CapturedBuffer: @unchecked Sendable {
        let value: AVAudioPCMBuffer

        init(_ value: AVAudioPCMBuffer) { self.value = value }
    }

    private let engine = AVAudioEngine()
    private let player = AVAudioPlayerNode()
    private let captureQueue = DispatchQueue(label: "local.macbot.capture", qos: .userInteractive)
    private let writeQueue = DispatchQueue(label: "local.macbot.audio-write", qos: .userInteractive)
    private let playbackQueue = DispatchQueue(label: "local.macbot.playback", qos: .userInteractive)
    private var descriptor: Int32 = -1
    private var converter: AVAudioConverter?
    private var running = false

    func connect(path: String, token: String) throws {
        let fd = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else { throw NativeClientError.socket("Could not create the audio socket") }
        var address = sockaddr_un()
        address.sun_family = sa_family_t(AF_UNIX)
        let bytes = Array(path.utf8CString)
        guard bytes.count <= MemoryLayout.size(ofValue: address.sun_path) else {
            Darwin.close(fd); throw NativeClientError.socket("Audio socket path is too long")
        }
        withUnsafeMutableBytes(of: &address.sun_path) { target in
            target.initializeMemory(as: UInt8.self, repeating: 0)
            bytes.withUnsafeBytes { source in target.copyBytes(from: source) }
        }
        let length = socklen_t(MemoryLayout<sa_family_t>.size + bytes.count)
        let result = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { Darwin.connect(fd, $0, length) }
        }
        guard result == 0 else {
            Darwin.close(fd); throw NativeClientError.socket("MacBot audio service is not reachable")
        }
        descriptor = fd
        try sendJSON(["op": "authenticate", "token": token])
        let reply = try readJSON()
        guard reply["ok"] as? Bool == true else {
            close(); throw NativeClientError.protocolError("Native audio authentication failed")
        }
        playbackQueue.async { [weak self] in self?.readPlayback() }
    }

    func start() async throws {
        let granted = await withCheckedContinuation { continuation in
            AVCaptureDevice.requestAccess(for: .audio) { continuation.resume(returning: $0) }
        }
        guard granted else {
            throw NSError(domain: "MacBot", code: 4, userInfo: [NSLocalizedDescriptionKey: "Microphone access is required for hands-free conversation"])
        }
        if running { return }
        try engine.inputNode.setVoiceProcessingEnabled(true)
        let inputFormat = engine.inputNode.outputFormat(forBus: 0)
        guard inputFormat.sampleRate > 0, inputFormat.channelCount > 0,
              let mono = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 16_000, channels: 1, interleaved: false),
              let conversion = AVAudioConverter(from: inputFormat, to: mono)
        else { throw NativeClientError.protocolError("The built-in microphone format is unavailable") }
        conversion.channelMap = [0]
        converter = conversion
        if !engine.attachedNodes.contains(player) {
            engine.attach(player)
            guard let playbackFormat = AVAudioFormat(
                commonFormat: .pcmFormatFloat32,
                sampleRate: 48_000,
                channels: 1,
                interleaved: false
            ) else {
                throw NativeClientError.protocolError("The playback format is unavailable")
            }
            engine.connect(player, to: engine.mainMixerNode, format: playbackFormat)
        }
        let outputChannels = engine.outputNode.inputFormat(forBus: 0).channelCount
        guard outputChannels > 0,
              let outputFormat = AVAudioFormat(
                standardFormatWithSampleRate: inputFormat.sampleRate,
                channels: outputChannels
              )
        else { throw NativeClientError.protocolError("The built-in speaker format is unavailable") }
        engine.connect(engine.mainMixerNode, to: engine.outputNode, format: outputFormat)
        engine.inputNode.installTap(onBus: 0, bufferSize: 1024, format: inputFormat) { [weak self] buffer, _ in
            guard let self, let copy = AVAudioPCMBuffer(pcmFormat: buffer.format, frameCapacity: buffer.frameLength),
                  let source = buffer.floatChannelData, let destination = copy.floatChannelData else { return }
            copy.frameLength = buffer.frameLength
            for channel in 0..<Int(buffer.format.channelCount) {
                memcpy(destination[channel], source[channel], Int(buffer.frameLength) * MemoryLayout<Float>.size)
            }
            let captured = CapturedBuffer(copy)
            self.captureQueue.async { self.convertAndSend(captured.value, to: mono) }
        }
        engine.prepare()
        try engine.start()
        player.play()
        running = true
    }

    func stopCapture() {
        guard running else { return }
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        player.stop()
        running = false
    }

    func setMuted(_ muted: Bool) {
        guard running else { return }
        engine.inputNode.isVoiceProcessingInputMuted = muted
    }

    func close() {
        stopCapture()
        let fd = descriptor
        descriptor = -1
        if fd >= 0 { Darwin.shutdown(fd, SHUT_RDWR); Darwin.close(fd) }
    }

    private func convertAndSend(_ source: AVAudioPCMBuffer, to format: AVAudioFormat) {
        guard running, descriptor >= 0, let converter else { return }
        let capacity = AVAudioFrameCount(Double(source.frameLength) * 16_000 / source.format.sampleRate + 32)
        guard let output = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: capacity) else { return }
        var supplied = false
        var error: NSError?
        converter.convert(to: output, error: &error) { _, status in
            if supplied { status.pointee = .noDataNow; return nil }
            supplied = true; status.pointee = .haveData; return source
        }
        guard error == nil, output.frameLength > 0, let samples = output.floatChannelData else { return }
        let frame = Data([2]) + Data(bytes: samples[0], count: Int(output.frameLength) * 4)
        writeQueue.async { [weak self] in try? self?.sendFrame(frame) }
    }

    private func readPlayback() {
        while descriptor >= 0 {
            do {
                let frame = try readFrame(limit: 2 * 1024 * 1024)
                guard let kind = frame.first else { continue }
                if kind == 3 { schedulePCM(frame.dropFirst()) }
                else if kind == 4 { player.stop(); if running { player.play() } }
            } catch { close(); return }
        }
    }

    private func schedulePCM(_ body: Data.SubSequence) {
        guard body.count >= 12, (body.count - 12) % 4 == 0 else { return }
        let rate = body.dropFirst(8).prefix(4).reduce(UInt32(0)) { ($0 << 8) | UInt32($1) }
        let sampleData = Data(body.dropFirst(12))
        let count = sampleData.count / 4
        guard let format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: Double(rate), channels: 1, interleaved: false),
              let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(count)),
              let destination = buffer.floatChannelData else { return }
        buffer.frameLength = AVAudioFrameCount(count)
        _ = sampleData.withUnsafeBytes { memcpy(destination[0], $0.baseAddress!, sampleData.count) }
        player.scheduleBuffer(buffer)
        if !player.isPlaying { player.play() }
    }

    private func sendJSON(_ value: [String: Any]) throws {
        try sendFrame(JSONSerialization.data(withJSONObject: value))
    }

    private func readJSON() throws -> [String: Any] {
        guard let value = try JSONSerialization.jsonObject(with: readFrame(limit: 1_048_576)) as? [String: Any] else {
            throw NativeClientError.protocolError("Invalid audio response")
        }
        return value
    }

    private func sendFrame(_ payload: Data) throws {
        guard descriptor >= 0 else { throw NativeClientError.socket("Audio socket is closed") }
        var size = UInt32(payload.count).bigEndian
        var frame = Data(bytes: &size, count: 4); frame.append(payload)
        try frame.withUnsafeBytes { raw in
            var sent = 0
            while sent < raw.count {
                let count = Darwin.send(descriptor, raw.baseAddress!.advanced(by: sent), raw.count - sent, 0)
                guard count > 0 else { throw NativeClientError.socket("Audio socket write failed") }
                sent += count
            }
        }
    }

    private func readFrame(limit: Int) throws -> Data {
        let header = try readExact(4)
        let size = header.reduce(UInt32(0)) { ($0 << 8) | UInt32($1) }
        guard size > 0 && size <= limit else { throw NativeClientError.protocolError("Invalid audio frame") }
        return try readExact(Int(size))
    }

    private func readExact(_ count: Int) throws -> Data {
        var data = Data(count: count); var offset = 0
        try data.withUnsafeMutableBytes { raw in
            while offset < count {
                let received = Darwin.recv(descriptor, raw.baseAddress!.advanced(by: offset), count - offset, 0)
                guard received > 0 else { throw NativeClientError.socket("Audio socket closed") }
                offset += received
            }
        }
        return data
    }
}
