import SwiftUI
import UniformTypeIdentifiers

struct ContentView: View {
    @EnvironmentObject private var state: AppState

    var body: some View {
        NavigationSplitView {
            List(SidebarPage.allCases, selection: $state.selectedPage) { page in
                Label(page.rawValue, systemImage: page.symbol).tag(page)
            }
            .navigationTitle("MacBot")
            .safeAreaInset(edge: .bottom) {
                HStack(spacing: 8) {
                    Circle().fill(state.connected ? .green : .orange).frame(width: 8, height: 8)
                    Text(state.connectionDetail)
                        .font(.caption).foregroundStyle(.secondary)
                    Spacer()
                }.padding()
            }
        } detail: {
            switch state.selectedPage ?? .conversation {
            case .conversation: ConversationView()
            case .library: LibraryView()
            case .diagnostics: DiagnosticsView()
            case .settings: SettingsView()
            }
        }
        .frame(minWidth: 920, minHeight: 640)
        .alert("MacBot needs attention", isPresented: Binding(
            get: { state.errorMessage != nil },
            set: { if !$0 { state.errorMessage = nil } }
        )) {
            Button("Dismiss", role: .cancel) { state.errorMessage = nil }
        } message: { Text(state.errorMessage ?? "Unknown error") }
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
                        if state.messages.isEmpty {
                            ContentUnavailableView(
                                "Ready when you are",
                                systemImage: "waveform.circle",
                                description: Text("Type a message or start hands-free conversation.")
                            ).padding(.top, 90)
                        }
                        ForEach(state.timeline) { item in
                            switch item {
                            case .message(let message): MessageBubble(item: message)
                            case .task(let task): TaskResultCard(item: task)
                            }
                        }
                    }.padding(24)
                }
                .onChange(of: state.messages.count + state.tasks.count) {
                    if let id = state.timeline.last?.id {
                        withAnimation(.easeOut(duration: 0.2)) { proxy.scrollTo(id, anchor: .bottom) }
                    }
                }
            }
            Divider()
            composer
        }
        .navigationTitle("Conversation")
        .toolbar {
            ToolbarItemGroup {
                Button("Clear", systemImage: "trash", action: state.clearConversation)
                    .help("Delete this local conversation")
                Button("Interrupt", systemImage: "stop.fill", action: state.interrupt)
                    .help("Stop the current response and playback")
            }
        }
    }

    private var statusHeader: some View {
        HStack(spacing: 14) {
            Image(systemName: state.phase.symbol)
                .font(.title2).symbolEffect(.pulse, isActive: [.listening, .thinking, .acting, .speaking].contains(state.phase))
                .frame(width: 40, height: 40).background(.quaternary, in: Circle())
            VStack(alignment: .leading, spacing: 3) {
                Text(state.phase.title).font(.headline)
                Text(state.heard.isEmpty ? "Your transcript will appear here." : "Heard: \(state.heard)")
                    .font(.subheadline).foregroundStyle(.secondary).lineLimit(2)
            }
            Spacer()
            Button(action: state.toggleMuted) {
                Label(state.muted ? "Unmute" : "Mute", systemImage: state.muted ? "mic.slash.fill" : "mic.fill")
            }
            .buttonStyle(.bordered)
            Button(action: state.toggleListening) {
                Label(state.listening ? "Stop hands-free" : "Start hands-free", systemImage: state.listening ? "stop.circle.fill" : "waveform.circle.fill")
            }
            .buttonStyle(.borderedProminent)
            .disabled(!state.connected)
        }.padding(20).background(.bar)
    }

    private var composer: some View {
        HStack(alignment: .bottom, spacing: 12) {
            TextField("Ask MacBot anything…", text: $state.draft, axis: .vertical)
                .textFieldStyle(.plain).lineLimit(1...5).focused($composerFocused)
                .onSubmit { if !NSEvent.modifierFlags.contains(.shift) { state.send() } }
            Button(action: state.send) { Image(systemName: "arrow.up").fontWeight(.semibold) }
                .buttonStyle(.borderedProminent).buttonBorderShape(.circle)
                .disabled(state.draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !state.connected)
                .keyboardShortcut(.return, modifiers: .command)
        }
        .padding(14).background(.regularMaterial).clipShape(RoundedRectangle(cornerRadius: 16))
        .padding(18)
    }
}

private struct MessageBubble: View {
    let item: ChatItem
    var body: some View {
        HStack {
            if item.role == .user { Spacer(minLength: 120) }
            Text(item.text).textSelection(.enabled).padding(.horizontal, 16).padding(.vertical, 12)
                .background(item.role == .user ? Color.accentColor : Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 16))
                .foregroundStyle(item.role == .user ? .white : .primary)
            if item.role != .user { Spacer(minLength: 120) }
        }
    }
}

private struct TaskResultCard: View {
    let item: TaskItem
    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: symbol)
                .foregroundStyle(tint)
            VStack(alignment: .leading, spacing: 5) {
                HStack {
                    Text(item.title.replacingOccurrences(of: "_", with: " ").capitalized).font(.headline)
                    Text(item.state.capitalized).font(.caption).foregroundStyle(.secondary)
                }
                Text(item.detail).font(.caption).foregroundStyle(.secondary).lineLimit(4)
            }
            Spacer()
        }.padding(14).background(.thinMaterial, in: RoundedRectangle(cornerRadius: 14))
    }

    private var symbol: String {
        switch item.state {
        case "running", "accepted": "clock.arrow.circlepath"
        case "denied": "nosign"
        case "partial": "exclamationmark.circle.fill"
        case "failed": "xmark.circle.fill"
        case "interrupted": "stop.circle.fill"
        default: "checkmark.circle.fill"
        }
    }

    private var tint: Color {
        switch item.state {
        case "running", "accepted": .blue
        case "denied", "partial": .orange
        case "failed": .red
        case "interrupted": .secondary
        default: .green
        }
    }
}

private struct LibraryView: View {
    @EnvironmentObject private var state: AppState
    var body: some View {
        VStack(spacing: 0) {
            HStack {
                TextField("Test document retrieval…", text: $state.documentQuery)
                    .textFieldStyle(.roundedBorder).onSubmit { state.searchDocuments(state.documentQuery) }
                Button("Search") { state.searchDocuments(state.documentQuery) }.disabled(state.documentQuery.trimmingCharacters(in: .whitespaces).isEmpty)
                Button("Import", systemImage: "plus") { state.importingDocuments = true }.buttonStyle(.borderedProminent)
            }.padding()
            Divider()
            if state.documents.isEmpty {
                ContentUnavailableView("No documents", systemImage: "books.vertical", description: Text("Import TXT, PDF, or DOCX files to search them locally."))
            } else {
                List {
                    ForEach(state.documents) { document in
                        HStack {
                            Image(systemName: "doc.text")
                            VStack(alignment: .leading) {
                                Text(document.title).font(.headline)
                                Text("\(document.type.uppercased()) · \(document.length.formatted()) characters").font(.caption).foregroundStyle(.secondary)
                            }
                            Spacer()
                            Button("Delete", systemImage: "trash", role: .destructive) { state.deleteDocument(document.id) }.labelStyle(.iconOnly)
                        }.padding(.vertical, 4)
                    }
                    if !state.documentResults.isEmpty {
                        Section("Retrieval results") {
                            ForEach(Array(state.documentResults.enumerated()), id: \.offset) { _, result in
                                Text(result).lineLimit(5).textSelection(.enabled)
                            }
                        }
                    }
                }
            }
        }
        .navigationTitle("Library")
        .onAppear { state.refreshDocuments() }
        .fileImporter(isPresented: $state.importingDocuments, allowedContentTypes: [.plainText, .pdf, .init(filenameExtension: "docx")!], allowsMultipleSelection: false) { result in
            if case .success(let urls) = result, let url = urls.first { state.importDocument(url) }
        }
    }
}

private struct DiagnosticsView: View {
    @EnvironmentObject private var state: AppState
    var body: some View {
        ScrollView {
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 180))], spacing: 14) {
                ForEach(state.metrics) { metric in
                    VStack(alignment: .leading, spacing: 8) {
                        Text(metric.label).foregroundStyle(.secondary)
                        Text(metric.value).font(.system(.title, design: .rounded, weight: .semibold))
                    }.frame(maxWidth: .infinity, alignment: .leading).padding(18)
                        .background(.quaternary, in: RoundedRectangle(cornerRadius: 14))
                }
            }.padding(24)
        }.navigationTitle("Diagnostics").toolbar {
            Button("Refresh", systemImage: "arrow.clockwise") { Task { await state.refreshStatus() } }
        }
    }
}

private struct SettingsView: View {
    @EnvironmentObject private var state: AppState
    var body: some View {
        Form {
            Section("Models") {
                LabeledContent("Language model", value: state.modelName)
                Picker("Voice candidate", selection: $state.selectedVoice) {
                    ForEach(state.availableVoices) { voice in
                        Text(voice.label + (voice.installed ? "" : " · not installed"))
                            .tag(voice.id)
                            .disabled(!voice.installed)
                    }
                }
                LabeledContent("Active voice", value: VoiceOption.label(for: state.voiceName))
                Button("Preview active voice", systemImage: "speaker.wave.2", action: state.previewVoice)
                Text("A saved voice becomes active after MacBot restarts. Qwen candidates are locally converted from pinned official weights; final selection requires listening approval.")
                    .foregroundStyle(.secondary)
            }
            Section("Privacy") {
                LabeledContent("Conversation history", value: state.historyAvailable ? "Encrypted · \(state.retentionDays) days" : "Unavailable")
                Text("Conversation content stays on this Mac. Raw microphone audio is not retained.").foregroundStyle(.secondary)
                Stepper("Retention: \(state.retentionDays) days", value: $state.retentionDays, in: 1...3650)
            }
            Section("Web search") {
                LabeledContent("Brave Search", value: state.searchCredentialConfigured ? "Configured in Keychain" : "Not configured")
                SecureField("Brave Search API key", text: $state.searchCredential)
                HStack {
                    Button("Save to Keychain", action: state.saveSearchCredential)
                        .disabled(state.searchCredential.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    if state.searchCredentialConfigured {
                        Button("Remove credential", role: .destructive, action: state.deleteSearchCredential)
                    }
                }
                Text("When no key is configured, DDGS results are explicitly labeled degraded.")
                    .foregroundStyle(.secondary)
            }
            Section("Conversation") {
                Stepper("Endpoint silence: \(state.endpointMilliseconds) ms", value: $state.endpointMilliseconds, in: 150...2000, step: 25)
                Picker("Context target", selection: $state.contextLength) {
                    Text("8K").tag(8192)
                    Text("16K").tag(16_384)
                    Text("32K").tag(32_768)
                }
                Toggle("Enable browser diagnostics fallback", isOn: $state.browserFallbackEnabled)
                Button("Save runtime settings", action: state.saveRuntimeSettings)
                if state.restartRequired {
                    Label("Quit and reopen MacBot to apply these changes.", systemImage: "arrow.clockwise")
                        .foregroundStyle(.orange)
                }
            }
        }.formStyle(.grouped).navigationTitle("Settings")
            .task { await state.refreshSettings() }
    }
}
