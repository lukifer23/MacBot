import Foundation
import Security

struct ServiceManager {
    let dataDirectory: URL
    let cliPath: String
    let sourcePath: String

    init(dataDirectory: URL? = nil, cliPath: String? = nil, sourcePath: String? = nil) {
        self.dataDirectory = dataDirectory ?? FileManager.default.homeDirectoryForCurrentUser
            .appending(path: "Library/Application Support/MacBot", directoryHint: .isDirectory)
        self.cliPath = cliPath ?? Bundle.main.object(forInfoDictionaryKey: "MacBotCLIPath") as? String ?? "macbot"
        self.sourcePath = sourcePath ?? Bundle.main.object(forInfoDictionaryKey: "MacBotSourcePath") as? String ?? ""
    }

    func prepareToken() throws -> String {
        let run = dataDirectory.appending(path: "run", directoryHint: .isDirectory)
        try FileManager.default.createDirectory(at: run, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        var bytes = [UInt8](repeating: 0, count: 32)
        guard SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes) == errSecSuccess else {
            throw NSError(domain: "MacBot", code: 2, userInfo: [NSLocalizedDescriptionKey: "Could not create the native session token"])
        }
        let token = bytes.map { String(format: "%02x", $0) }.joined()
        let path = run.appending(path: "native-token")
        try Data(token.utf8).write(to: path, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: path.path)
        return token
    }

    func restart(historyKey: Data) async throws -> String {
        _ = try? await run(["stop"])
        let token = try prepareToken()
        _ = try await run(["start", "--background"], standardInput: historyKey)
        return token
    }

    func stop() async {
        _ = try? await run(["stop"])
    }

    func stopSynchronously() {
        let process = configuredProcess(["stop"])
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        try? process.run()
        process.waitUntilExit()
    }

    private func configuredProcess(_ arguments: [String]) -> Process {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: cliPath)
        process.arguments = arguments
        var environment = ProcessInfo.processInfo.environment
        environment["MACBOT_DATA_DIR"] = dataDirectory.path
        if !sourcePath.isEmpty {
            environment["PYTHONPATH"] = sourcePath
        }
        process.environment = environment
        return process
    }

    private func run(_ arguments: [String], standardInput: Data? = nil) async throws -> String {
        try await Task.detached(priority: .userInitiated) {
            let process = self.configuredProcess(arguments)
            let output = Pipe()
            let input = standardInput.map { _ in Pipe() }
            process.standardOutput = output
            process.standardError = output
            if let input {
                process.standardInput = input
                var environment = process.environment ?? ProcessInfo.processInfo.environment
                environment["MACBOT_HISTORY_KEY_FD"] = "0"
                process.environment = environment
            }
            try process.run()
            if let input, let standardInput {
                input.fileHandleForWriting.write(standardInput)
                try? input.fileHandleForWriting.close()
            }
            process.waitUntilExit()
            let text = String(decoding: output.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
            guard process.terminationStatus == 0 else {
                throw NSError(domain: "MacBot", code: Int(process.terminationStatus), userInfo: [NSLocalizedDescriptionKey: text.isEmpty ? "MacBot service command failed" : text])
            }
            return text
        }.value
    }
}
