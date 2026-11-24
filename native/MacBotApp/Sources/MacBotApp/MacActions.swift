import AppKit
import Foundation

struct MacApplicationTarget: Equatable, Identifiable {
    let name: String
    let bundleID: String
    let url: URL

    var id: URL { url }

    init?(url: URL) {
        let path = url.standardizedFileURL.resolvingSymlinksInPath()
        guard path.isFileURL, path.pathExtension.lowercased() == "app",
              let bundle = Bundle(url: path),
              let bundleID = bundle.bundleIdentifier else { return nil }
        self.url = path
        self.bundleID = bundleID
        name = bundle.object(forInfoDictionaryKey: "CFBundleDisplayName") as? String
            ?? bundle.object(forInfoDictionaryKey: "CFBundleName") as? String
            ?? path.deletingPathExtension().lastPathComponent
    }
}

@MainActor
enum MacActions {
    static func installedApplications() -> [MacApplicationTarget] {
        let manager = FileManager.default
        let directories = [
            URL(fileURLWithPath: "/System/Applications", isDirectory: true),
            URL(fileURLWithPath: "/System/Applications/Utilities", isDirectory: true),
        ] + manager.urls(for: .applicationDirectory, in: [.localDomainMask, .userDomainMask])
        let registered = directories.flatMap {
            (try? manager.contentsOfDirectory(at: $0, includingPropertiesForKeys: nil, options: [.skipsHiddenFiles])) ?? []
        }
        let running = NSWorkspace.shared.runningApplications.compactMap(\.bundleURL)
        var seen = Set<URL>()
        return (running + registered).compactMap(MacApplicationTarget.init)
            .filter { seen.insert($0.id).inserted }
            .sorted {
                if $0.name == $1.name { return $0.url.path < $1.url.path }
                return $0.name.localizedStandardCompare($1.name) == .orderedAscending
            }
    }

    static func open(_ target: MacApplicationTarget) async throws -> NSRunningApplication {
        guard let current = MacApplicationTarget(url: target.url), current == target else {
            throw NativeClientError.protocolError("The selected app has changed. Review the target again.")
        }
        return try await NSWorkspace.shared.openApplication(at: target.url, configuration: .init())
    }
}
