// swift-tools-version: 5.10
// Package.swift — Swift Package manifest for SpatailPlayer.
//
// The iOS app target is created in Xcode; this Package wraps the Sources/
// folder so the same code can be unit-tested on macOS host (without ARKit)
// and consumed by the Xcode app as a local package dependency.

import PackageDescription

let package = Package(
    name: "SpatailPlayer",
    platforms: [
        .iOS("18.0"),     // SceneController uses Entity(contentsOf:) which is iOS 18+
        .macOS(.v14),     // for host-side codable/protocol tests
    ],
    products: [
        .library(name: "SpatailPlayer", targets: ["SpatailPlayer"]),
    ],
    dependencies: [
        .package(url: "https://github.com/weichsel/ZIPFoundation", from: "0.9.19"),
    ],
    targets: [
        .target(
            name: "SpatailPlayer",
            dependencies: [
                .product(name: "ZIPFoundation", package: "ZIPFoundation"),
            ],
            path: "Sources/SpatailPlayer"),
        .testTarget(
            name: "SpatailPlayerTests",
            dependencies: ["SpatailPlayer"],
            path: "Tests/SpatailPlayerTests"),
    ]
)
