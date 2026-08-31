import AppKit
import SwiftUI

final class AppDelegate: NSObject, NSApplicationDelegate {
    var shutdown: (() -> Void)?

    func applicationWillFinishLaunching(_ notification: Notification) {
        let current = ProcessInfo.processInfo.processIdentifier
        if let existing = NSRunningApplication.runningApplications(
            withBundleIdentifier: "local.macbot.app"
        ).first(where: { $0.processIdentifier != current }) {
            existing.activate(options: [.activateAllWindows])
            NSApp.terminate(nil)
        }
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        DispatchQueue.main.async {
            NSApp.activate()
            NSApp.windows.first(where: { $0.canBecomeKey })?.makeKeyAndOrderFront(nil)
        }
    }

    func applicationWillTerminate(_ notification: Notification) { shutdown?() }
}

@main
struct MacBotApplication: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @StateObject private var state = AppState()

    var body: some Scene {
        WindowGroup("MacBot", id: "main") {
            ContentView().environmentObject(state).onAppear {
                delegate.shutdown = state.shutdown
                state.start()
            }
        }
        .defaultSize(width: 1080, height: 760)
        .windowResizability(.contentMinSize)
        .commands {
            CommandGroup(after: .appInfo) {
                Button("Stop response") { state.interrupt() }.keyboardShortcut(".", modifiers: .command)
            }
        }

        MenuBarExtra {
            MenuBarControls().environmentObject(state)
        } label: {
            Image(systemName: state.productState.symbol)
                .accessibilityLabel(state.productState.title)
        }
        .menuBarExtraStyle(.menu)
    }
}

private struct MenuBarControls: View {
    @Environment(\.openWindow) private var openWindow
    @EnvironmentObject private var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label(state.productState.title, systemImage: state.productState.symbol)
            Text(state.connectionDetail).font(.caption).foregroundStyle(.secondary)
            Divider()
            Button(state.listening ? "Stop hands-free" : "Start hands-free") {
                state.toggleListening()
            }
            .disabled(!state.canListen)
            Button("Stop response") { state.interrupt() }.disabled(!state.canInterrupt)
            if !state.connected {
                Button("Retry local services") { state.restartServices() }.disabled(state.isRestarting)
            }
            Button("Open Task Center") {
                state.selectedPage = .tasks
                showMainWindow()
            }
            Button("Open MacBot") {
                showMainWindow()
            }
            Divider()
            Button("Quit MacBot") { NSApp.terminate(nil) }
        }
        .padding(4)
    }

    private func showMainWindow() {
        openWindow(id: "main")
        NSApp.activate()
        DispatchQueue.main.async {
            NSApp.windows.first(where: { $0.canBecomeKey })?.makeKeyAndOrderFront(nil)
        }
    }
}
