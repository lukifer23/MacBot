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
    var createdAt = Date()
}

struct TaskItem: Identifiable, Equatable {
    let id: String
    var title: String
    var state: String
    var detail: String
}

struct ServiceMetric: Identifiable {
    let id: String
    let label: String
    let value: String
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
