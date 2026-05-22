// MustangServiceSceneBuilder.swift
//
// Adds Mustang-specific scenery to the root entity *before* the per-
// element renderers run. For v1 this is a "simulated engine bay base":
// a dark slab beneath where the target housing will appear, so the
// air filter doesn't look like it's floating in mid-air on top of the
// user's real floor.
//
// Future: detect a real horizontal plane large enough to host the
// workspace and skip the slab entirely.

import Foundation
import RealityKit
import UIKit

struct MustangServiceSceneBuilder {
    /// Drops the engine-bay base into the experience root. The base
    /// sits at y ≈ 0.74 (just under the target's y=0.8) so the part
    /// visibly rests on it.
    func addScenery(to root: Entity) {
        let baseW: Float = 1.4
        let baseH: Float = 0.04
        let baseD: Float = 0.9
        let baseMat = SimpleMaterial(
            color: UIColor(red: 0.18, green: 0.20, blue: 0.24, alpha: 1.0),
            roughness: 0.7, isMetallic: false,
        )
        let base = ModelEntity(
            mesh: .generateBox(size: SIMD3<Float>(baseW, baseH, baseD), cornerRadius: 0.02),
            materials: [baseMat],
        )
        base.position = SIMD3<Float>(0, 0.74, 0)
        base.name = "engine_bay_base"
        root.addChild(base)

        // A subtle accent strip across the base to suggest the
        // intake/airbox area — purely visual scaffolding.
        let stripMat = SimpleMaterial(
            color: UIColor(red: 0.43, green: 0.66, blue: 1.0, alpha: 0.4),
            roughness: 0.4, isMetallic: false,
        )
        let strip = ModelEntity(
            mesh: .generateBox(size: SIMD3<Float>(0.6, 0.005, 0.4), cornerRadius: 0.005),
            materials: [stripMat],
        )
        strip.position = SIMD3<Float>(0, 0.762, 0)
        root.addChild(strip)
    }
}
