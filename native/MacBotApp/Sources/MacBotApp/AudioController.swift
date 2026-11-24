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
    private let captureSlots = DispatchSemaphore(value: 6)
    private let writeQueue = DispatchQueue(label: "local.macbot.audio-write", qos: .userInteractive)
    private let readQueue = DispatchQueue(label: "local.macbot.audio-read", qos: .userInteractive)
    private let playbackQueue = DispatchQueue(label: "local.macbot.playback", qos: .userInteractive)
    private var descriptor: Int32 = -1
    private var readerOwnsSocket = false
    private var converter: AVAudioConverter?
    private var running = false
    private var capturing = false
    private var pending: [UInt64: Int] = [:]
    private var ended: Set<UInt64> = []
    private var scheduled: Set<UInt64> = []
    private var playbackFloor: UInt64 = 0
    private var closedPlaybackGeneration: UInt64?

    func connect(path: String, token: String) throws {
        descriptor = try connectNativeSocket(path: path)
        do {
            var sendDeadline = timeval(tv_sec: 1, tv_usec: 0)
            guard setsockopt(
                descriptor, SOL_SOCKET, SO_SNDTIMEO, &sendDeadline, socklen_t(MemoryLayout<timeval>.size)
            ) == 0 else {
                throw NativeClientError.socket("Could not bound audio writes")
            }
            try sendJSON([
                "op": "authenticate", "token": token,
                "protocol_version": TaskProtocolV3.version,
            ])
            let reply = try readJSON(from: descriptor)
            guard reply["ok"] as? Bool == true else {
                throw NativeClientError.protocolError("Native audio authentication failed")
            }
            try startPlaybackOnly()
        } catch {
            close()
            throw error
        }
        let connectedSocket = descriptor
        readerOwnsSocket = true
        readQueue.async { [weak self] in
            defer { Darwin.close(connectedSocket) }
            self?.readPlayback(from: connectedSocket)
        }
    }

    func start(captureID: String) async throws {
        let granted = await withCheckedContinuation { continuation in
            AVCaptureDevice.requestAccess(for: .audio) { continuation.resume(returning: $0) }
        }
        guard granted else {
            throw NSError(domain: "MacBot", code: 4, userInfo: [NSLocalizedDescriptionKey: "Microphone access is required for hands-free conversation"])
        }
        try playbackQueue.sync { try startCapture(captureID: captureID) }
    }

    private func startCapture(captureID: String) throws {
        guard writeQueue.sync(execute: { descriptor >= 0 }) else {
            throw NativeClientError.socket("Audio socket is closed")
        }
        if capturing { return }
        if running {
            engine.stop()
            player.stop()
            engine.disconnectNodeOutput(player)
            engine.disconnectNodeOutput(engine.mainMixerNode)
            running = false
        }
        try engine.inputNode.setVoiceProcessingEnabled(true)
        let inputFormat = engine.inputNode.outputFormat(forBus: 0)
        guard inputFormat.sampleRate > 0, inputFormat.channelCount > 0,
              let mono = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 16_000, channels: 1, interleaved: false),
              let conversion = AVAudioConverter(from: inputFormat, to: mono)
        else { throw NativeClientError.protocolError("The built-in microphone format is unavailable") }
        conversion.channelMap = [0]
        try connectPlaybackGraph(outputSampleRate: inputFormat.sampleRate)
        engine.inputNode.installTap(onBus: 0, bufferSize: 1024, format: inputFormat) { [weak self] buffer, _ in
            guard let self, self.captureSlots.wait(timeout: .now()) == .success else { return }
            guard let copy = AVAudioPCMBuffer(pcmFormat: buffer.format, frameCapacity: buffer.frameLength),
                  let source = buffer.floatChannelData, let destination = copy.floatChannelData else {
                self.captureSlots.signal()
                return
            }
            copy.frameLength = buffer.frameLength
            for channel in 0..<Int(buffer.format.channelCount) {
                memcpy(destination[channel], source[channel], Int(buffer.frameLength) * MemoryLayout<Float>.size)
            }
            let captured = CapturedBuffer(copy)
            self.captureQueue.async {
                defer { self.captureSlots.signal() }
                self.convertAndSend(captured.value, to: mono, using: conversion)
            }
        }
        engine.prepare()
        do {
            try engine.start()
        } catch {
            engine.inputNode.removeTap(onBus: 0)
            throw error
        }
        player.play()
        running = true
        capturing = true
        writeQueue.sync { converter = conversion }
        sendEventSynchronously([
            "event": "ready",
            "aec": engine.inputNode.isVoiceProcessingEnabled,
            "capture_id": captureID,
            "input_sample_rate": inputFormat.sampleRate,
            "output_sample_rate": engine.outputNode.inputFormat(forBus: 0).sampleRate,
        ])
    }

    func stopCapture() {
        playbackQueue.sync { stopCaptureOnPlaybackQueue() }
    }

    private func stopCaptureOnPlaybackQueue() {
        guard capturing else { return }
        writeQueue.sync { converter = nil }
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        player.stop()
        engine.disconnectNodeOutput(player)
        engine.disconnectNodeOutput(engine.mainMixerNode)
        try? engine.inputNode.setVoiceProcessingEnabled(false)
        capturing = false
        running = false
        try? startPlaybackOnly()
    }

    func setMuted(_ muted: Bool) {
        playbackQueue.sync {
            if capturing { engine.inputNode.isVoiceProcessingInputMuted = muted }
        }
    }

    func close() {
        playbackQueue.sync {
            if capturing { engine.inputNode.removeTap(onBus: 0) }
            engine.stop()
            player.stop()
            capturing = false
            running = false
            pending.removeAll()
            ended.removeAll()
            scheduled.removeAll()
            writeQueue.sync {
                converter = nil
                let fd = descriptor
                descriptor = -1
                if fd >= 0 {
                    Darwin.shutdown(fd, SHUT_RDWR)
                    if !readerOwnsSocket { Darwin.close(fd) }
                }
            }
        }
    }

    private func startPlaybackOnly() throws {
        if running { return }
        let deviceFormat = engine.outputNode.inputFormat(forBus: 0)
        guard deviceFormat.sampleRate > 0 else {
            throw NativeClientError.protocolError("The built-in speaker format is unavailable")
        }
        try connectPlaybackGraph(outputSampleRate: deviceFormat.sampleRate)
        engine.prepare()
        try engine.start()
        player.play()
        running = true
        sendEventSynchronously([
            "event": "ready",
            "aec": false,
            "output_sample_rate": engine.outputNode.inputFormat(forBus: 0).sampleRate,
        ])
    }

    private func connectPlaybackGraph(outputSampleRate: Double) throws {
        if !engine.attachedNodes.contains(player) { engine.attach(player) }
        guard let playbackFormat = AVAudioFormat(
            commonFormat: .pcmFormatFloat32,
            sampleRate: 48_000,
            channels: 1,
            interleaved: false
        ) else { throw NativeClientError.protocolError("The playback format is unavailable") }
        let outputChannels = engine.outputNode.inputFormat(forBus: 0).channelCount
        guard outputChannels > 0,
              let outputFormat = AVAudioFormat(
                standardFormatWithSampleRate: outputSampleRate,
                channels: outputChannels
              )
        else { throw NativeClientError.protocolError("The built-in speaker format is unavailable") }
        engine.connect(player, to: engine.mainMixerNode, format: playbackFormat)
        engine.connect(engine.mainMixerNode, to: engine.outputNode, format: outputFormat)
    }

    private func convertAndSend(
        _ source: AVAudioPCMBuffer, to format: AVAudioFormat, using converter: AVAudioConverter
    ) {
        guard writeQueue.sync(execute: { self.converter === converter }) else { return }
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
        // Keep the admission slot until the socket write finishes. A slow
        // reader cannot move the same unbounded backlog to the write queue.
        writeQueue.sync {
            guard self.converter === converter else { return }
            sendAudioFrame(frame)
        }
    }

    private func readPlayback(from socket: Int32) {
        while true {
            do {
                let frame = try NativeFrame.read(from: socket, limit: 2 * 1024 * 1024)
                playbackQueue.sync {
                    guard running, let kind = frame.first else { return }
                    if kind == 3 { schedulePCM(frame.dropFirst()) }
                    else if kind == 4 { stopPlayback(frame.dropFirst()) }
                    else if kind == 5 { finishPlayback(frame.dropFirst()) }
                }
            } catch {
                close()
                return
            }
        }
    }

    private func schedulePCM(_ body: Data.SubSequence) {
        guard body.count >= 12, (body.count - 12) % 4 == 0 else { return }
        let generation = body.prefix(8).reduce(UInt64(0)) { ($0 << 8) | UInt64($1) }
        guard generation >= playbackFloor, generation != closedPlaybackGeneration else { return }
        playbackFloor = generation
        let rate = body.dropFirst(8).prefix(4).reduce(UInt32(0)) { ($0 << 8) | UInt32($1) }
        let sampleData = Data(body.dropFirst(12))
        let count = sampleData.count / 4
        guard let format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: Double(rate), channels: 1, interleaved: false),
              let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(count)),
              let destination = buffer.floatChannelData else { return }
        buffer.frameLength = AVAudioFrameCount(count)
        _ = sampleData.withUnsafeBytes { memcpy(destination[0], $0.baseAddress!, sampleData.count) }
        pending[generation, default: 0] += 1
        if scheduled.insert(generation).inserted {
            sendEvent(["event": "playback_scheduled", "generation": generation])
        }
        player.scheduleBuffer(buffer, completionCallbackType: .dataPlayedBack) { [weak self] _ in
            guard let controller = self else { return }
            controller.playbackQueue.async { controller.played(generation) }
        }
        if !player.isPlaying { player.play() }
    }

    private func stopPlayback(_ body: Data.SubSequence) {
        guard body.count == 8 else { return }
        let generation = body.reduce(UInt64(0)) { ($0 << 8) | UInt64($1) }
        guard generation >= playbackFloor else { return }
        playbackFloor = generation
        closedPlaybackGeneration = generation
        player.stop()
        pending.removeAll()
        ended.removeAll()
        scheduled.removeAll()
        if running { player.play() }
        sendEvent(["event": "stopped", "generation": generation])
    }

    private func finishPlayback(_ body: Data.SubSequence) {
        guard body.count == 8 else { return }
        let generation = body.reduce(UInt64(0)) { ($0 << 8) | UInt64($1) }
        guard generation >= playbackFloor, generation != closedPlaybackGeneration else { return }
        playbackFloor = generation
        closedPlaybackGeneration = generation
        ended.insert(generation)
        emitDrainedIfReady(generation)
    }

    private func played(_ generation: UInt64) {
        guard let count = pending[generation], count > 0 else { return }
        pending[generation] = count - 1
        emitDrainedIfReady(generation)
    }

    private func emitDrainedIfReady(_ generation: UInt64) {
        guard ended.contains(generation), pending[generation, default: 0] == 0 else { return }
        pending[generation] = nil
        ended.remove(generation)
        scheduled.remove(generation)
        sendEvent(["event": "drained", "generation": generation])
    }

    private func sendEvent(_ value: [String: Any]) {
        guard let encoded = try? JSONSerialization.data(withJSONObject: value) else { return }
        let frame = Data([1]) + encoded
        writeQueue.async { [weak self] in self?.sendAudioFrame(frame) }
    }

    private func sendEventSynchronously(_ value: [String: Any]) {
        guard let encoded = try? JSONSerialization.data(withJSONObject: value) else { return }
        let frame = Data([1]) + encoded
        writeQueue.sync { sendAudioFrame(frame) }
    }

    private func sendAudioFrame(_ frame: Data) {
        do {
            try sendFrame(frame)
        } catch {
            // A partial frame makes this byte stream unusable.
            if descriptor >= 0 { Darwin.shutdown(descriptor, SHUT_RDWR) }
        }
    }

    private func sendJSON(_ value: [String: Any]) throws {
        try sendFrame(JSONSerialization.data(withJSONObject: value))
    }

    private func readJSON(from socket: Int32) throws -> [String: Any] {
        guard let value = try JSONSerialization.jsonObject(with: NativeFrame.read(from: socket, limit: 1_048_576)) as? [String: Any] else {
            throw NativeClientError.protocolError("Invalid audio response")
        }
        return value
    }

    private func sendFrame(_ payload: Data) throws {
        try NativeFrame.write(payload, to: descriptor, limit: 256 * 1024)
    }
}
