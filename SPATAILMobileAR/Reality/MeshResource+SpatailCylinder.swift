// MeshResource+SpatailCylinder.swift
//
// iOS-16-safe cylinder. RealityKit's built-in `MeshResource.generateCylinder`
// is iOS 18+, but SPATAIL deploys to iOS 16 on purpose (it hosts the scene in
// an `ARView`, not a RealityView, precisely to keep the iOS 16 floor — see
// ARExperienceCoordinator). On iOS 16/17 we approximate the thin stems and
// dashed-line segments with a square-section box of the same height. At the
// radii used here (~3 mm) a box and a cylinder are visually indistinguishable,
// and both have their long axis on +Y, so existing orientation math is unchanged.

import RealityKit
import simd

extension MeshResource {
    /// A cylinder whose long axis is +Y, falling back to an equivalent
    /// thin box on systems older than iOS 18 where `generateCylinder`
    /// is unavailable.
    static func spatailCylinder(height: Float, radius: Float) -> MeshResource {
        if #available(iOS 18.0, *) {
            return .generateCylinder(height: height, radius: radius)
        } else {
            return .generateBox(size: SIMD3<Float>(radius * 2, height, radius * 2))
        }
    }
}
