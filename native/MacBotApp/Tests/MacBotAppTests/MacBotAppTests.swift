import Foundation
import Security
import Testing
@testable import MacBotApp

@Test
func nativeSessionTokenIsOwnerOnlyAndRotates() throws {
    let root = FileManager.default.temporaryDirectory
        .appending(path: "macbot-swift-tests-\(UUID().uuidString)", directoryHint: .isDirectory)
    defer { try? FileManager.default.removeItem(at: root) }
    let services = ServiceManager(dataDirectory: root, cliPath: "/usr/bin/true")

    let first = try services.prepareToken()
    let path = root.appending(path: "run/native-token")
    let mode = try FileManager.default.attributesOfItem(atPath: path.path)[.posixPermissions] as? NSNumber
    #expect(first.count == 64)
    #expect(mode?.intValue == 0o600)

    let second = try services.prepareToken()
    #expect(first != second)
    #expect(try String(contentsOf: path, encoding: .utf8) == second)
}

@Test
func darkWakeIsARecoverableKeychainState() {
    let darkWake = NSError(domain: NSOSStatusErrorDomain, code: Int(errSecInDarkWake))
    let missing = NSError(domain: NSOSStatusErrorDomain, code: Int(errSecItemNotFound))
    #expect(KeychainStore.isTemporarilyUnavailable(darkWake))
    #expect(!KeychainStore.isTemporarilyUnavailable(missing))
}

@MainActor @Test
func timelineUsesEventSequenceAcrossMessagesAndActions() {
    let state = AppState()
    state.messages = [
        ChatItem(id: "assistant", role: .assistant, text: "Done", sequence: 9),
        ChatItem(id: "user", role: .user, text: "Open Notes", sequence: 2),
    ]
    state.tasks = [
        TaskItem(id: "action", title: "open_app", state: "completed", detail: "Notes", sequence: 5)
    ]
    #expect(state.timeline.map(\.id) == ["message-user", "task-action", "message-assistant"])
}

@MainActor @Test
func toolResultsArePresentedWithoutRawDictionaryText() {
    #expect(
        AppState.summarize(["status": "completed", "datetime": "2026-08-29T05:45:00-05:00"])
            == "2026-08-29T05:45:00-05:00"
    )
    #expect(AppState.summarize(["status": "denied", "reason": "Not requested"]) == "Not requested")
}

@Test
func productStateControlsOperationalAvailability() {
    #expect(ProductState.ready.isOperational)
    #expect(ProductState.listening.isOperational)
    #expect(ProductState.working.isOperational)
    #expect(!ProductState.starting.isOperational)
    #expect(!ProductState.reconnecting.isOperational)
    #expect(!ProductState.blocked.isOperational)
}

@Test
func taskStatesAndCommandsAreExplicit() {
    #expect(TaskState(serviceValue: "approval_required") == .awaitingAuthorization)
    #expect(TaskState(serviceValue: "unexpected") == .failed)
    let task = TaskItem(
        id: "task-1", title: "Search documents", state: "running", detail: "Searching",
        sequence: 4, source: "explicit_request", turnID: "turn-1", availableCommands: [.cancel]
    )
    #expect(task.state == .running)
    #expect(task.availableCommands == [.cancel])
    #expect(task.turnID == "turn-1")
    #expect(TaskCommand.allCases == [.authorize, .deny, .pause, .resume, .cancel])
}

@Test
func composerModesExplainTheirDifferentCommitments() {
    #expect(ComposerMode.conversation.actionLabel == "Send message")
    #expect(ComposerMode.task.actionLabel == "Create task")
    #expect(ComposerMode.task.guidance.contains("authorize"))
}
