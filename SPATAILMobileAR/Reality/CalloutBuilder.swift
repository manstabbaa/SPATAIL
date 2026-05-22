// CalloutBuilder.swift
//
// Anchored-callout element: a small label panel with a stem pointing
// toward the (anchor-implied) target. The contract already positions
// the callout near its target; this builder just renders the visual.

import Foundation
import RealityKit
import UIKit

struct CalloutBuilder {
    func build(for element: SpatialElement) -> Entity {
        let group = Entity()

        let panelW: Float = max(element.placement.planeSizeMeters.width, 0.32)
        let panelH: Float = max(element.placement.planeSizeMeters.height, 0.16)

        // Renderable text panel (same renderer as 2D panels, callout style).
        let panel = SpatialPanelBuilder().build(for: element, style: .callout)
        group.addChild(panel)

        // Stem: a short cylinder from the panel's bottom-left back
        // toward the target attachment point. Direction is approximated
        // (-X / -Y) since the target sits on the table at a lower Y
        // and slightly to the left of the callout per the placement
        // engine. This is intentionally a heuristic — when the player
        // knows the actual target position relative to the callout, it
        // can replace this with a real line segment.
        let stemMaterial = SimpleMaterial(
            color: UIColor(red: 0.96, green: 0.73, blue: 0.26, alpha: 0.9),
            roughness: 0.3, isMetallic: false,
        )
        let stem = ModelEntity(
            mesh: .generateCylinder(height: 0.12, radius: 0.0035),
            materials: [stemMaterial],
        )
        stem.position = SIMD3<Float>(-panelW / 2 - 0.04, -panelH / 2, 0)
        stem.orientation = simd_quatf(angle: .pi / 4, axis: SIMD3<Float>(0, 0, 1))
        group.addChild(stem)
        return group
    }
}
