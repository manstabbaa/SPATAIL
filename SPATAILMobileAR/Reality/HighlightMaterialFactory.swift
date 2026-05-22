// HighlightMaterialFactory.swift
//
// Materials for the "highlighted target" — the blue translucent
// material the spec calls out for the air filter housing. Centralised
// so the look of "this is the part we're working on" is consistent
// everywhere.

import Foundation
import RealityKit
import UIKit

enum HighlightMaterialFactory {
    /// The base translucent-blue material used for a highlighted physical target.
    static func translucentBlue() -> RealityKit.Material {
        var m = PhysicallyBasedMaterial()
        m.baseColor = .init(tint: UIColor(red: 0.43, green: 0.66, blue: 1.0, alpha: 0.85))
        m.roughness = .init(floatLiteral: 0.35)
        m.metallic  = .init(floatLiteral: 0.2)
        m.emissiveColor = .init(color: UIColor(red: 0.12, green: 0.23, blue: 0.43, alpha: 1.0))
        m.emissiveIntensity = 0.6
        // SPATAIL_NEEDS_MAC_BUILD_VERIFY: PhysicallyBasedMaterial.Opacity
        // initializer variants. `init(floatLiteral:)` is ExpressibleByFloatLiteral;
        // if Xcode rejects, try plain `0.85` literal or `.init(scale: 0.85)`.
        m.blending = .transparent(opacity: .init(floatLiteral: 0.85))
        return m
    }

    /// Halo material for a slightly larger box that wraps the target,
    /// producing a glow without compositor work.
    static func halo() -> RealityKit.Material {
        var m = UnlitMaterial()
        m.color = .init(tint: UIColor(red: 0.43, green: 0.66, blue: 1.0, alpha: 0.18))
        // SPATAIL_NEEDS_MAC_BUILD_VERIFY: UnlitMaterial.Blending uses the
        // same PhysicallyBasedMaterial.Opacity type. If `.init(scale:)`
        // doesn't compile, fall back to `.transparent(opacity: 1.0)`.
        m.blending = .transparent(opacity: .init(scale: 1.0))
        return m
    }
}
