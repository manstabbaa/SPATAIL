// SpatialPanelBuilder.swift
//
// Builds a textured plane Entity for any 2D-panel-style element:
// two_d_panel, floating_decision_card, diagnostic_overlay,
// anchored_callout. The wall_dashboard variant uses WallPanelBuilder
// for the larger size.
//
// Output is always an Entity with a single ModelEntity child carrying
// the panel material — that wrapping makes future "child overlays"
// (selection rings, highlight halos) trivial to add.

import Foundation
import RealityKit

struct SpatialPanelBuilder {
    func build(for element: SpatialElement, style: PanelStyle = .standard) -> Entity {
        let group = Entity()
        let (w, h) = element.placement.planeSizeMeters

        let mesh = MeshResource.generatePlane(width: w, height: h)
        let material = makePanelMaterial(for: element, w: w, h: h, style: style)
        let panel = ModelEntity(mesh: mesh, materials: [material])

        // Panels are rendered double-sided by giving them a sibling
        // back face — RealityKit's PhysicallyBasedMaterial supports
        // .twoSided faceCulling on iOS 17+, but to stay iOS 16
        // compatible we attach a back twin rotated 180°.
        let back = panel.clone(recursive: false)
        back.transform.rotation = simd_quatf(angle: .pi, axis: SIMD3<Float>(0, 1, 0))

        group.addChild(panel)
        group.addChild(back)
        return group
    }

    private func makePanelMaterial(for element: SpatialElement,
                                   w: Float, h: Float,
                                   style: PanelStyle) -> RealityKit.Material {
        let textureResource = PanelTextureRenderer().renderTexture(
            for: element, widthMeters: w, heightMeters: h, style: style,
        )
        var material = UnlitMaterial()
        if let tex = textureResource {
            // SPATAIL_NEEDS_MAC_BUILD_VERIFY: MaterialParameters.Texture(tex)
            // is the positional init available iOS 15+. Stable in 16/17.
            material.color = .init(tint: .white,
                                   texture: MaterialParameters.Texture(tex))
        } else {
            // Fallback: solid accent if rasterization failed.
            material.color = .init(tint: .init(red: 0.43, green: 0.66, blue: 1.0, alpha: 1.0))
        }
        material.blending = .transparent(opacity: .init(scale: 1.0))
        return material
    }
}
