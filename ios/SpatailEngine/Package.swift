// swift-tools-version:5.9
import PackageDescription

// SpatailEngine — a platform-agnostic spatial-experience engine.
//
//   • SpatailEngineCore        Foundation-only. The simulation: ECS-lite world,
//                              systems scheduler, the rules/objectives/goals
//                              engine, kits, the contract. NO RealityKit / ARKit /
//                              SwiftUI — it can run on iOS, visionOS, macOS, Linux,
//                              or a headless test harness. The "platform-agnostic
//                              capabilities" live here.
//
//   • SpatailEngineRealityKit  The iOS/visionOS adapter: implements `SceneBackend`
//                              by mapping the core's commands onto RealityKit and
//                              feeding ARKit/RealityKit events back in. Swap this
//                              target to port the engine to another platform.
//
// The core never imports a rendering framework. It emits `BackendCommand`s and
// consumes `EngineEvent`s; the backend is the only platform-specific code.

let package = Package(
    name: "SpatailEngine",
    platforms: [
        .iOS(.v17),
        .visionOS(.v1),
        .macOS(.v13)        // so `swift test` runs the pure core on a Mac
    ],
    products: [
        .library(name: "SpatailEngineCore", targets: ["SpatailEngineCore"]),
        .library(name: "SpatailEngineRealityKit", targets: ["SpatailEngineRealityKit"]),
    ],
    targets: [
        .target(name: "SpatailEngineCore"),
        .target(
            name: "SpatailEngineRealityKit",
            dependencies: ["SpatailEngineCore"]
        ),
        .testTarget(
            name: "SpatailEngineCoreTests",
            dependencies: ["SpatailEngineCore"]
        ),
    ]
)
