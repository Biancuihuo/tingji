// swift-tools-version:6.0
import PackageDescription

let package = Package(
    name: "Tingji",
    platforms: [.macOS(.v15)],
    targets: [
        .executableTarget(
            name: "Tingji",
            path: "Sources/Tingji",
            swiftSettings: [.swiftLanguageMode(.v5)]
        )
    ]
)
