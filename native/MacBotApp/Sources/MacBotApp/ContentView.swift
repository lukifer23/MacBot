import SwiftUI
import UniformTypeIdentifiers

struct ContentView: View {
    @EnvironmentObject private var state: AppState

    var body: some View {
        NavigationSplitView {
            List(SidebarPage.allCases, selection: $state.selectedPage) { page in
                Label(page.rawValue, systemImage: page.symbol).tag(page)
                    .accessibilityIdentifier("sidebar.\(page.id.lowercased().replacingOccurrences(of: " ", with: "-"))")
            }
            .navigationTitle("MacBot")
            .safeAreaInset(edge: .bottom) { productStatus.padding() }
        } detail: {
            switch state.selectedPage ?? .conversation {
            case .conversation: ConversationView()
            case .tasks: TaskCenterView()
            case .macActions: MacActionsView()
            case .library: LibraryView()
            case .diagnostics: DiagnosticsView()
            case .settings: SettingsView()
            }
        }
        .frame(minWidth: 860, minHeight: 620)
        .alert("MacBot needs attention", isPresented: Binding(
            get: { state.errorMessage != nil },
            set: { if !$0 { state.errorMessage = nil } }
        )) {
            if !state.connected {
                Button("Retry services") { state.errorMessage = nil; state.restartServices() }
            }
            Button("Open Diagnostics") { state.errorMessage = nil; state.selectedPage = .diagnostics }
            Button("Dismiss", role: .cancel) { state.errorMessage = nil }
        } message: { Text(state.errorMessage ?? "Unknown error") }
    }

    private var productStatus: some View {
        HStack(spacing: 8) {
            Image(systemName: state.productState.symbol)
                .foregroundStyle(state.productState == .blocked ? .red : .secondary)
            VStack(alignment: .leading, spacing: 2) {
                Text(state.productState.title).font(.caption).fontWeight(.medium)
                Text(state.connectionDetail).font(.caption2).foregroundStyle(.secondary).lineLimit(2)
            }
            Spacer()
        }
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("product-status")
    }
}

private struct ConversationView: View {
    @EnvironmentObject private var state: AppState
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @FocusState private var composerFocused: Bool
    @AppStorage("conversation.followLatest") private var followLatest = true

    var body: some View {
        let timeline = state.timeline
        VStack(spacing: 0) {
            statusHeader
            Divider()
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(spacing: 14) {
                        if timeline.isEmpty {
                            ContentUnavailableView(
                                state.canConverse ? "Ready when you are" : state.productState.title,
                                systemImage: state.productState.symbol,
                                description: Text(state.canConverse
                                    ? "Type a message, plan a research task, or start hands-free."
                                    : state.connectionDetail)
                            ).padding(.top, 90)
                        }
                        ForEach(timeline) { item in
                            switch item {
                            case .message(let message): MessageBubble(item: message)
                            case .task(let task): TaskResultCard(item: task, compact: true)
                            }
                        }
                    }.padding(24)
                }
                .onChange(of: timeline.last?.id, initial: true) {
                    if followLatest, let id = timeline.last?.id {
                        withAnimation(reduceMotion ? nil : .easeOut(duration: 0.2)) {
                            proxy.scrollTo(id, anchor: .bottom)
                        }
                    }
                }
                .onChange(of: followLatest) {
                    if followLatest, let id = timeline.last?.id {
                        proxy.scrollTo(id, anchor: .bottom)
                    }
                }
                .onChange(of: state.messages.last?.text) {
                    if followLatest, let id = timeline.last?.id {
                        proxy.scrollTo(id, anchor: .bottom)
                    }
                }
            }
            Divider()
            if state.transcribingFile {
                ProgressView("Transcribing audio file…").controlSize(.small).padding(8)
            }
            if state.isDictating || !state.dictationText.isEmpty { dictationPreview }
            if state.macActionBusy || state.macActionTarget != nil || state.composerMode == .macAction {
                MacActionReview()
                    .frame(maxWidth: 825)
                    .padding(.horizontal, 18)
            }
            composer
        }
        .navigationTitle("Conversation")
        .toolbar {
            ToolbarItemGroup {
                Toggle("Follow latest", systemImage: "arrow.down.to.line", isOn: $followLatest)
                    .toggleStyle(.button)
                    .help("Keep the latest conversation entry in view")
                Button("Focus composer", systemImage: "square.and.pencil") { composerFocused = true }
                    .disabled(!state.canConverse)
                    .keyboardShortcut("l", modifiers: .command)
                    .help("Focus the message or task composer")
                Button("Clear conversation", systemImage: "trash") { state.confirmClearConversation = true }
                    .disabled(state.messages.isEmpty || !state.canConverse || state.isClearingConversation)
                    .help("Delete this local conversation")
                    .accessibilityIdentifier("conversation.clear")
            }
        }
        .confirmationDialog(
            "Delete this conversation?", isPresented: $state.confirmClearConversation, titleVisibility: .visible
        ) {
            Button("Delete conversation", role: .destructive, action: state.clearConversation)
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Messages in this local conversation will be permanently removed. Durable Task Center records remain available.")
        }
    }

    private var statusHeader: some View {
        HStack(spacing: 14) {
            Image(systemName: state.productState.isOperational ? state.phase.symbol : state.productState.symbol)
                .font(.title2)
                .symbolEffect(.pulse, isActive: !reduceMotion && [.listening, .gatheringContext, .thinking, .acting, .speaking].contains(state.phase))
                .frame(width: 40, height: 40).background(.quaternary, in: Circle())
            VStack(alignment: .leading, spacing: 3) {
                Text(state.productState.isOperational ? state.phase.title : state.productState.title).font(.headline)
                Text(state.partialTranscript.isEmpty
                    ? (state.heard.isEmpty ? state.connectionDetail : "Heard: \(state.heard)")
                    : "Live: \(state.partialTranscript)")
                    .font(.subheadline).foregroundStyle(.secondary).lineLimit(2)
            }
            Spacer()
        }.padding(.horizontal, 20).padding(.vertical, 10).background(.bar)
    }

    private var inputControls: some View {
        HStack {
            Menu {
                Button(action: state.toggleListening) {
                    Label(state.listening && !state.isDictating ? "Stop hands-free" : "Start hands-free", systemImage: "waveform")
                }
                .disabled(!state.canListen || state.isChangingListening || state.isDictating)
                Button(state.isDictating ? "Stop dictation" : "Dictate", systemImage: "text.bubble", action: state.toggleDictation)
                    .disabled(!state.canListen || state.isChangingListening || (state.listening && !state.isDictating))
                Button(state.muted ? "Unmute" : "Mute", systemImage: state.muted ? "mic.slash.fill" : "mic.fill", action: state.toggleMuted)
                    .disabled(!state.listening || state.isChangingListening)
                Button("Copy latest transcript", systemImage: "document.on.document", action: state.copyTranscript)
                    .disabled(state.heard.isEmpty)
            } label: {
                Label(state.listening ? (state.muted ? "Muted" : "Microphone on") : "Microphone", systemImage: state.muted ? "mic.slash" : "mic")
            }
            Button("Audio file…", systemImage: "paperclip", action: state.chooseAudioToTranscribe)
                .disabled(!state.connected || state.transcribingFile)
            Spacer()
            if state.composerMode == .conversation {
                Toggle("Speak replies", isOn: $state.speakTypedReplies).toggleStyle(.checkbox)
                    .disabled(!state.canConverse)
            }
            Button("Stop", systemImage: "stop.fill") {
                if state.canInterrupt {
                    state.interrupt()
                } else if state.isDictating {
                    state.toggleDictation()
                } else if state.listening {
                    state.toggleListening()
                }
            }
            .disabled(!(state.canInterrupt || state.listening) || state.isInterrupting || state.isChangingListening)
            .help("Stop the response or turn off the microphone")
            .accessibilityIdentifier("conversation.stop")
        }
        .font(.callout)
    }

    private var dictationPreview: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("Dictation draft").font(.headline)
                Spacer()
                Button("Copy", action: state.copyDictation).disabled(state.dictationText.isEmpty)
                Button("Clear") { state.dictationText = "" }.disabled(state.dictationText.isEmpty)
                Button("Use in composer") {
                    state.useDictationInComposer()
                    composerFocused = true
                }
                .disabled(state.isDictating || state.dictationText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
            TextEditor(text: $state.dictationText)
                .frame(height: 78)
                .accessibilityLabel("Dictation draft")
                .accessibilityIdentifier("dictation.draft")
        }
        .padding(.horizontal, 24)
        .padding(.top, 12)
    }

    private var composer: some View {
        VStack(alignment: .leading, spacing: 8) {
            Picker("Composer mode", selection: $state.composerMode) {
                ForEach(ComposerMode.allCases) { mode in Text(mode.rawValue).tag(mode) }
            }
            .pickerStyle(.segmented)
            .fixedSize(horizontal: true, vertical: false)
            .disabled(state.isSending)
            .accessibilityHint("Choose an immediate conversation or a durable task that requires authorization")
            .accessibilityIdentifier("composer.mode")
            Text(state.composerMode.guidance).font(.caption).foregroundStyle(.secondary)
            if state.isSending {
                ProgressView(state.composerMode == .task ? "Submitting task…" : "Sending…")
                    .controlSize(.small)
                    .accessibilityIdentifier("composer.progress")
            }
            HStack(alignment: .bottom, spacing: 12) {
                TextField(state.composerMode.placeholder, text: $state.draft, axis: .vertical)
                    .textFieldStyle(.plain).lineLimit(1...5).focused($composerFocused)
                    .disabled(!state.canConverse)
                    .onSubmit { if !NSEvent.modifierFlags.contains(.shift) { state.send() } }
                    .accessibilityIdentifier("composer.input")
                Button(action: state.send) {
                    Label(state.composerMode.actionLabel, systemImage: state.composerMode.symbol)
                        .fontWeight(.semibold)
                }
                .buttonStyle(.borderedProminent)
                .disabled(state.draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                    || !state.canConverse || state.isSending
                    || (state.composerMode == .macAction && (state.macActionBusy || state.draft.count > 400)))
                .keyboardShortcut(.return, modifiers: .command)
                .accessibilityIdentifier("composer.send")
            }
            inputControls
        }
        .padding(14)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 14))
        .overlay { RoundedRectangle(cornerRadius: 14).strokeBorder(.quaternary) }
        .frame(maxWidth: 860)
        .padding(18)
    }
}

private struct MessageBubble: View {
    let item: ChatItem

    private var author: String {
        switch item.role {
        case .user: "You"
        case .assistant: "MacBot"
        case .system: "System"
        }
    }

    private var symbol: String {
        switch item.role {
        case .user: "person.crop.circle"
        case .assistant: "waveform.circle"
        case .system: "info.circle"
        }
    }

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: symbol)
                .font(.title2)
                .foregroundStyle(item.role == .assistant ? Color.accentColor : .secondary)
                .frame(width: 28)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 5) {
                Text(author).font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                Text(item.text).textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 16).padding(.vertical, 12)
        .background(
            item.role == .user ? Color.accentColor.opacity(0.09) : Color(nsColor: .controlBackgroundColor),
            in: RoundedRectangle(cornerRadius: 12)
        )
        .frame(maxWidth: 860, alignment: .leading)
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("message.\(item.id)")
    }
}

private struct TaskResultCard: View {
    @EnvironmentObject private var state: AppState
    let item: TaskItem
    var compact = false

    var body: some View {
        Group {
            if item.canEditRequest {
                VStack(alignment: .leading, spacing: 8) {
                    heading
                    Text(item.detail).textSelection(.enabled)
                    Button("Edit request", systemImage: "square.and.pencil") { state.editResearchRequest(item) }
                        .disabled(!state.canRestoreResearchDraft)
                        .accessibilityIdentifier("task.\(item.id).edit-request")
                }
            } else if item.state == .awaitingAuthorization {
                VStack(alignment: .leading, spacing: 0) {
                    heading
                    details
                }
            } else {
                DisclosureGroup {
                    details
                } label: {
                    heading
                }
            }
        }
        .padding(14).background(.thinMaterial, in: RoundedRectangle(cornerRadius: 14))
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("task.\(item.id)")
    }

    private var heading: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: item.state.symbol).foregroundStyle(tint)
            VStack(alignment: .leading, spacing: 4) {
                Text(item.title).font(.headline)
                Text(item.state.title).font(.caption).foregroundStyle(.secondary)
                if compact && item.state != .awaitingAuthorization && !item.canEditRequest {
                    Text(item.detail).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                }
            }
            Spacer()
        }
    }

    private var details: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(item.detail).textSelection(.enabled)
            if !item.sources.isEmpty {
                GroupBox("Recorded sources") {
                    VStack(alignment: .leading, spacing: 12) {
                        ForEach(item.sources) { source in
                            VStack(alignment: .leading, spacing: 4) {
                                if let url = source.url {
                                    Link("[\(source.id)] \(source.title)", destination: url)
                                } else {
                                    Text("[\(source.id)] \(source.title)").fontWeight(.medium)
                                }
                                if !source.excerpt.isEmpty {
                                    Text(source.excerpt)
                                        .font(.callout)
                                        .foregroundStyle(.secondary)
                                        .textSelection(.enabled)
                                }
                            }
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            if !item.authority.isEmpty { authority }
            if !item.steps.isEmpty { steps }
            if !item.availableCommands.isEmpty {
                HStack {
                    ForEach(TaskCommand.allCases.filter(item.availableCommands.contains), id: \.self) { command in
                        if command == .cancel || command == .deny {
                            taskButton(command).buttonStyle(.bordered)
                        } else {
                            taskButton(command).buttonStyle(.borderedProminent)
                        }
                    }
                }
            }
        }
        .padding(.top, 8)
    }

    private func dependencyLabels(_ step: TaskStepItem) -> [String] {
        step.dependsOn.map { dependency in
            if let previous = item.steps.first(where: { $0.id == dependency }) {
                return "Step \(previous.ordinal + 1)"
            }
            return dependency
        }
    }

    private func taskButton(_ command: TaskCommand) -> some View {
        Button(command.label, systemImage: command.symbol) { state.perform(command, on: item) }
            .disabled(state.taskCommandsInFlight.contains(item.id))
            .accessibilityIdentifier("task.\(item.id).\(command.rawValue)")
    }

    private var authority: some View {
        GroupBox("Authorization scope") {
            VStack(alignment: .leading, spacing: 7) {
                if !item.authority.tools.isEmpty {
                    LabeledContent("Capabilities", value: item.authority.tools.map(display).joined(separator: ", "))
                }
                if !item.authority.dataScopes.isEmpty {
                    LabeledContent("Data", value: item.authority.dataScopes.map(display).joined(separator: ", "))
                }
                if let maximum = item.authority.maximumSteps {
                    LabeledContent("Maximum steps", value: maximum.formatted())
                }
                if let seconds = item.authority.deadlineSeconds {
                    LabeledContent("Deadline", value: "\(seconds) seconds")
                }
                if !item.authority.targets.isEmpty {
                    Text("Exact targets").font(.caption).foregroundStyle(.secondary)
                    ForEach(Array(item.authority.targets.enumerated()), id: \.offset) { _, target in
                        Text(target).font(.caption.monospaced()).textSelection(.enabled)
                    }
                }
            }.frame(maxWidth: .infinity, alignment: .leading)
        }
        .accessibilityIdentifier("task.\(item.id).authority")
    }

    private var steps: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Plan and progress").font(.headline)
            ForEach(item.steps) { step in
                HStack(alignment: .top, spacing: 10) {
                    Image(systemName: step.state.symbol).foregroundStyle(stepTint(step.state))
                    VStack(alignment: .leading, spacing: 4) {
                        HStack {
                            Text("\(step.ordinal + 1). \(step.title)").fontWeight(.medium)
                            Spacer()
                            Text(step.state.title).font(.caption).foregroundStyle(.secondary)
                        }
                        Text(step.arguments).font(.caption.monospaced()).textSelection(.enabled)
                        Text("Authority: \(display(step.safetyClass))")
                            .font(.caption).foregroundStyle(.secondary)
                        if !step.dependsOn.isEmpty {
                            Text("Waits for evidence from \(dependencyLabels(step).joined(separator: ", "))")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        if step.attempts > 0 {
                            Text("Attempts: \(step.attempts) of \(step.maxAttempts)")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        if let result = step.result { Text(result).textSelection(.enabled) }
                        if let provenance = step.provenance {
                            Label(provenance, systemImage: "link").font(.caption).textSelection(.enabled)
                        }
                        if let error = step.error {
                            Label(error, systemImage: "exclamationmark.triangle.fill").foregroundStyle(.red)
                        }
                    }
                }
                .padding(10).background(.quaternary, in: RoundedRectangle(cornerRadius: 10))
                .accessibilityElement(children: .combine)
                .accessibilityIdentifier("task.\(item.id).step.\(step.id)")
            }
        }
    }

    private func display(_ value: String) -> String {
        value.replacingOccurrences(of: "_", with: " ").capitalized
    }

    private func stepTint(_ state: StepState) -> Color {
        switch state {
        case .succeeded: .green
        case .failed, .unknownEffect: .red
        case .blocked: .orange
        case .running, .authorized: .blue
        case .planned, .skipped: .secondary
        }
    }

    private var tint: Color {
        switch item.state {
        case .proposed, .queued, .running: .blue
        case .awaitingAuthorization, .pauseRequested, .paused, .cancelRequested, .blocked, .partial: .orange
        case .failed: .red
        case .cancelled: .secondary
        case .completed: .green
        }
    }
}

private struct TaskCenterView: View {
    @EnvironmentObject private var state: AppState

    var body: some View {
        Group {
            if state.tasks.isEmpty {
                ContentUnavailableView(
                    "No tasks yet", systemImage: "checklist",
                    description: Text("Requested actions will show their progress, result, and source here.")
                )
            } else {
                List {
                    if !state.tasksNeedingApproval.isEmpty {
                        Section("Needs your approval") {
                            ForEach(state.tasksNeedingApproval) { TaskResultCard(item: $0) }
                        }
                    }
                    if !state.activeTasks.isEmpty {
                        Section("In progress") { ForEach(state.activeTasks) { TaskResultCard(item: $0) } }
                    }
                    if !state.completedTasks.isEmpty {
                        Section("Recent") { ForEach(state.completedTasks) { TaskResultCard(item: $0) } }
                    }
                }
            }
        }
        .navigationTitle("Task Center")
        .accessibilityIdentifier("task-center")
    }
}

private struct LibraryView: View {
    @EnvironmentObject private var state: AppState

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                TextField("Search your documents…", text: $state.documentQuery)
                    .textFieldStyle(.roundedBorder)
                    .disabled(!state.canManageLibrary)
                    .onSubmit { state.searchDocuments(state.documentQuery) }
                Button("Search") { state.searchDocuments(state.documentQuery) }
                    .disabled(state.documentQuery.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !state.canManageLibrary || state.searchState == .searching)
                Button("Import", systemImage: "plus") { state.importingDocuments = true }
                    .buttonStyle(.borderedProminent).disabled(!state.canManageLibrary)
            }.padding()
            Divider()
            libraryContent
        }
        .navigationTitle("Library")
        .onAppear { if state.libraryState == .idle { state.refreshDocuments() } }
        .fileImporter(isPresented: $state.importingDocuments, allowedContentTypes: [.plainText, .pdf, .init(filenameExtension: "docx")!], allowsMultipleSelection: false) { result in
            if case .success(let urls) = result, let url = urls.first { state.importDocument(url) }
        }
        .confirmationDialog(
            "Delete \(state.pendingDocumentDeletion?.title ?? "this document")?",
            isPresented: Binding(get: { state.pendingDocumentDeletion != nil }, set: { if !$0 { state.pendingDocumentDeletion = nil } }),
            titleVisibility: .visible
        ) {
            Button("Delete document", role: .destructive) {
                if let document = state.pendingDocumentDeletion { state.deleteDocument(document.id) }
                state.pendingDocumentDeletion = nil
            }
            Button("Cancel", role: .cancel) { state.pendingDocumentDeletion = nil }
        } message: { Text("The local source and its searchable index entries will be removed.") }
    }

    @ViewBuilder private var libraryContent: some View {
        switch state.libraryState {
        case .idle, .loading:
            ProgressView("Loading your local library…").frame(maxWidth: .infinity, maxHeight: .infinity)
        case .failed(let message):
            ContentUnavailableView {
                Label("Library unavailable", systemImage: "exclamationmark.triangle")
            } description: { Text(message) } actions: {
                Button("Try again", action: state.refreshDocuments).disabled(!state.canManageLibrary)
            }
        case .loaded:
            if state.documents.isEmpty {
                ContentUnavailableView(
                    "No documents", systemImage: "books.vertical",
                    description: Text("Import a TXT, PDF, or DOCX file to search it locally.")
                )
            } else {
                List {
                    Section("Documents") {
                        ForEach(state.documents) { document in
                            HStack {
                                Image(systemName: "doc.text")
                                VStack(alignment: .leading) {
                                    Text(document.title).font(.headline)
                                    Text("\(document.type.uppercased()) · \(document.length.formatted()) characters")
                                        .font(.caption).foregroundStyle(.secondary)
                                }
                                Spacer()
                                Button("Delete \(document.title)", systemImage: "trash", role: .destructive) {
                                    state.pendingDocumentDeletion = document
                                }.labelStyle(.iconOnly)
                            }.padding(.vertical, 4)
                        }
                    }
                    searchResults
                }
            }
        }
    }

    @ViewBuilder private var searchResults: some View {
        switch state.searchState {
        case .idle: EmptyView()
        case .searching: Section("Search results") { ProgressView("Searching locally…") }
        case .failed(let message): Section("Search results") { Label(message, systemImage: "exclamationmark.triangle").foregroundStyle(.red) }
        case .complete:
            Section("Search results") {
                if state.documentResults.isEmpty {
                    ContentUnavailableView("No matching passages", systemImage: "magnifyingglass", description: Text("Try a different phrase or a more specific term."))
                } else {
                    ForEach(state.documentResults) { result in
                        VStack(alignment: .leading, spacing: 5) {
                            Text(result.title).font(.headline)
                            Text(result.content).lineLimit(6).textSelection(.enabled)
                            Text(result.sourceDetail).font(.caption).foregroundStyle(.secondary)
                        }.padding(.vertical, 4)
                    }
                }
            }
        }
    }
}

private struct MacActionReview: View {
    @EnvironmentObject private var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if state.macActionBusy { ProgressView("Working on Mac action…").controlSize(.small) }
            if let target = state.macActionTarget {
                GroupBox {
                    VStack(alignment: .leading, spacing: 8) {
                        LabeledContent("Bundle", value: target.bundleID)
                        Text(target.url.path).font(.caption.monospaced()).textSelection(.enabled)
                        HStack {
                            Button("Open app", action: state.approveMacAction).buttonStyle(.borderedProminent)
                            Button("Dismiss", action: state.discardMacAction)
                        }
                    }.frame(maxWidth: .infinity, alignment: .leading)
                } label: {
                    Label("Open \(target.name)?", systemImage: "macwindow")
                }
            }
            if let receipt = state.macActionStatus { Text(receipt).textSelection(.enabled) }
        }
    }
}

private struct MacActionsView: View {
    @EnvironmentObject private var state: AppState

    var body: some View {
        Form {
            Section("Ask MacBot to open an app") {
                TextField("Open an installed app", text: $state.macActionRequest, prompt: Text("Open Calendar"))
                    .onSubmit(state.planMacAction)
                Button("Find app", systemImage: "sparkle.magnifyingglass", action: state.planMacAction)
                    .disabled(!state.connected || state.macActionBusy || state.macActionRequest.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                MacActionReview()
            }
            Section("Applications") {
                LabeledContent {
                    Button("Choose an app…", systemImage: "macwindow", action: state.chooseApplication)
                } label: {
                    Text("Open an installed Mac app")
                }
            }
            Section("Finder") {
                LabeledContent {
                    Button("Choose an item…", systemImage: "doc", action: state.chooseItemToReveal)
                } label: {
                    Text("Reveal a local file or folder")
                }
                LabeledContent {
                    Button("Show in Finder", systemImage: "folder", action: state.revealDataFolder)
                } label: {
                    Text("MacBot data folder")
                }
            }
        }
        .formStyle(.grouped)
        .navigationTitle("Mac Actions")
    }
}

private struct DiagnosticsView: View {
    @EnvironmentObject private var state: AppState
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                HStack {
                    Label(state.productState.title, systemImage: state.productState.symbol).font(.title2).fontWeight(.semibold)
                    Spacer()
                    if !state.connected { Button("Retry services", action: state.restartServices).disabled(state.isRestarting) }
                }
                Text(state.connectionDetail).foregroundStyle(.secondary)
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 180))], spacing: 14) {
                    ForEach(state.metrics) { metric in
                        VStack(alignment: .leading, spacing: 8) {
                            Text(metric.label).foregroundStyle(.secondary)
                            Text(metric.value).font(.system(.title, design: .rounded, weight: .semibold))
                        }.frame(maxWidth: .infinity, alignment: .leading).padding(18)
                            .background(.quaternary, in: RoundedRectangle(cornerRadius: 14))
                    }
                }
            }.padding(24)
        }.navigationTitle("Diagnostics").toolbar {
            Button("Refresh", systemImage: "arrow.clockwise") { Task { await state.refreshStatus() } }
                .disabled(!state.connected)
        }
    }
}

private struct SettingsView: View {
    @EnvironmentObject private var state: AppState

    var body: some View {
        Form {
            Section("Models") {
                LabeledContent("Language model", value: state.modelName)
                LabeledContent("Active profile", value: state.activeModelProfile)
                if state.modelProfile != state.activeModelProfile {
                    LabeledContent("After restart", value: state.modelProfile)
                }
                if !state.modelProfiles.isEmpty {
                    Picker("Model profile", selection: $state.candidateProfile) {
                        ForEach(state.modelProfiles) { profile in
                            Text(profile.id + (profile.installed ? "" : " · not provisioned"))
                                .tag(profile.id)
                        }
                    }.disabled(!state.canChangeSettings || state.isSavingSettings)
                    if let profile = state.modelProfiles.first(where: { $0.id == state.candidateProfile }) {
                        LabeledContent("Model and runtime", value: "\(profile.model) · \(profile.runtime)")
                        LabeledContent("Language model files", value: ByteCountFormatter.string(fromByteCount: profile.modelBytes, countStyle: .file))
                        if !profile.installed {
                            Text("Provision the model and runtime before choosing this profile.").font(.callout)
                        }
                        Button("Save profile for restart", action: state.saveModelProfile)
                            .disabled(!profile.installed || profile.id == state.modelProfile
                                || state.settingsHaveChanges || !state.canChangeSettings || state.isSavingSettings)
                    }
                }
                Picker("Voice", selection: $state.selectedVoice) {
                    ForEach(state.availableVoices) { voice in
                        Text(voice.label + (voice.installed ? "" : " · not installed"))
                            .tag(voice.id).disabled(!voice.installed)
                    }
                }.disabled(!state.canChangeSettings)
                LabeledContent("Active voice", value: VoiceOption.label(for: state.voiceName))
                Button("Preview active voice", systemImage: "speaker.wave.2", action: state.previewVoice)
                    .disabled(!state.canChangeSettings || state.isPreviewingVoice)
                    .accessibilityIdentifier("settings.preview-voice")
                Text("Only installed voices can be selected. A saved voice becomes active after local services restart.")
                    .foregroundStyle(.secondary)
            }
            Section("Memory and privacy") {
                Toggle("Use conversation summaries as memory", isOn: $state.semanticMemoryEnabled)
                    .disabled(!state.canChangeSettings)
                Button("Review saved summaries…", action: state.reviewMemories)
                    .disabled(!state.connected || !state.historyAvailable || state.memoryBusy)
                LabeledContent("Conversation history", value: state.historyAvailable ? "Encrypted on this Mac" : "Unavailable")
                Text("Conversation content is retained for the selected period. Raw microphone audio is never retained.")
                    .foregroundStyle(.secondary)
                Stepper("Retention: \(state.retentionDays) days", value: $state.retentionDays, in: 1...3650)
                    .disabled(!state.canChangeSettings)
            }
            Section("Web search") {
                LabeledContent("Brave Search", value: state.searchCredentialConfigured ? "Configured in Keychain" : "Not configured")
                SecureField("Brave Search API key", text: $state.searchCredential)
                HStack {
                    Button("Save to Keychain", action: state.saveSearchCredential)
                        .disabled(state.searchCredential.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    if state.searchCredentialConfigured {
                        Button("Remove credential", role: .destructive) { state.confirmCredentialRemoval = true }
                    }
                }
                Text("Web search remains unavailable until a supported provider is explicitly configured.")
                    .foregroundStyle(.secondary)
            }
            Section("Conversation") {
                Toggle("Live transcript preview", isOn: $state.liveTranscriptionPreview)
                    .disabled(!state.canChangeSettings)
                Stepper("Endpoint silence: \(state.endpointMilliseconds) ms", value: $state.endpointMilliseconds, in: 150...2000, step: 25)
                    .disabled(!state.canChangeSettings)
                Picker("Context target", selection: $state.contextLength) {
                    Text("8K").tag(8192); Text("16K").tag(16_384); Text("32K").tag(32_768)
                }.disabled(!state.canChangeSettings)
                Button("Save runtime settings", action: state.saveRuntimeSettings)
                    .disabled(!state.settingsHaveChanges || !state.canChangeSettings || state.isSavingSettings)
                    .accessibilityIdentifier("settings.save")
                if state.restartRequired {
                    HStack {
                        Label("Saved settings are not active yet.", systemImage: "arrow.clockwise").foregroundStyle(.orange)
                        Spacer()
                        Button("Restart local services now", action: state.restartServices).disabled(state.isRestarting)
                    }
                } else if state.settingsHaveChanges {
                    Label("Unsaved changes", systemImage: "circle.fill").font(.caption).foregroundStyle(.secondary)
                }
            }
        }
        .formStyle(.grouped).navigationTitle("Settings")
        .task { await state.refreshSettings() }
        .sheet(isPresented: $state.memorySheet) {
            VStack(alignment: .leading, spacing: 12) {
                Text("Conversation memory").font(.title2)
                if state.memoryBusy {
                    ProgressView()
                } else if state.memories.isEmpty {
                    ContentUnavailableView("No saved summaries", systemImage: "brain")
                } else {
                    List(state.memories) { memory in
                        VStack(alignment: .leading, spacing: 4) {
                            Text(memory.content).textSelection(.enabled)
                            Text("From \(memory.sourceCount) conversation turns")
                                .foregroundStyle(.secondary).font(.caption)
                        }.padding(.vertical, 4)
                    }
                }
                HStack {
                    Button("Clear summaries…", role: .destructive) { state.confirmClearMemory = true }
                        .disabled(state.memoryBusy || state.memories.isEmpty)
                    Spacer()
                    Button("Done") { state.memorySheet = false }.keyboardShortcut(.defaultAction)
                }
            }
            .padding(20)
            .frame(minWidth: 520, minHeight: 320)
            .confirmationDialog("Clear conversation summaries?", isPresented: $state.confirmClearMemory) {
                Button("Clear summaries", role: .destructive, action: state.clearMemories)
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("The transcript and research records are preserved. If memory is enabled, MacBot can create new summaries from the transcript.")
            }
        }
        .confirmationDialog("Remove the Brave Search credential?", isPresented: $state.confirmCredentialRemoval, titleVisibility: .visible) {
            Button("Remove credential", role: .destructive, action: state.deleteSearchCredential)
            Button("Cancel", role: .cancel) {}
        } message: { Text("Web search will stop working until another supported credential is saved.") }
    }
}
