import Foundation

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

struct TaskItem: Identifiable, Equatable {
    let id: String
    var title: String
    var state: String
    var detail: String
    let sequence: Int
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
        case "kokoro-heart": "Heart · Kokoro fallback"
        case "kokoro-michael": "Michael · Kokoro fallback"
        case "lessac": "Lessac · Piper fallback"
        case "amy": "Amy · Piper fallback"
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
    case library = "Library"
    case diagnostics = "Diagnostics"
    case settings = "Settings"
    var id: String { rawValue }
    var symbol: String {
        switch self {
        case .conversation: "bubble.left.and.bubble.right"
        case .library: "books.vertical"
        case .diagnostics: "gauge.with.dots.needle.67percent"
        case .settings: "gearshape"
        }
    }
}
