// swift-tools-version: 6.2
// The classifier's on-device rung (see ../../sara/classify.py, AppleBackend):
// a tiny stdin/stdout JSON shim over Apple's Foundation Models framework.
// Built lazily by the classifier (`swift build -c release`) — never a hard
// install dependency; the skill runs fine on machines with no Swift at all.
import PackageDescription

let package = Package(
    name: "sara-classify-shim",
    platforms: [.macOS(.v26)],
    targets: [
        .executableTarget(name: "sara-classify-shim")
    ]
)
