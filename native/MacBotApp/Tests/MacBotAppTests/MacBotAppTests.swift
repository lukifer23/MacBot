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
        TaskItem(id: "action", title: "open_app", state: .completed, detail: "Notes", sequence: 5)
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
    #expect(TaskState(rawValue: "approval_required") == nil)
    #expect(TaskState(rawValue: "accepted") == nil)
    #expect(TaskState(rawValue: "unexpected") == nil)
    let task = TaskItem(
        id: "task-1", title: "Search documents", state: .running, detail: "Searching",
        sequence: 4, source: "explicit_request", turnID: "turn-1", availableCommands: [.cancel]
    )
    #expect(task.state == .running)
    #expect(task.availableCommands == [.cancel])
    #expect(task.turnID == "turn-1")
    #expect(TaskCommand.allCases == [.authorize, .deny, .pause, .resume, .cancel])
}

@Test
func packagedTaskProtocolV3HasExactCanonicalCoverage() throws {
    let contract = try TaskProtocolV3.load()
    #expect(TaskProtocolV3.version == 3)
    #expect(contract.protocolVersion == 3)
    #expect(Set(contract.taskStates) == Set(TaskState.allCases))
    #expect(Set(contract.stepStates) == Set(StepState.allCases))
    #expect(Set(contract.failureClasses) == Set(FailureClass.allCases))
    #expect(Set(contract.commands) == Set(TaskCommand.allCases))
}

@Test
func serviceCommandsRemainAuthoritative() throws {
    let contract = try TaskProtocolV3.load()
    #expect(contract.authorizedCommands(nil, for: .awaitingAuthorization).isEmpty)
    #expect(contract.authorizedCommands([], for: .running).isEmpty)
    #expect(contract.authorizedCommands(["deny"], for: .awaitingAuthorization) == [.deny])
    #expect(contract.authorizedCommands(["cancel"], for: .awaitingAuthorization).isEmpty)
    #expect(contract.authorizedCommands(["resume", "cancel"], for: .paused) == [.resume, .cancel])
}

@Test
func composerModesExplainTheirDifferentCommitments() {
    #expect(ComposerMode.conversation.actionLabel == "Send message")
    #expect(ComposerMode.task.actionLabel == "Create task")
    #expect(ComposerMode.task.guidance.contains("authorize"))
}

@MainActor @Test
func taskSnapshotPreservesAuthorizationPlanDependenciesAndProvenance() throws {
    let state = AppState()
    let task = try state.taskItem(from: [
        "task_id": "task-42",
        "turn_id": "turn-42",
        "title": "Research local notes",
        "state": "awaiting_authorization",
        "detail": "Review the exact plan before execution",
        "commands": ["authorize", "deny", "cancel"],
        "capability_manifest": [
            "tools": ["rag_search", "web_search"],
            "targets": [["query": "MacBot architecture"]],
            "data_scopes": ["local_documents", "external_network"],
            "maximum_steps": 8,
            "deadline_seconds": 300,
        ],
        "steps": [[
            "step_id": "step-1",
            "ordinal": 0,
            "capability": "rag_search",
            "arguments": ["query": "MacBot architecture"],
            "safety_class": "read_local",
            "state": "succeeded",
            "depends_on": [String](),
            "attempts": 1,
            "max_attempts": 2,
            "result": [
                "summary": "Found the architecture note",
                "source": "rag_search",
                "path": "/Documents/architecture.md",
            ],
        ]],
    ], sequence: 7)

    #expect(task.availableCommands == [.authorize, .deny])
    #expect(task.authority.tools == ["rag_search", "web_search"])
    #expect(task.authority.dataScopes == ["local_documents", "external_network"])
    #expect(task.authority.maximumSteps == 8)
    #expect(task.steps.count == 1)
    #expect(task.steps[0].arguments.contains("MacBot architecture"))
    #expect(task.steps[0].state == .succeeded)
    #expect(task.steps[0].provenance?.contains("/Documents/architecture.md") == true)
}

@MainActor @Test
func taskSnapshotRejectsUnknownProtocolState() {
    let state = AppState()
    #expect(throws: NativeClientError.self) {
        _ = try state.taskItem(from: [
            "task_id": "task-unknown",
            "state": "almost_done",
        ], sequence: 1)
    }
}
