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
    @FocusState private var composerFocused: Bool

    var body: some View {
        VStack(spacing: 0) {
            statusHeader
            Divider()
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(spacing: 14) {
                        if state.messages.isEmpty && state.tasks.isEmpty {
                            ContentUnavailableView(
                                state.canConverse ? "Ready when you are" : state.productState.title,
                                systemImage: state.productState.symbol,
                                description: Text(state.canConverse
                                    ? "Type a message or start hands-free conversation."
                                    : state.connectionDetail)
                            ).padding(.top, 90)
                        }
                        ForEach(state.timeline) { item in
                            switch item {
                            case .message(let message): MessageBubble(item: message)
                            case .task(let task): TaskResultCard(item: task, compact: true)
                            }
                        }
                    }.padding(24)
                }
                .onChange(of: state.messages.count + state.tasks.count) {
                    if let id = state.timeline.last?.id {
                        withAnimation(.easeOut(duration: 0.2)) { proxy.scrollTo(id, anchor: .bottom) }
                    }
                }
                .onChange(of: state.messages.last?.text) {
                    if let id = state.timeline.last?.id {
                        proxy.scrollTo(id, anchor: .bottom)
                    }
                }
            }
            Divider()
            composer
        }
        .navigationTitle("Conversation")
        .toolbar {
            ToolbarItemGroup {
                Button("Clear conversation", systemImage: "trash") { state.confirmClearConversation = true }
                    .disabled(state.messages.isEmpty || !state.canConverse || state.isClearingConversation)
                    .help("Delete this local conversation")
                    .accessibilityIdentifier("conversation.clear")
                Button("Stop response", systemImage: "stop.fill", action: state.interrupt)
                    .disabled(!state.canInterrupt || state.isInterrupting)
                    .help("Stop the current response and playback")
                    .accessibilityIdentifier("conversation.stop")
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
                .symbolEffect(.pulse, isActive: [.listening, .thinking, .acting, .speaking].contains(state.phase))
                .frame(width: 40, height: 40).background(.quaternary, in: Circle())
            VStack(alignment: .leading, spacing: 3) {
                Text(state.productState.isOperational ? state.phase.title : state.productState.title).font(.headline)
                Text(state.heard.isEmpty ? state.connectionDetail : "Heard: \(state.heard)")
                    .font(.subheadline).foregroundStyle(.secondary).lineLimit(2)
            }
            Spacer()
            Button(action: state.toggleMuted) {
                Label(state.muted ? "Unmute" : "Mute", systemImage: state.muted ? "mic.slash.fill" : "mic.fill")
            }
            .buttonStyle(.bordered)
            .disabled(!state.listening || state.isChangingListening)
            .accessibilityIdentifier("conversation.mute")
            Button(action: state.toggleListening) {
                Label(state.listening ? "Stop hands-free" : "Start hands-free", systemImage: state.listening ? "stop.circle.fill" : "waveform.circle.fill")
            }
            .buttonStyle(.borderedProminent)
            .disabled(!state.canListen || state.isChangingListening)
            .accessibilityIdentifier("conversation.hands-free")
        }.padding(20).background(.bar)
    }

    private var composer: some View {
        VStack(alignment: .leading, spacing: 8) {
            Picker("Composer mode", selection: $state.composerMode) {
                ForEach(ComposerMode.allCases) { mode in Text(mode.rawValue).tag(mode) }
            }
            .pickerStyle(.segmented)
            .accessibilityHint("Choose an immediate conversation or a durable task that requires authorization")
            .accessibilityIdentifier("composer.mode")
            Text(state.composerMode.guidance).font(.caption).foregroundStyle(.secondary)
            if state.isSending {
                ProgressView(state.composerMode == .task ? "Planning the Task…" : "Sending…")
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
                    Label(state.composerMode.actionLabel, systemImage: state.composerMode == .task ? "checklist" : "arrow.up")
                        .labelStyle(.iconOnly).fontWeight(.semibold)
                }
                .buttonStyle(.borderedProminent).buttonBorderShape(.circle)
                .disabled(state.draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !state.canConverse || state.isSending)
                .keyboardShortcut(.return, modifiers: .command)
                .accessibilityIdentifier("composer.send")
            }
            if state.composerMode == .conversation {
                Toggle("Speak typed replies", isOn: $state.speakTypedReplies)
                    .toggleStyle(.checkbox).font(.caption).disabled(!state.canConverse)
            }
        }
        .padding(14).background(.regularMaterial).clipShape(RoundedRectangle(cornerRadius: 16))
        .padding(18)
    }
}

private struct MessageBubble: View {
    let item: ChatItem
    var body: some View {
        HStack {
            if item.role == .user { Spacer(minLength: 100) }
            Text(item.text).textSelection(.enabled).padding(.horizontal, 16).padding(.vertical, 12)
                .background(item.role == .user ? Color.accentColor.opacity(0.18) : Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 16))
                .foregroundStyle(.primary)
            if item.role != .user { Spacer(minLength: 100) }
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel(item.role == .user ? "You" : item.role == .assistant ? "MacBot" : "System")
        .accessibilityValue(item.text)
        .accessibilityIdentifier("message.\(item.id)")
    }
}

private struct TaskResultCard: View {
    @EnvironmentObject private var state: AppState
    let item: TaskItem
    var compact = false

    var body: some View {
        DisclosureGroup {
            VStack(alignment: .leading, spacing: 10) {
                Text(item.detail).textSelection(.enabled)
                Label(item.source.replacingOccurrences(of: "_", with: " ").capitalized, systemImage: "person.crop.circle.badge.checkmark")
                    .font(.caption).foregroundStyle(.secondary)
                if !item.authority.isEmpty { authority }
                if !item.steps.isEmpty { steps }
                if !item.availableCommands.isEmpty {
                    HStack {
                        ForEach(TaskCommand.allCases.filter(item.availableCommands.contains), id: \.self) { command in
                            if command == .cancel || command == .deny {
                                Button(command.label, systemImage: command.symbol) { state.perform(command, on: item) }
                                    .buttonStyle(.bordered)
                                    .disabled(state.taskCommandsInFlight.contains(item.id))
                                    .accessibilityIdentifier("task.\(item.id).\(command.rawValue)")
                            } else {
                                Button(command.label, systemImage: command.symbol) { state.perform(command, on: item) }
                                    .buttonStyle(.borderedProminent)
                                    .disabled(state.taskCommandsInFlight.contains(item.id))
                                    .accessibilityIdentifier("task.\(item.id).\(command.rawValue)")
                            }
                        }
                    }
                }
            }.padding(.top, 8)
        } label: {
            HStack(alignment: .top, spacing: 12) {
                Image(systemName: item.state.symbol).foregroundStyle(tint)
                VStack(alignment: .leading, spacing: 4) {
                    Text(item.title.replacingOccurrences(of: "_", with: " ").capitalized).font(.headline)
                    Text(item.state.title).font(.caption).foregroundStyle(.secondary)
                    if compact { Text(item.detail).font(.caption).foregroundStyle(.secondary).lineLimit(2) }
                }
                Spacer()
            }
        }
        .padding(14).background(.thinMaterial, in: RoundedRectangle(cornerRadius: 14))
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("task.\(item.id)")
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
                            Text("Depends on: \(step.dependsOn.joined(separator: ", "))")
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
            Section("Privacy") {
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
        .confirmationDialog("Remove the Brave Search credential?", isPresented: $state.confirmCredentialRemoval, titleVisibility: .visible) {
            Button("Remove credential", role: .destructive, action: state.deleteSearchCredential)
            Button("Cancel", role: .cancel) {}
        } message: { Text("Web search will stop working until another supported credential is saved.") }
    }
}
