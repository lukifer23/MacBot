import Foundation
import AVFoundation
import AVFAudio

// Protocol: uint32 big-endian length, uint8 kind, payload. Kind 1 = JSON.
// Python -> helper PCM: kind 2, uint64 generation, uint32 rate, float32 LE mono.
// Helper -> Python PCM: kind 2, float32 LE mono at 16 kHz.
// Only the main control queue touches the engine. Audio callbacks never perform IPC.
final class Bridge {
    let engine = AVAudioEngine()
    let player = AVAudioPlayerNode()
    let control = DispatchQueue(label: "macbot.audio.control")
    let output = DispatchQueue(label: "macbot.audio.output")
    let outputCredits = DispatchSemaphore(value: 64)
    let inputCredits = DispatchSemaphore(value: 16)
    let captureLock = NSLock()
    var captured: [AVAudioPCMBuffer] = []
    var dropped = 0
    var started = false
    var capturing = false
    var generation: UInt64 = 0
    var pending = 0
    var converter: AVAudioConverter?
    var timer: DispatchSourceTimer?
    let mono = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 16000, channels: 1, interleaved: false)!

    func send(_ kind: UInt8, _ data: Data) {
        // The control queue may drop capture PCM, but never accumulates unlimited
        // pipe writes if the parent stalls. Lifecycle backpressure fails closed.
        guard outputCredits.wait(timeout: .now()) == .success else {
            if kind == 2 {
                captureLock.lock(); dropped += 1; captureLock.unlock()
                return
            }
            exit(2)
        }
        output.async {
            defer { self.outputCredits.signal() }
            var length = UInt32(data.count + 1).bigEndian
            var frame = Data(bytes: &length, count: 4)
            frame.append(kind)
            frame.append(data)
            do { try FileHandle.standardOutput.write(contentsOf: frame) }
            catch { exit(1) }
        }
    }
    func event(_ name: String, _ more: [String: Any] = [:]) {
        var obj = more; obj["event"] = name
        obj["time_ns"] = DispatchTime.now().uptimeNanoseconds
        if let data = try? JSONSerialization.data(withJSONObject: obj) { send(1, data) }
    }
    func start(capture: Bool) throws {
        if started { setCapture(capture); return }
        try engine.inputNode.setVoiceProcessingEnabled(true)
        let format = engine.inputNode.outputFormat(forBus: 0)
        guard format.sampleRate > 0, format.channelCount > 0 else {
            throw NSError(domain: "MacBot", code: 1, userInfo: [NSLocalizedDescriptionKey: "No microphone input format"])
        }
        converter = AVAudioConverter(from: format, to: mono)
        engine.attach(player)
        engine.connect(player, to: engine.mainMixerNode, format: mono)
        // VoiceProcessingIO requires matching client input/output sample rates.
        // Its output can otherwise retain the engine's 44.1 kHz default even
        // when the microphone runs at 48 kHz, making AU initialization fail.
        // The mixer converts our 16 kHz playback stream to the device rate.
        let outputChannels = engine.outputNode.inputFormat(forBus: 0).channelCount
        guard outputChannels > 0,
              let outputFormat = AVAudioFormat(standardFormatWithSampleRate: format.sampleRate,
                                               channels: outputChannels) else {
            throw NSError(domain: "MacBot", code: 3, userInfo: [NSLocalizedDescriptionKey: "No speaker output format"])
        }
        engine.connect(engine.mainMixerNode, to: engine.outputNode, format: outputFormat)
        engine.inputNode.installTap(onBus: 0, bufferSize: 1024, format: format) { [weak self] buffer, _ in
            guard let self = self, self.captureLock.try() else { return }
            defer { self.captureLock.unlock() }
            guard self.capturing else { return }
            guard self.captured.count < 16 else { self.dropped += 1; return }
            guard let copy = AVAudioPCMBuffer(pcmFormat: buffer.format, frameCapacity: buffer.frameLength),
                  let src = buffer.floatChannelData, let dst = copy.floatChannelData else { return }
            copy.frameLength = buffer.frameLength
            for channel in 0..<Int(buffer.format.channelCount) {
                memcpy(dst[channel], src[channel], Int(buffer.frameLength) * MemoryLayout<Float>.size)
            }
            self.captured.append(copy)
        }
        setCapture(capture)
        engine.prepare()
        try engine.start()
        player.play()
        started = true
        let t = DispatchSource.makeTimerSource(queue: control)
        t.schedule(deadline: .now(), repeating: .milliseconds(10))
        t.setEventHandler { [weak self] in self?.drainCapture() }
        t.resume(); timer = t
        event("ready", ["aec": engine.inputNode.isVoiceProcessingEnabled, "sample_rate": 16000,
                        "input_sample_rate": format.sampleRate,
                        "output_sample_rate": engine.outputNode.inputFormat(forBus: 0).sampleRate])
    }
    func setCapture(_ enabled: Bool) {
        captureLock.lock(); capturing = enabled; captured.removeAll(); captureLock.unlock()
        engine.inputNode.isVoiceProcessingInputMuted = !enabled
        event("capture", ["enabled": enabled])
    }
    func drainCapture() {
        captureLock.lock()
        let buffers = captured; captured.removeAll(keepingCapacity: true)
        let lost = dropped; dropped = 0
        captureLock.unlock()
        if lost > 0 { event("overflow", ["frames": lost]) }
        for buffer in buffers {
            guard let converter = converter else { continue }
            let capacity = AVAudioFrameCount(Double(buffer.frameLength) * 16000 / buffer.format.sampleRate + 32)
            guard let result = AVAudioPCMBuffer(pcmFormat: mono, frameCapacity: capacity) else { continue }
            var supplied = false
            var error: NSError?
            converter.convert(to: result, error: &error) { _, status in
                if supplied { status.pointee = .noDataNow; return nil }
                supplied = true; status.pointee = .haveData; return buffer
            }
            if let error = error { event("error", ["message": error.localizedDescription]); continue }
            if let samples = result.floatChannelData, result.frameLength > 0 {
                send(2, Data(bytes: samples[0], count: Int(result.frameLength) * 4))
            }
        }
    }
    func stopPlayback(_ next: UInt64) {
        player.stop(); generation = next; pending = 0
        if started { player.play() }
        event("stopped", ["generation": generation])
    }
    func command(_ obj: [String: Any]) throws {
        switch obj["op"] as? String {
        case "start":
            generation = (obj["generation"] as? NSNumber)?.uint64Value ?? 0
            try start(capture: obj["capture"] as? Bool ?? true)
        case "capture":
            setCapture(obj["enabled"] as? Bool ?? false)
        case "cancel": stopPlayback((obj["generation"] as? NSNumber)?.uint64Value ?? generation + 1)
        case "stop":
            stopPlayback(generation + 1)
            setCapture(false)
        case "shutdown":
            timer?.cancel(); engine.stop(); event("closed"); output.sync {}; exit(0)
        default: throw NSError(domain: "MacBot", code: 2, userInfo: [NSLocalizedDescriptionKey: "Unknown audio command"])
        }
    }
    func pcm(_ body: Data) throws {
        guard body.count >= 12, (body.count - 12) % 4 == 0, body.count <= 256 * 1024 else { return }
        let gen = body.prefix(8).reduce(UInt64(0)) { ($0 << 8) | UInt64($1) }
        let rate = body.dropFirst(8).prefix(4).reduce(UInt32(0)) { ($0 << 8) | UInt32($1) }
        guard started, gen == generation, rate == 16000 else { event("rejected", ["generation": gen]); return }
        guard pending < 4 else { event("error", ["message": "Playback credit limit exceeded"]); return }
        let count = (body.count - 12) / 4
        guard let buffer = AVAudioPCMBuffer(pcmFormat: mono, frameCapacity: AVAudioFrameCount(count)), let dest = buffer.floatChannelData else { return }
        buffer.frameLength = AVAudioFrameCount(count)
        body.dropFirst(12).withUnsafeBytes { raw in memcpy(dest[0], raw.baseAddress!, count * 4) }
        pending += 1
        if pending == 1 { event("playback_scheduled", ["generation": gen]) }
        player.scheduleBuffer(buffer, completionCallbackType: .dataPlayedBack) { [weak self] _ in
            self?.control.async {
                guard let self = self, self.generation == gen else { return }
                self.pending = max(0, self.pending - 1)
                self.event("played", ["generation": gen])
            }
        }
    }
}

func readExact(_ count: Int) -> Data? {
    var data = Data()
    while data.count < count {
        guard let part = try? FileHandle.standardInput.read(upToCount: count - data.count), !part.isEmpty else { return nil }
        data.append(part)
    }
    return data
}
let bridge = Bridge()
if CommandLine.arguments.contains("--probe") {
    bridge.event("capabilities", ["voice_processing": true, "protocol": 1])
    bridge.output.sync {}; exit(0)
}
DispatchQueue.global(qos: .userInteractive).async {
    while let header = readExact(4) {
        let size = header.reduce(UInt32(0)) { ($0 << 8) | UInt32($1) }
        guard size > 0, size <= 512 * 1024, let frame = readExact(Int(size)) else { break }
        // Bound IPC work waiting for the engine. Blocking stays off audio callbacks.
        guard bridge.inputCredits.wait(timeout: .now() + .seconds(1)) == .success else { exit(2) }
        bridge.control.async {
            defer { bridge.inputCredits.signal() }
            do {
                if frame[0] == 1 {
                    guard let obj = try JSONSerialization.jsonObject(with: frame.dropFirst()) as? [String: Any] else { return }
                    try bridge.command(obj)
                } else if frame[0] == 2 { try bridge.pcm(Data(frame.dropFirst())) }
            } catch { bridge.event("error", ["message": error.localizedDescription]) }
        }
    }
    bridge.control.async { bridge.engine.stop(); exit(0) }
}
RunLoop.main.run()
