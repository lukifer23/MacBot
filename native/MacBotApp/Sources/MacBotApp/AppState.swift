import Foundation
import SwiftUI

@MainActor
final class AppState: ObservableObject {
    @Published var selectedPage: SidebarPage? = .conversation
    @Published var phase: AssistantPhase = .starting
    @Published var connected = false
    @Published var listening = false
    @Published var muted = false
    @Published var draft = ""
    @Published var heard = ""
    @Published var messages: [ChatItem] = []
    @Published var tasks: [TaskItem] = []
    @Published var metrics: [ServiceMetric] = []
    @Published var errorMessage: String?
    @Published var modelName = "Loading…"
    @Published var voiceName = "Loading…"
    @Published var historyAvailable = false
    @Published var documents: [DocumentItem] = []
    @Published var documentResults: [String] = []
    @Published var importingDocuments = false
    @Published var documentQuery = ""

    private let services = ServiceManager()
    private var client: NativeClient?
    private var audio: AudioController?
    private var eventTask: Task<Void, Never>?
    private var cursor = 0
    private var epoch: String?
    private var responseIndex: [String: Int] = [:]

    func start() {
        guard eventTask == nil else { return }
        eventTask = Task {
            do {
                try KeychainStore.ensureHistoryKey()
                let token = try await services.restart()
                let socket = services.dataDirectory.appending(path: "run/control.sock").path
                for _ in 0..<100 {
                    if FileManager.default.fileExists(atPath: socket) { break }
                    try await Task.sleep(for: .milliseconds(100))
                }
                let native = NativeClient(socketPath: socket, token: token)
                client = native
                let audioController = AudioController()
                try audioController.connect(
                    path: services.dataDirectory.appending(path: "run/audio.sock").path,
                    token: token
                )
                audio = audioController
                connected = true
                phase = .idle
                await refreshStatus()
                await eventLoop(native)
            } catch {
                connected = false
                phase = .error
                errorMessage = error.localizedDescription
                eventTask = nil
            }
        }
    }

    func send() {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, let client else { return }
        draft = ""
        Task {
            do {
                _ = try await client.request(JSONPayload(["op": "chat", "message": text, "speak": true]))
            } catch { show(error) }
        }
    }

    func toggleListening() {
        guard let client else { return }
        let desired = !listening
        Task {
            do {
                if desired {
                    try await audio?.start()
                    audio?.setMuted(muted)
                } else {
                    audio?.stopCapture()
                }
                _ = try await client.request(JSONPayload(["op": "listen", "enabled": desired]))
                listening = desired
                phase = desired ? .listening : .idle
            } catch { show(error) }
        }
    }

    func toggleMuted() {
        muted.toggle()
        audio?.setMuted(muted)
        phase = muted ? .muted : (listening ? .listening : .idle)
    }

    func interrupt() {
        guard let client else { return }
        Task {
            do {
                _ = try await client.request(JSONPayload(["op": "interrupt"]))
                phase = listening ? .listening : .interrupted
            } catch { show(error) }
        }
    }

    func clearConversation() {
        guard let client else { return }
        Task {
            do {
                _ = try await client.request(JSONPayload(["op": "clear"]))
                messages.removeAll()
                tasks.removeAll()
                responseIndex.removeAll()
                heard = ""
            } catch { show(error) }
        }
    }

    func shutdown() {
        eventTask?.cancel()
        eventTask = nil
        audio?.close()
        audio = nil
        services.stopSynchronously()
    }

    func refreshDocuments() {
        guard let client else { return }
        Task {
            do {
                let response = try await client.request(JSONPayload(["op": "documents"])).value
                documents = (response["documents"] as? [[String: Any]] ?? []).compactMap { item in
                    guard let id = item["id"] as? String, let title = item["title"] as? String else { return nil }
                    return DocumentItem(id: id, title: title, type: item["type"] as? String ?? "text", length: item["length"] as? Int ?? 0)
                }
            } catch { show(error) }
        }
    }

    func importDocument(_ url: URL) {
        guard let client else { return }
        Task {
            do {
                let accessed = url.startAccessingSecurityScopedResource()
                defer { if accessed { url.stopAccessingSecurityScopedResource() } }
                let content = try Data(contentsOf: url, options: .mappedIfSafe)
                guard content.count <= 8 * 1024 * 1024 else {
                    throw NSError(domain: "MacBot", code: 3, userInfo: [NSLocalizedDescriptionKey: "Documents are limited to 8 MiB"])
                }
                _ = try await client.request(JSONPayload([
                    "op": "document_import", "name": url.deletingPathExtension().lastPathComponent,
                    "suffix": "." + url.pathExtension.lowercased(), "content": content.base64EncodedString(),
                ]))
                refreshDocuments()
            } catch { show(error) }
        }
    }

    func deleteDocument(_ id: String) {
        guard let client else { return }
        Task {
            do {
                _ = try await client.request(JSONPayload(["op": "document_delete", "id": id]))
                refreshDocuments()
            } catch { show(error) }
        }
    }

    func searchDocuments(_ query: String) {
        guard let client else { return }
        Task {
            do {
                let response = try await client.request(JSONPayload(["op": "document_search", "query": query])).value
                documentResults = (response["results"] as? [[String: Any]] ?? []).compactMap { $0["content"] as? String }
            } catch { show(error) }
        }
    }

    func refreshStatus() async {
        guard let client else { return }
        do {
            let response = try await client.request(JSONPayload(["op": "status"])).value
            guard let status = response["status"] as? [String: Any] else { return }
            if let models = status["models"] as? [String: Any] {
                modelName = models["llm"] as? String ?? "Unknown"
                voiceName = models["tts_voice"] as? String ?? "Unknown"
            }
            if let history = status["history"] as? [String: Any] {
                historyAvailable = history["available"] as? Bool ?? false
            }
            let turnQueue = status["turn_queue"] as? Int ?? 0
            let speechQueue = status["speech_queue"] as? Int ?? 0
            let dropped = status["audio_dropped"] as? Int ?? 0
            let errors = status["errors"] as? Int ?? 0
            metrics = [
                .init(id: "turn", label: "Turn queue", value: "\(turnQueue)"),
                .init(id: "speech", label: "Speech queue", value: "\(speechQueue)"),
                .init(id: "dropped", label: "Dropped frames", value: "\(dropped)"),
                .init(id: "errors", label: "Turn errors", value: "\(errors)"),
            ]
        } catch { show(error) }
    }

    private func eventLoop(_ native: NativeClient) async {
        while !Task.isCancelled {
            do {
                var request: [String: Any] = ["op": "events", "after": cursor]
                if let epoch { request["epoch"] = epoch }
                let response = try await native.request(JSONPayload(request)).value
                cursor = response["cursor"] as? Int ?? cursor
                epoch = response["epoch"] as? String
                if response["reset"] as? Bool == true {
                    messages.removeAll(); tasks.removeAll(); responseIndex.removeAll()
                }
                for event in response["events"] as? [[String: Any]] ?? [] {
                    consume(event)
                }
                await refreshStatus()
            } catch {
                if !Task.isCancelled {
                    connected = false
                    show(error)
                    try? await Task.sleep(for: .seconds(1))
                }
            }
        }
    }

    private func consume(_ event: [String: Any]) {
        let kind = event["kind"] as? String ?? ""
        let state = event["state"] as? String ?? ""
        let turnID = event["turn_id"] as? String ?? UUID().uuidString
        let data = event["data"] as? [String: Any] ?? [:]
        switch kind {
        case "transcription", "user":
            let text = data["text"] as? String ?? ""
            heard = text
            if !text.isEmpty, !messages.contains(where: { $0.id == "user-\(turnID)" }) {
                messages.append(.init(id: "user-\(turnID)", role: .user, text: text))
            }
        case "delta":
            let text = data["text"] as? String ?? ""
            if let index = responseIndex[turnID] {
                messages[index].text += text
            } else {
                messages.append(.init(id: "assistant-\(turnID)", role: .assistant, text: text))
                responseIndex[turnID] = messages.count - 1
            }
        case "planning": phase = .planning
        case "generating": phase = .thinking
        case "action", "tool": phase = .acting
        case "speaking": phase = .speaking
        case "listening":
            listening = data["enabled"] as? Bool ?? listening
            phase = listening ? .listening : .idle
        case "tool_result":
            let tool = data["tool"] as? String ?? "Action"
            let result = data["result"].map { String(describing: $0) } ?? ""
            tasks.append(.init(id: "\(turnID)-\(tasks.count)", title: tool, state: state, detail: result))
        default: break
        }
        if state == "completed" { phase = listening ? .listening : .idle }
        if state == "interrupted" { phase = .interrupted }
        if state == "failed" {
            phase = .error
            errorMessage = data["message"] as? String ?? "The turn failed"
        }
    }

    private func show(_ error: Error) {
        errorMessage = error.localizedDescription
        phase = .error
    }
}
