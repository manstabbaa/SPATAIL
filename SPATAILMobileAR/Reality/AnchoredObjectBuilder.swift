// AnchoredObjectBuilder.swift
//
// Builds the highlighted-target entity used for `highlighted_target`
// elements (the Mustang air filter housing). Wrapped in a Highlightable
// proxy so the toggle button can pulse it on/off.

import Foundation
import RealityKit
import UIKit

struct AnchoredObjectBuilder {
    func buildTarget(for element: SpatialElement) -> Entity {
        let wrapper = HighlightableTargetEntity()
        wrapper.name = element.id

        let (w, h, d) = element.placement.boxSizeMeters
        let mesh = MeshResource.generateBox(size: SIMD3<Float>(w, h, d), cornerRadius: 0.015)

        let body = ModelEntity(mesh: mesh, materials: [HighlightMaterialFactory.translucentBlue()])
        body.position.y = h / 2
        wrapper.body = body
        wrapper.addChild(body)

        let halo = ModelEntity(
            mesh: .generateBox(size: SIMD3<Float>(w * 1.25, h * 1.25, d * 1.25), cornerRadius: 0.025),
            materials: [HighlightMaterialFactory.halo()],
        )
        halo.position.y = h / 2
        halo.isEnabled = false
        wrapper.halo = halo
        wrapper.addChild(halo)

        // Floating title.
        let label = LabelEntity.make(
            text: element.title,
            widthMeters: max(w * 1.4, 0.5),
            heightMeters: 0.1,
            bgColor: UIColor(red: 0.12, green: 0.23, blue: 0.43, alpha: 0.9),
        )
        label.position = SIMD3<Float>(0, h + 0.15, 0)
        wrapper.addChild(label)
        return wrapper
    }
}

/// Concrete entity that exposes a Highlightable interface to the coordinator.
final class HighlightableTargetEntity: Entity, Highlightable {
    var body: ModelEntity?
    var halo: ModelEntity?

    func setHighlighted(_ on: Bool) {
        halo?.isEnabled = on
        // Boost emissive intensity when on.
        guard let body = body else { return }
        if var mat = body.model?.materials.first as? PhysicallyBasedMaterial {
            mat.emissiveIntensity = on ? 1.4 : 0.6
            body.model?.materials = [mat]
        }
    }

    // SPATAIL_NEEDS_MAC_BUILD_VERIFY: Entity subclasses must provide
    // `required init()`. Xcode 16+ may require `@MainActor` annotation
    // on the class (RealityKit pushed Entity to MainActor isolation).
    // If the build complains, add `@MainActor` before `final class`.
    required init() { super.init() }
}
