// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "MacBotApp",
    platforms: [.macOS(.v14)],
    products: [.executable(name: "MacBotApp", targets: ["MacBotApp"])],
    targets: [
        .executableTarget(
            name: "MacBotApp",
            path: "Sources/MacBotApp",
            resources: [.process("Resources")]
        ),
        .testTarget(
            name: "MacBotAppTests",
            dependencies: ["MacBotApp"],
            path: "Tests/MacBotAppTests",
            swiftSettings: [
                .unsafeFlags([
                    "-load-plugin-library",
                    "/Library/Developer/CommandLineTools/usr/lib/swift/host/plugins/testing/libTestingMacros.dylib",
                ])
            ],
            linkerSettings: [
                .unsafeFlags([
                    "-Xlinker", "-rpath", "-Xlinker",
                    "/Library/Developer/CommandLineTools/Library/Developer/Frameworks",
                    "-Xlinker", "-rpath", "-Xlinker",
                    "/Library/Developer/CommandLineTools/Library/Developer/usr/lib",
                ])
            ]
        ),
    ]
)
