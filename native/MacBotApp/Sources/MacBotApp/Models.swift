import Foundation

enum ProductState: String, Equatable {
    case starting, ready, listening, working, reconnecting, blocked

    var title: String {
        switch self {
        case .starting: "Starting MacBot"
        case .ready: "Ready"
        case .listening: "Listening"
        case .working: "Working"
        case .reconnecting: "Reconnecting"
        case .blocked: "Needs attention"
        }
    }

    var symbol: String {
        switch self {
        case .starting: "hourglass"
        case .ready: "checkmark.circle"
        case .listening: "waveform"
        case .working: "sparkles"
        case .reconnecting: "arrow.trianglehead.2.clockwise.rotate.90"
        case .blocked: "exclamationmark.triangle"
        }
    }

    var isOperational: Bool { [.ready, .listening, .working].contains(self) }
}

enum AssistantPhase: String, CaseIterable {
    case starting, idle, listening, transcribing, planning, thinking, acting, speaking, muted, interrupted, error

    var title: String {
        switch self {
        case .starting: "Starting"
        case .idle: "Ready"
        case .listening: "Listening"
        case .transcribing: "Transcribing"
        case .planning: "Understanding"
        case .thinking: "Thinking"
        case .acting: "Working"
        case .speaking: "Speaking"
        case .muted: "Muted"
        case .interrupted: "Interrupted"
        case .error: "Needs attention"
        }
    }

    var symbol: String {
        switch self {
        case .starting: "hourglass"
        case .idle: "sparkles"
        case .listening: "waveform"
        case .transcribing: "text.bubble"
        case .planning, .thinking: "brain.head.profile"
        case .acting: "gearshape.2"
        case .speaking: "speaker.wave.2"
        case .muted: "mic.slash"
        case .interrupted: "stop.circle"
        case .error: "exclamationmark.triangle"
        }
    }
}

struct ChatItem: Identifiable, Equatable {
    enum Role: String { case user, assistant, system }
    let id: String
    let role: Role
    var text: String
    let sequence: Int
    var createdAt = Date()
}

enum ComposerMode: String, CaseIterable, Identifiable {
    case conversation = "Conversation"
    case task = "Task"

    var id: String { rawValue }

    var placeholder: String {
        switch self {
        case .conversation: "Ask MacBot anything…"
        case .task: "Describe a bounded task for MacBot…"
        }
    }

    var actionLabel: String {
        switch self {
        case .conversation: "Send message"
        case .task: "Create task"
        }
    }

    var guidance: String {
        switch self {
        case .conversation: "Get an answer now. MacBot may use explicitly requested tools during this turn."
        case .task: "Create a durable plan, review its scope, then authorize or deny execution in Task Center."
        }
    }
}

enum TaskState: String, Equatable, CaseIterable, Decodable {
    case proposed
    case awaitingAuthorization = "awaiting_authorization"
    case queued, running
    case pauseRequested = "pause_requested"
    case paused
    case cancelRequested = "cancel_requested"
    case blocked, completed, partial, failed, cancelled

    var title: String {
        switch self {
        case .proposed: "Proposed"
        case .awaitingAuthorization: "Needs authorization"
        case .queued: "Queued"
        case .running: "Running"
        case .pauseRequested: "Pausing"
        case .paused: "Paused"
        case .cancelRequested: "Stopping"
        case .blocked: "Blocked"
        case .completed: "Completed"
        case .partial: "Partially completed"
        case .failed: "Failed"
        case .cancelled: "Stopped"
        }
    }

    var symbol: String {
        switch self {
        case .proposed: "doc.badge.clock"
        case .awaitingAuthorization: "hand.raised.circle.fill"
        case .queued, .running: "clock.arrow.circlepath"
        case .pauseRequested, .paused: "pause.circle.fill"
        case .cancelRequested, .cancelled: "stop.circle.fill"
        case .blocked: "exclamationmark.octagon.fill"
        case .completed: "checkmark.circle.fill"
        case .partial: "exclamationmark.circle.fill"
        case .failed: "xmark.circle.fill"
        }
    }

    var isActive: Bool {
        [.proposed, .awaitingAuthorization, .queued, .running, .pauseRequested, .paused,
         .cancelRequested, .blocked].contains(self)
    }
}

enum StepState: String, Equatable, CaseIterable, Decodable {
    case planned, authorized, running, succeeded, failed, blocked, skipped
    case unknownEffect = "unknown_effect"

    var title: String {
        switch self {
        case .planned: "Planned"
        case .authorized: "Authorized"
        case .running: "Running"
        case .succeeded: "Succeeded"
        case .failed: "Failed"
        case .blocked: "Blocked"
        case .skipped: "Skipped"
        case .unknownEffect: "Effect unknown"
        }
    }

    var symbol: String {
        switch self {
        case .planned: "circle.dotted"
        case .authorized: "checkmark.shield"
        case .running: "clock.arrow.circlepath"
        case .succeeded: "checkmark.circle.fill"
        case .failed: "xmark.circle.fill"
        case .blocked: "exclamationmark.octagon.fill"
        case .skipped: "forward.end.circle"
        case .unknownEffect: "questionmark.diamond.fill"
        }
    }
}

enum FailureClass: String, Equatable, CaseIterable, Decodable {
    case notConfigured = "not_configured"
    case invalidRequest = "invalid_request"
    case denied
    case transientRead = "transient_read"
    case permanent, cancelled, timeout
    case unknownEffect = "unknown_effect"
    case integrityFailure = "integrity_failure"
}

enum TaskCommand: String, Hashable, CaseIterable, Decodable {
    case authorize, deny, pause, resume, cancel

    var label: String {
        switch self {
        case .authorize: "Authorize"
        case .deny: "Deny"
        case .pause: "Pause"
        case .resume: "Resume"
        case .cancel: "Stop"
        }
    }

    var symbol: String {
        switch self {
        case .authorize: "checkmark.shield.fill"
        case .deny: "xmark.shield.fill"
        case .pause: "pause.fill"
        case .resume: "play.fill"
        case .cancel: "stop.fill"
        }
    }
}

struct TaskProtocolContract: Decodable, Equatable {
    let protocolVersion: Int
    let channels: [String]
    let operations: [String]
    let reconciliationFields: [String]
    let errorFields: [String]
    let taskStates: [TaskState]
    let stepStates: [StepState]
    let failureClasses: [FailureClass]
    let commands: [TaskCommand]
    let legalCommands: [String: [TaskCommand]]

    private enum CodingKeys: String, CodingKey {
        case protocolVersion = "protocol_version"
        case channels, operations
        case reconciliationFields = "reconciliation_fields"
        case errorFields = "error_fields"
        case taskStates = "task_states"
        case stepStates = "step_states"
        case failureClasses = "failure_classes"
        case commands
        case legalCommands = "legal_commands"
    }

    func validate() throws {
        guard protocolVersion == TaskProtocolV3.version else {
            throw TaskProtocolError.unsupportedVersion(protocolVersion)
        }
        guard Set(channels) == ["command", "event", "audio"],
              Set(reconciliationFields) == [
                  "protocol_version", "epoch", "cursor", "messages", "tasks", "active_turn",
              ],
              Set(errorFields) == ["code", "message", "retryable", "failure_class"],
              Set(operations).count == operations.count
        else {
            throw TaskProtocolError.invalidCoverage("protocol_v3")
        }
        try requireExactCoverage(taskStates, expected: TaskState.allCases, field: "task_states")
        try requireExactCoverage(stepStates, expected: StepState.allCases, field: "step_states")
        try requireExactCoverage(
            failureClasses, expected: FailureClass.allCases, field: "failure_classes")
        try requireExactCoverage(commands, expected: TaskCommand.allCases, field: "commands")
        guard Set(legalCommands.keys) == Set(TaskState.allCases.map(\.rawValue)) else {
            throw TaskProtocolError.invalidCoverage("legal_commands")
        }
        let declared = Set(commands)
        guard legalCommands.values.allSatisfy({ Set($0).count == $0.count && Set($0).isSubset(of: declared) }) else {
            throw TaskProtocolError.invalidCoverage("legal_commands values")
        }
    }

    func authorizedCommands(_ serviceCommands: [String]?, for state: TaskState) -> Set<TaskCommand> {
        guard let serviceCommands else { return [] }
        let provided = Set(serviceCommands.compactMap(TaskCommand.init(rawValue:)))
        let legal = Set(legalCommands[state.rawValue] ?? [])
        return provided.intersection(legal)
    }

    private func requireExactCoverage<Value: Hashable>(
        _ values: [Value], expected: [Value], field: String
    ) throws {
        guard values.count == Set(values).count, Set(values) == Set(expected) else {
            throw TaskProtocolError.invalidCoverage(field)
        }
    }
}

enum TaskProtocolError: LocalizedError, Equatable {
    case missingFixture
    case unsupportedVersion(Int)
    case invalidCoverage(String)

    var errorDescription: String? {
        switch self {
        case .missingFixture: "The packaged Task protocol contract is missing."
        case .unsupportedVersion(let version): "Unsupported Task protocol version \(version)."
        case .invalidCoverage(let field): "The Task protocol contract has invalid \(field) coverage."
        }
    }
}

enum TaskProtocolV3 {
    static let version = 3
    static let current: Result<TaskProtocolContract, Error> = Result { try load() }

    static func load(bundle: Bundle = .module) throws -> TaskProtocolContract {
        guard let url = bundle.url(forResource: "task_protocol_v3", withExtension: "json") else {
            throw TaskProtocolError.missingFixture
        }
        let contract = try JSONDecoder().decode(TaskProtocolContract.self, from: Data(contentsOf: url))
        try contract.validate()
        return contract
    }
}

struct TaskAuthority: Equatable {
    var tools: [String] = []
    var targets: [String] = []
    var dataScopes: [String] = []
    var maximumSteps: Int?
    var deadlineSeconds: Int?

    var isEmpty: Bool {
        tools.isEmpty && targets.isEmpty && dataScopes.isEmpty
            && maximumSteps == nil && deadlineSeconds == nil
    }
}

struct TaskStepItem: Identifiable, Equatable {
    let id: String
    let ordinal: Int
    let capability: String
    let arguments: String
    let safetyClass: String
    let state: StepState
    let dependsOn: [String]
    let attempts: Int
    let maxAttempts: Int
    let result: String?
    let error: String?
    let provenance: String?

    var title: String {
        capability.replacingOccurrences(of: "_", with: " ").capitalized
    }
}

struct TaskItem: Identifiable, Equatable {
    let id: String
    var title: String
    var state: TaskState
    var detail: String
    let sequence: Int
    var source: String
    var turnID: String?
    var availableCommands: Set<TaskCommand>
    var steps: [TaskStepItem]
    var authority: TaskAuthority

    init(
        id: String,
        title: String,
        state: TaskState,
        detail: String,
        sequence: Int,
        source: String = "Requested by you",
        turnID: String? = nil,
        availableCommands: Set<TaskCommand> = [],
        steps: [TaskStepItem] = [],
        authority: TaskAuthority = .init()
    ) {
        self.id = id
        self.title = title
        self.state = state
        self.detail = detail
        self.sequence = sequence
        self.source = source
        self.turnID = turnID
        self.availableCommands = availableCommands
        self.steps = steps
        self.authority = authority
    }
}

enum LibraryLoadState: Equatable {
    case idle, loading, loaded, failed(String)
}

enum SearchLoadState: Equatable {
    case idle, searching, complete, failed(String)
}

struct DocumentSearchResult: Identifiable, Equatable {
    let id: String
    let title: String
    let content: String
    let sourceDetail: String
}

enum TimelineItem: Identifiable {
    case message(ChatItem)
    case task(TaskItem)

    var id: String {
        switch self {
        case .message(let item): "message-" + item.id
        case .task(let item): "task-" + item.id
        }
    }

    var sequence: Int {
        switch self {
        case .message(let item): item.sequence
        case .task(let item): item.sequence
        }
    }
}

struct ServiceMetric: Identifiable {
    let id: String
    let label: String
    let value: String
}

struct VoiceOption: Identifiable, Equatable {
    let id: String
    let installed: Bool

    var label: String { Self.label(for: id) }

    static func label(for id: String) -> String {
        switch id {
        case "qwen-aiden-1.7b": "Aiden · Qwen3-TTS 1.7B"
        default: id
        }
    }
}

struct DocumentItem: Identifiable, Equatable {
    let id: String
    let title: String
    let type: String
    let length: Int
}

enum SidebarPage: String, CaseIterable, Identifiable {
    case conversation = "Conversation"
    case tasks = "Task Center"
    case library = "Library"
    case diagnostics = "Diagnostics"
    case settings = "Settings"
    var id: String { rawValue }
    var symbol: String {
        switch self {
        case .conversation: "bubble.left.and.bubble.right"
        case .tasks: "checklist"
        case .library: "books.vertical"
        case .diagnostics: "gauge.with.dots.needle.67percent"
        case .settings: "gearshape"
        }
    }
}
