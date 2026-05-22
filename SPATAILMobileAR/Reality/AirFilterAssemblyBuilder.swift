// AirFilterAssemblyBuilder.swift
//
// Builds the Mustang exploded-view: a vertical stack of N component
// boxes with per-part labels, plus a dashed guide line running from
// the base (target position) straight up through every part.
//
// CRITICAL RULE (spec):
//   "The exploded air filter assembly must align directly above the
//    actual target housing. Do not randomly tilt the parts. The
//    serviced part must be visually obvious."
//
// The contract's placement engine already centers the assembly above
// the target on the X / Z axes. This builder enforces vertical stacking
// (parts strictly along +Y) and adds the guide line.

import Foundation
import RealityKit
import UIKit

struct AirFilterAssemblyBuilder {
    func build(for element: SpatialElement) -> Entity {
        let wrapper = ExplodableAssemblyEntity()
        wrapper.name = element.id

        let components = element.sourceContent?.components ?? [
            .init(id: "p1", name: "Top"),
            .init(id: "p2", name: "Middle"),
            .init(id: "p3", name: "Bottom"),
        ]

        let partWidth: Float = 0.18
        let partHeight: Float = 0.06
        let partDepth: Float = 0.14
        let collapsedGap: Float = 0.005
        let explodedGap: Float = 0.06
        wrapper.partHeight = partHeight
        wrapper.collapsedGap = collapsedGap
        wrapper.explodedGap = explodedGap

        let palette: [UIColor] = [
            UIColor(red: 0.43, green: 0.66, blue: 1.0, alpha: 1.0),
            UIColor(red: 0.71, green: 0.42, blue: 1.0, alpha: 1.0),
            UIColor(red: 0.96, green: 0.73, blue: 0.26, alpha: 1.0),
            UIColor(red: 0.34, green: 0.82, blue: 0.61, alpha: 1.0),
            UIColor(red: 0.94, green: 0.41, blue: 0.41, alpha: 1.0),
        ]

        let mesh = MeshResource.generateBox(
            size: SIMD3<Float>(partWidth, partHeight, partDepth),
            cornerRadius: 0.008,
        )

        for (i, comp) in components.enumerated() {
            let mat = SimpleMaterial(color: palette[i % palette.count],
                                     roughness: 0.45, isMetallic: false)
            let entity = ModelEntity(mesh: mesh, materials: [mat])
            wrapper.partEntities.append(entity)
            wrapper.addChild(entity)

            let label = LabelEntity.make(
                text: comp.name,
                widthMeters: 0.3, heightMeters: 0.05,
            )
            wrapper.partLabels.append(label)
            wrapper.addChild(label)
        }

        // Guide line — multiple short cylinders forming a dashed line
        // straight down (along -Y) from the assembly base toward where
        // the target housing sits. The exploded stack itself is
        // positioned by the contract; the guide visually closes the
        // gap so the part-to-position relationship is unmistakable.
        let guideHeight: Float = 0.55  // matches placement_engine offset
        let dashLen: Float = 0.025
        let gap: Float = 0.02
        var y: Float = 0
        let cylinderMat = SimpleMaterial(
            color: UIColor(red: 0.43, green: 0.66, blue: 1.0, alpha: 0.85),
            roughness: 0.3, isMetallic: false,
        )
        while y > -guideHeight {
            let dash = ModelEntity(
                mesh: .generateCylinder(height: dashLen, radius: 0.003),
                materials: [cylinderMat],
            )
            dash.position = SIMD3<Float>(0, y - dashLen / 2, 0)
            wrapper.addChild(dash)
            y -= (dashLen + gap)
        }

        // Initial layout = collapsed (parts close together).
        wrapper.collapse()
        return wrapper
    }
}

/// Concrete entity that exposes an Explodable interface to the coordinator.
/// Holds the part entities + their labels and moves them apart on toggle.
final class ExplodableAssemblyEntity: Entity, Explodable {
    var partEntities: [ModelEntity] = []
    var partLabels: [Entity] = []
    var partHeight: Float = 0.06
    var collapsedGap: Float = 0.005
    var explodedGap: Float = 0.06

    func explode() { layout(gap: explodedGap) }
    func collapse() { layout(gap: collapsedGap) }

    private func layout(gap: Float) {
        for (i, part) in partEntities.enumerated() {
            let y = Float(i) * (partHeight + gap)
            part.position = SIMD3<Float>(0, y, 0)
            if i < partLabels.count {
                partLabels[i].position = SIMD3<Float>(0.32, y, 0)
            }
        }
    }

    // SPATAIL_NEEDS_MAC_BUILD_VERIFY: see AnchoredObjectBuilder.swift for
    // the @MainActor caveat on Entity subclasses under Xcode 16+.
    required init() { super.init() }
}
