import Foundation
import SwiftUI

@MainActor
final class AppState: ObservableObject {
    @Published var selectedPage: SidebarPage? = .conversation
    @Published var productState: ProductState = .starting
    @Published var phase: AssistantPhase = .starting
    @Published var connected = false
    @Published var connectionDetail = "Starting local services"
    @Published var listening = false
    @Published var muted = false
    @Published var composerMode: ComposerMode = .conversation
    @Published var draft = ""
    @Published var heard = ""
    @Published var messages: [ChatItem] = []
    @Published var tasks: [TaskItem] = []
    @Published var metrics: [ServiceMetric] = []
    @Published var errorMessage: String?
    @Published var modelName = "Loading…"
    @Published var voiceName = "Loading…"
    @Published var selectedVoice = ""
    @Published var availableVoices: [VoiceOption] = []
    @Published var historyAvailable = false
    @Published var documents: [DocumentItem] = []
    @Published var documentResults: [DocumentSearchResult] = []
    @Published var libraryState: LibraryLoadState = .idle
    @Published var searchState: SearchLoadState = .idle
    @Published var importingDocuments = false
    @Published var documentQuery = ""
    @Published var retentionDays = 30
    @Published var endpointMilliseconds = 350
    @Published var contextLength = 16_384
    @Published var searchCredentialConfigured = false
    @Published var searchCredential = ""
    @Published var restartRequired = false
    @Published var speakTypedReplies = true
    @Published var isRestarting = false
    @Published var confirmClearConversation = false
    @Published var pendingDocumentDeletion: DocumentItem?
    @Published var confirmCredentialRemoval = false

    private var savedRetentionDays = 30
    private var savedEndpointMilliseconds = 350
    private var savedContextLength = 16_384
    private var savedVoice = ""

    private let services = ServiceManager()
    private var client: NativeClient?
    private var audio: AudioController?
    private var eventTask: Task<Void, Never>?
    private var cursor = 0
    private var epoch: String?
    private var responseIndex: [String: Int] = [:]
    private var historyKey: Data?

    var timeline: [TimelineItem] {
        (messages.map(TimelineItem.message) + tasks.map(TimelineItem.task)).sorted {
            if $0.sequence == $1.sequence { return $0.id < $1.id }
            return $0.sequence < $1.sequence
        }
    }

    var canConverse: Bool { connected && productState.isOperational && !isRestarting }
    var canListen: Bool { canConverse }
    var canManageLibrary: Bool { connected && productState.isOperational && !isRestarting }
    var canChangeSettings: Bool { connected && productState.isOperational && !isRestarting }
    var canInterrupt: Bool { [.planning, .thinking, .acting, .speaking].contains(phase) }
    var hasExecutingTask: Bool {
        tasks.contains { [.queued, .running, .pauseRequested, .cancelRequested].contains($0.state) }
    }
    var settingsHaveChanges: Bool {
        retentionDays != savedRetentionDays
            || endpointMilliseconds != savedEndpointMilliseconds
            || contextLength != savedContextLength
            || selectedVoice != savedVoice
    }

    var activeTasks: [TaskItem] { tasks.filter { $0.state.isActive } }
    var completedTasks: [TaskItem] { Array(tasks.filter { !$0.state.isActive }.reversed()) }

    func start() {
        guard eventTask == nil else { return }
        productState = .starting
        connectionDetail = "Starting local services"
        eventTask = Task {
            do {
                let historyKey = try await waitForHistoryKey()
                self.historyKey = historyKey
                connectionDetail = "Starting local services"
                try await establishServices()
                await refreshStatus()
                await refreshSettings()
                await refreshTasks()
                await eventLoop()
            } catch {
                connected = false
                isRestarting = false
                connectionDetail = "Startup failed"
                phase = .error
                productState = .blocked
                errorMessage = error.localizedDescription
                eventTask = nil
            }
        }
    }

    private func establishServices() async throws {
        guard let historyKey else {
            throw NSError(domain: "MacBot", code: 7, userInfo: [NSLocalizedDescriptionKey: "The history key is unavailable"])
        }
        audio?.close()
        audio = nil
        client = nil
        listening = false
        let token = try await services.restart(historyKey: historyKey)
        let run = services.dataDirectory.appending(path: "run")
        let controlSocket = run.appending(path: "control.sock").path
        let audioSocket = run.appending(path: "audio.sock").path
        for _ in 0..<150 {
            if FileManager.default.fileExists(atPath: controlSocket),
               FileManager.default.fileExists(atPath: audioSocket) { break }
            try await Task.sleep(for: .milliseconds(100))
        }
        guard FileManager.default.fileExists(atPath: controlSocket),
              FileManager.default.fileExists(atPath: audioSocket) else {
            throw NSError(domain: "MacBot", code: 8, userInfo: [NSLocalizedDescriptionKey: "Local services did not create their private sockets"])
        }
        let native = NativeClient(socketPath: controlSocket, token: token)
        let audioController = AudioController()
        try audioController.connect(path: audioSocket, token: token)
        client = native
        audio = audioController
        connected = true
        connectionDetail = "Local and private"
        phase = .idle
        productState = .ready
        restartRequired = false
        isRestarting = false
        errorMessage = nil
    }

    private func waitForHistoryKey() async throws -> Data {
        while !Task.isCancelled {
            do {
                try KeychainStore.ensureHistoryKey()
                return try KeychainStore.historyKey()
            } catch where KeychainStore.isTemporarilyUnavailable(error) {
                connected = false
                phase = .starting
                productState = .starting
                connectionDetail = "Waiting for this Mac to wake"
                try await Task.sleep(for: .seconds(2))
            }
        }
        throw CancellationError()
    }

    func send() {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        let mode = composerMode
        guard !text.isEmpty, let client else {
            explainUnavailable("send a message")
            return
        }
        draft = ""
        Task {
            do {
                switch mode {
                case .conversation:
                    _ = try await client.request(JSONPayload([
                        "op": "chat", "message": text, "speak": speakTypedReplies,
                    ]))
                case .task:
                    let response = try await client.request(JSONPayload([
                        "op": "task_create", "message": text,
                    ])).value
                    if let payload = response["task"] as? [String: Any] {
                        upsertTask(payload, sequence: cursor + 1)
                    }
                    selectedPage = .tasks
                }
            } catch {
                draft = text
                show(error)
            }
        }
    }

    private func refreshTasks() async {
        guard let client else { return }
        do {
            let response = try await client.request(JSONPayload(["op": "task_list"])).value
            let snapshots = response["tasks"] as? [[String: Any]] ?? []
            let durableIDs = Set(snapshots.compactMap { $0["task_id"] as? String })
            tasks.removeAll { $0.source == "MacBot Task Engine" && !durableIDs.contains($0.id) }
            for (index, snapshot) in snapshots.reversed().enumerated() {
                upsertTask(snapshot, sequence: -(snapshots.count - index))
            }
        } catch {
            show(error)
        }
    }

    func toggleListening() {
        guard let client else {
            explainUnavailable("start hands-free conversation")
            return
        }
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
                productState = desired ? .listening : .ready
            } catch { show(error) }
        }
    }

    func toggleMuted() {
        guard listening else {
            errorMessage = "Start hands-free conversation before muting the microphone."
            return
        }
        muted.toggle()
        audio?.setMuted(muted)
        phase = muted ? .muted : (listening ? .listening : .idle)
    }

    func interrupt() {
        guard let client else {
            explainUnavailable("stop the current response")
            return
        }
        Task {
            do {
                _ = try await client.request(JSONPayload(["op": "interrupt"]))
                phase = listening ? .listening : .interrupted
                productState = listening ? .listening : .ready
            } catch { show(error) }
        }
    }

    func clearConversation() {
        guard let client else {
            explainUnavailable("clear the conversation")
            return
        }
        Task {
            do {
                _ = try await client.request(JSONPayload(["op": "clear"]))
                messages.removeAll()
                responseIndex.removeAll()
                heard = ""
            } catch { show(error) }
        }
    }

    func restartServices() {
        guard !isRestarting else { return }
        isRestarting = true
        connected = false
        listening = false
        muted = false
        productState = .starting
        phase = .starting
        connectionDetail = "Restarting local services"
        eventTask?.cancel()
        eventTask = nil
        audio?.close()
        audio = nil
        client = nil
        start()
    }

    func shutdown() {
        eventTask?.cancel()
        eventTask = nil
        audio?.close()
        audio = nil
        historyKey = nil
        services.stopSynchronously()
    }

    func refreshDocuments() {
        guard let client else {
            libraryState = .failed("MacBot must be ready before the library can load.")
            return
        }
        libraryState = .loading
        Task {
            do {
                let response = try await client.request(JSONPayload(["op": "documents"])).value
                documents = (response["documents"] as? [[String: Any]] ?? []).compactMap { item in
                    guard let id = item["id"] as? String, let title = item["title"] as? String else { return nil }
                    return DocumentItem(id: id, title: title, type: item["type"] as? String ?? "text", length: item["length"] as? Int ?? 0)
                }
                libraryState = .loaded
            } catch {
                libraryState = .failed(error.localizedDescription)
            }
        }
    }

    func importDocument(_ url: URL) {
        guard let client else {
            libraryState = .failed("MacBot must be ready before a document can be imported.")
            return
        }
        libraryState = .loading
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
            } catch {
                libraryState = .failed(error.localizedDescription)
            }
        }
    }

    func deleteDocument(_ id: String) {
        guard let client else {
            libraryState = .failed("MacBot must be ready before a document can be deleted.")
            return
        }
        libraryState = .loading
        Task {
            do {
                _ = try await client.request(JSONPayload(["op": "document_delete", "id": id]))
                refreshDocuments()
            } catch {
                libraryState = .failed(error.localizedDescription)
            }
        }
    }

    func searchDocuments(_ query: String) {
        let trimmed = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        guard let client else {
            searchState = .failed("MacBot must be ready before documents can be searched.")
            return
        }
        searchState = .searching
        documentResults.removeAll()
        Task {
            do {
                let response = try await client.request(JSONPayload(["op": "document_search", "query": trimmed])).value
                documentResults = (response["results"] as? [[String: Any]] ?? []).enumerated().compactMap { index, item in
                    guard let content = item["content"] as? String else { return nil }
                    let metadata = item["metadata"] as? [String: Any] ?? [:]
                    let title = metadata["title"] as? String ?? "Local document"
                    let chunk = (metadata["chunk"] as? Int).map { "Passage \($0 + 1)" } ?? "Local source"
                    return DocumentSearchResult(
                        id: "\(index)-\(title)", title: title, content: content, sourceDetail: chunk
                    )
                }
                searchState = .complete
            } catch {
                searchState = .failed(error.localizedDescription)
            }
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
            let observations = status["metrics"] as? [[String: Any]] ?? []
            let ttft = observations.compactMap { $0["ttft_ms"] as? Double }
            let firstAudio = observations.compactMap { $0["first_audio_scheduled_ms"] as? Double }
            let stt = observations.compactMap { $0["stt_ms"] as? Double }
            let interrupt = status["interruption_ms"] as? [Double] ?? []
            let turnQueue = status["turn_queue"] as? Int ?? 0
            let speechQueue = status["speech_queue"] as? Int ?? 0
            let partialQueue = status["partial_queue"] as? Int ?? 0
            let dropped = status["audio_dropped"] as? Int ?? 0
            let errors = status["errors"] as? Int ?? 0
            metrics = [
                .init(id: "ttft", label: "First text p50 / p95", value: latencyPair(ttft)),
                .init(id: "audio", label: "Speech end → audio queued p50 / p95", value: latencyPair(firstAudio)),
                .init(id: "stt", label: "Transcription p50 / p95", value: latencyPair(stt)),
                .init(id: "interrupt", label: "Playback stop ack p50 / p95", value: latencyPair(interrupt)),
                .init(id: "turn", label: "Turn queue", value: "\(turnQueue)"),
                .init(id: "speech", label: "Speech / interim queues", value: "\(speechQueue) / \(partialQueue)"),
                .init(id: "dropped", label: "Dropped frames", value: "\(dropped)"),
                .init(id: "errors", label: "Turn errors", value: "\(errors)"),
            ]
            if let supervisor = status["supervisor"] as? [String: Any],
               let services = supervisor["services"] as? [String: [String: Any]] {
                for (name, service) in services.sorted(by: { $0.key < $1.key }) {
                    let ready = service["ready"] as? Bool == true ? "Ready" : "Unavailable"
                    let rss = (service["rss_bytes"] as? Double).map { String(format: "%.2f GiB", $0 / 1_073_741_824) } ?? "—"
                    metrics.append(.init(id: "service-\(name)", label: name.capitalized, value: "\(ready) · \(rss)"))
                }
            }
        } catch { show(error) }
    }

    func refreshSettings() async {
        guard let client else { return }
        do {
            let response = try await client.request(JSONPayload(["op": "settings"])).value
            guard let settings = response["settings"] as? [String: Any] else { return }
            retentionDays = settings["retention_days"] as? Int ?? 30
            endpointMilliseconds = settings["endpoint_ms"] as? Int ?? 350
            contextLength = settings["context_length"] as? Int ?? 16_384
            selectedVoice = settings["tts_voice"] as? String ?? voiceName
            availableVoices = (settings["voices"] as? [[String: Any]] ?? []).compactMap { item in
                guard let id = item["id"] as? String else { return nil }
                return VoiceOption(id: id, installed: item["installed"] as? Bool == true)
            }
            savedRetentionDays = retentionDays
            savedEndpointMilliseconds = endpointMilliseconds
            savedContextLength = contextLength
            savedVoice = selectedVoice
            searchCredentialConfigured = KeychainStore.hasSearchCredential()
        } catch { show(error) }
    }

    func saveRuntimeSettings() {
        guard let client else {
            explainUnavailable("save settings")
            return
        }
        guard availableVoices.contains(where: { $0.id == selectedVoice && $0.installed }) else {
            errorMessage = "Select an installed voice before saving settings."
            return
        }
        Task {
            do {
                _ = try await client.request(JSONPayload([
                    "op": "update_settings",
                    "values": [
                        "retention_days": retentionDays,
                        "endpoint_ms": endpointMilliseconds,
                        "context_length": contextLength,
                        "tts_voice": selectedVoice,
                    ],
                ]))
                savedRetentionDays = retentionDays
                savedEndpointMilliseconds = endpointMilliseconds
                savedContextLength = contextLength
                savedVoice = selectedVoice
                restartRequired = true
            } catch { show(error) }
        }
    }

    func previewVoice() {
        guard let client else {
            explainUnavailable("preview the active voice")
            return
        }
        Task {
            do {
                _ = try await client.request(JSONPayload(["op": "preview_voice"]))
            } catch { show(error) }
        }
    }

    func saveSearchCredential() {
        do {
            let value = searchCredential.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !value.isEmpty else { throw NSError(domain: "MacBot", code: 5, userInfo: [NSLocalizedDescriptionKey: "Enter a Brave Search API key"]) }
            try KeychainStore.setSearchCredential(value)
            searchCredential = ""
            searchCredentialConfigured = true
        } catch { show(error) }
    }

    func deleteSearchCredential() {
        do {
            try KeychainStore.deleteSearchCredential()
            searchCredential = ""
            searchCredentialConfigured = false
        } catch { show(error) }
    }

    private func latencyPair(_ values: [Double]) -> String {
        guard !values.isEmpty else { return "No samples" }
        let sorted = values.sorted()
        func percentile(_ value: Double) -> Double {
            sorted[min(sorted.count - 1, Int(ceil(value * Double(sorted.count))) - 1)]
        }
        return String(format: "%.0f / %.0f ms", percentile(0.5), percentile(0.95))
    }

    private func eventLoop() async {
        var consecutiveFailures = 0
        while !Task.isCancelled {
            do {
                guard let native = client else {
                    throw NSError(domain: "MacBot", code: 9, userInfo: [NSLocalizedDescriptionKey: "The local event connection is unavailable"])
                }
                var request: [String: Any] = ["op": "events", "after": cursor]
                if let epoch { request["epoch"] = epoch }
                let response = try await native.request(JSONPayload(request)).value
                cursor = response["cursor"] as? Int ?? cursor
                epoch = response["epoch"] as? String
                if response["reset"] as? Bool == true {
                    messages.removeAll(); tasks.removeAll(); responseIndex.removeAll()
                    await refreshTasks()
                }
                for event in response["events"] as? [[String: Any]] ?? [] {
                    consume(event)
                }
                connected = true
                connectionDetail = "Local and private"
                consecutiveFailures = 0
                if phase == .error { phase = listening ? .listening : .idle }
                productState = listening ? .listening : (canInterrupt || hasExecutingTask ? .working : .ready)
                await refreshStatus()
            } catch {
                if !Task.isCancelled {
                    consecutiveFailures += 1
                    connected = false
                    productState = .reconnecting
                    connectionDetail = "Reconnecting to local services"
                    if consecutiveFailures >= 3 {
                        do {
                            try await establishServices()
                            await refreshTasks()
                            consecutiveFailures = 0
                        } catch {
                            phase = .error
                            productState = .blocked
                            errorMessage = error.localizedDescription
                            try? await Task.sleep(for: .seconds(2))
                        }
                    } else {
                        try? await Task.sleep(for: .seconds(1))
                    }
                }
            }
        }
    }

    private func consume(_ event: [String: Any]) {
        let kind = event["kind"] as? String ?? ""
        let state = event["state"] as? String ?? ""
        let sequence = event["seq"] as? Int ?? cursor
        let turnID = event["turn_id"] as? String ?? UUID().uuidString
        let data = event["data"] as? [String: Any] ?? [:]
        switch kind {
        case "transcription":
            let text = data["text"] as? String ?? ""
            heard = text
            if !text.isEmpty {
                if let index = messages.firstIndex(where: { $0.id == "user-\(turnID)" }) {
                    messages[index].text = text
                } else {
                    messages.append(.init(id: "user-\(turnID)", role: .user, text: text, sequence: sequence))
                }
            }
        case "user":
            let text = data["text"] as? String ?? ""
            if !text.isEmpty {
                if let index = messages.firstIndex(where: { $0.id == "user-\(turnID)" }) {
                    messages[index].text = text
                } else {
                    messages.append(.init(id: "user-\(turnID)", role: .user, text: text, sequence: sequence))
                }
            }
        case "delta":
            let text = data["text"] as? String ?? ""
            if let index = responseIndex[turnID] {
                messages[index].text += text
            } else {
                messages.append(.init(id: "assistant-\(turnID)", role: .assistant, text: text, sequence: sequence))
                responseIndex[turnID] = messages.count - 1
            }
        case "planning":
            phase = .planning
            productState = .working
        case "generating":
            phase = .thinking
            productState = .working
        case "action", "tool":
            phase = .acting
            productState = .working
            if let action = data["action"] as? [String: Any] {
                upsertAction(action, eventState: state.isEmpty ? "running" : state, sequence: sequence, turnID: turnID)
            }
        case "task":
            let payload = data["task"] as? [String: Any] ?? data
            var snapshot = payload
            if snapshot["state"] == nil { snapshot["state"] = state.isEmpty ? "running" : state }
            if snapshot["turn_id"] == nil { snapshot["turn_id"] = turnID }
            upsertTask(snapshot, sequence: sequence)
            productState = hasExecutingTask ? .working : (listening ? .listening : .ready)
        case "speaking":
            phase = .speaking
            productState = .working
        case "listening":
            listening = data["enabled"] as? Bool ?? listening
            phase = listening ? .listening : .idle
            productState = listening ? .listening : .ready
        case "tool_result":
            let tool = data["tool"] as? String ?? "Action"
            let actionID = data["action_id"] as? String ?? "\(turnID)-\(sequence)"
            let result = data["result"] as? [String: Any] ?? [:]
            let resultState = result["status"] as? String ?? state
            let terminal = ["denied", "failed", "partial", "interrupted"].contains(resultState)
                ? resultState : "completed"
            let item = TaskItem(
                id: actionID,
                title: tool,
                state: terminal,
                detail: Self.summarize(result),
                sequence: tasks.first(where: { $0.id == actionID })?.sequence ?? sequence,
                source: (result["source"] as? String) ?? (data["authorization"] as? String) ?? "Requested by you",
                turnID: turnID,
                availableCommands: commands(from: data)
            )
            if let index = tasks.firstIndex(where: { $0.id == actionID }) { tasks[index] = item }
            else { tasks.append(item) }
        default: break
        }
        if state == "completed" {
            phase = listening ? .listening : .idle
            productState = listening ? .listening : .ready
        }
        if state == "interrupted" {
            phase = .interrupted
            productState = listening ? .listening : .ready
        }
        if state == "failed" {
            phase = .error
            productState = connected ? (listening ? .listening : .ready) : .blocked
            errorMessage = data["message"] as? String ?? "The turn failed"
        }
    }

    private func upsertAction(
        _ action: [String: Any], eventState: String, sequence: Int, turnID: String
    ) {
        guard let id = (action["task_id"] as? String) ?? (action["action_id"] as? String),
              let name = (action["title"] as? String) ?? (action["name"] as? String) else { return }
        let detail = (action["detail"] as? String) ?? (action["source_span"] as? String) ?? "Requested action"
        let item = TaskItem(
            id: id,
            title: name,
            state: eventState,
            detail: detail,
            sequence: tasks.first(where: { $0.id == id })?.sequence ?? sequence,
            source: (action["source"] as? String) ?? (action["authorization"] as? String) ?? "Requested by you",
            turnID: turnID,
            availableCommands: commands(from: action)
        )
        if let index = tasks.firstIndex(where: { $0.id == id }) { tasks[index] = item }
        else { tasks.append(item) }
    }

    private func upsertTask(_ snapshot: [String: Any], sequence: Int) {
        guard let id = snapshot["task_id"] as? String else { return }
        let stateValue = snapshot["state"] as? String ?? "proposed"
        let taskState = TaskState(serviceValue: stateValue)
        let result = snapshot["result"] as? [String: Any] ?? [:]
        let detail = (snapshot["detail"] as? String)
            ?? (result["summary"] as? String)
            ?? (snapshot["error"] as? String)
            ?? taskState.title
        var availableCommands = commands(from: snapshot)
        if snapshot["commands"] == nil {
            availableCommands = canonicalCommands(for: taskState)
        }
        let item = TaskItem(
            id: id,
            title: (snapshot["title"] as? String) ?? (snapshot["original_text"] as? String) ?? "MacBot Task",
            state: stateValue,
            detail: detail,
            sequence: tasks.first(where: { $0.id == id })?.sequence ?? sequence,
            source: (snapshot["source"] as? String) ?? "MacBot Task Engine",
            turnID: snapshot["turn_id"] as? String,
            availableCommands: availableCommands
        )
        if let index = tasks.firstIndex(where: { $0.id == id }) { tasks[index] = item }
        else { tasks.append(item) }
    }

    private func canonicalCommands(for state: TaskState) -> Set<TaskCommand> {
        switch state {
        case .awaitingAuthorization: [.authorize, .deny]
        case .running: [.pause, .cancel]
        case .pauseRequested, .queued, .blocked: [.cancel]
        case .paused: [.resume, .cancel]
        default: []
        }
    }

    private func commands(from payload: [String: Any]) -> Set<TaskCommand> {
        Set((payload["commands"] as? [String] ?? []).compactMap(TaskCommand.init(rawValue:)))
    }

    func perform(_ command: TaskCommand, on task: TaskItem) {
        guard task.availableCommands.contains(command) else { return }
        guard let client else {
            explainUnavailable("\(command.label.lowercased()) this task")
            return
        }
        Task {
            do {
                var request: [String: Any] = [
                    "op": "task_command", "task_id": task.id, "command": command.rawValue,
                ]
                if let turnID = task.turnID { request["turn_id"] = turnID }
                let response = try await client.request(JSONPayload(request)).value
                if let snapshot = response["task"] as? [String: Any] {
                    upsertTask(snapshot, sequence: task.sequence)
                }
            } catch { show(error) }
        }
    }

    static func summarize(_ result: [String: Any]) -> String {
        for key in ["message", "summary", "answer", "datetime", "time", "weather", "application", "app", "opened_url", "url", "path"] {
            if let value = result[key] as? String, !value.isEmpty { return value }
        }
        if let reason = result["reason"] as? String { return reason }
        if let error = result["error"] as? String { return error }
        if let results = result["results"] as? [[String: Any]], !results.isEmpty {
            let titles = results.prefix(3).compactMap { $0["title"] as? String }
            if !titles.isEmpty { return titles.joined(separator: " · ") }
            return "Returned \(results.count) result\(results.count == 1 ? "" : "s")."
        }
        switch result["status"] as? String {
        case "denied": return "The requested action was denied."
        case "failed": return "The requested action failed."
        case "partial": return "The request completed only in part."
        default: return "Completed."
        }
    }

    private func show(_ error: Error) {
        errorMessage = error.localizedDescription
        phase = .error
        productState = connected ? (listening ? .listening : .ready) : .blocked
    }

    private func explainUnavailable(_ action: String) {
        errorMessage = "MacBot cannot \(action) while local services are unavailable. Retry the services or open Diagnostics for details."
    }
}
