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
    enum Role { case user, assistant, system }
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

enum TaskState: String, Equatable, CaseIterable {
    case proposed
    case awaitingAuthorization = "awaiting_authorization"
    case queued, running
    case pauseRequested = "pause_requested"
    case paused
    case cancelRequested = "cancel_requested"
    case blocked, completed, partial, failed, cancelled

    init(serviceValue: String) {
        switch serviceValue {
        case "accepted": self = .queued
        case "approval_required", "waiting": self = .awaitingAuthorization
        case "denied", "interrupted": self = .cancelled
        default: self = Self(rawValue: serviceValue) ?? .failed
        }
    }

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

enum TaskCommand: String, Hashable, CaseIterable {
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

struct TaskItem: Identifiable, Equatable {
    let id: String
    var title: String
    var state: TaskState
    var detail: String
    let sequence: Int
    var source: String
    var turnID: String?
    var availableCommands: Set<TaskCommand>

    init(
        id: String,
        title: String,
        state: String,
        detail: String,
        sequence: Int,
        source: String = "Requested by you",
        turnID: String? = nil,
        availableCommands: Set<TaskCommand> = []
    ) {
        self.id = id
        self.title = title
        self.state = TaskState(serviceValue: state)
        self.detail = detail
        self.sequence = sequence
        self.source = source
        self.turnID = turnID
        self.availableCommands = availableCommands
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
        case "qwen-aiden-1.7b": "Aiden · Qwen3-TTS 1.7B candidate"
        case "qwen-ryan-1.7b": "Ryan · Qwen3-TTS 1.7B candidate"
        case "qwen-aiden-0.6b": "Aiden · Qwen3-TTS 0.6B candidate"
        case "qwen-ryan-0.6b": "Ryan · Qwen3-TTS 0.6B candidate"
        case "kokoro-heart": "Heart · Kokoro"
        case "kokoro-michael": "Michael · Kokoro"
        case "lessac": "Lessac · Piper"
        case "amy": "Amy · Piper"
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
