// GuideLineBuilder.swift
//
// Renders a dashed line segment between two SIMD3 positions. Used by
// the assembly builder; pulled out so any future "this thing relates
// to that thing" visualization can reuse the same pattern.

import Foundation
import RealityKit
import UIKit

struct GuideLineBuilder {
    /// Builds a `guide_line` element from the contract. The planner
    /// resolves both endpoints into absolute world positions on the
    /// element's `placement.from` / `placement.to`, so the renderer
    /// has nothing to look up — it just connects them.
    ///
    /// IMPORTANT: ARSceneRenderer applies element.placement.position to
    /// the returned entity's transform. The dashes here live in *local*
    /// space, so we subtract the midpoint from from/to.
    func build(for element: SpatialElement) -> Entity {
        let p = element.placement
        let group = Entity()
        guard let from = p.from, from.count >= 3,
              let to = p.to, to.count >= 3,
              let mid = p.position, mid.count >= 3 else {
            return group
        }
        let f = SIMD3<Float>(Float(from[0] - mid[0]),
                             Float(from[1] - mid[1]),
                             Float(from[2] - mid[2]))
        let t = SIMD3<Float>(Float(to[0] - mid[0]),
                             Float(to[1] - mid[1]),
                             Float(to[2] - mid[2]))
        group.addChild(GuideLineBuilder.dashedLine(from: f, to: t))
        return group
    }

    /// Returns an Entity containing N short cylinders forming a dashed
    /// line from `from` to `to`. Color/thickness tuned for the indoor
    /// AR scale used by SPATAIL contracts.
    static func dashedLine(from: SIMD3<Float>,
                           to: SIMD3<Float>,
                           color: UIColor = UIColor(red: 0.43, green: 0.66, blue: 1.0, alpha: 0.85),
                           dashLength: Float = 0.025,
                           gap: Float = 0.02,
                           radius: Float = 0.003) -> Entity {
        let group = Entity()
        let diff = to - from
        let length = simd_length(diff)
        if length < 1e-4 { return group }
        let dir = diff / length
        let mat = SimpleMaterial(color: color, roughness: 0.3, isMetallic: false)

        let stride = dashLength + gap
        var t: Float = 0
        while t + dashLength <= length {
            let center = from + dir * (t + dashLength / 2)
            let dash = ModelEntity(
                mesh: .generateCylinder(height: dashLength, radius: radius),
                materials: [mat],
            )
            dash.position = center
            // Align cylinder (+Y axis) to dir.
            dash.orientation = simd_quatf(from: SIMD3<Float>(0, 1, 0), to: dir)
            group.addChild(dash)
            t += stride
        }
        return group
    }
}
