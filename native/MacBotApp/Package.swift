// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "MacBotApp",
    platforms: [.macOS(.v14)],
    products: [.executable(name: "MacBotApp", targets: ["MacBotApp"])],
    targets: [.executableTarget(name: "MacBotApp", path: "Sources/MacBotApp")]
)
