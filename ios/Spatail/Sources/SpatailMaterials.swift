import Foundation
#if os(iOS)
import RealityKit
import UIKit

/// SpatailMaterials — the on-device "master material" effects layer (Phase 1).
///
/// WHY THIS EXISTS: USDZ flattens every Blender material down to UsdPreviewSurface,
/// which RealityKit loads as `PhysicallyBasedMaterial`. We cannot ship a fancy
/// Blender shader graph to the phone, so runtime effects (highlight, glow, …) must
/// mutate the ALREADY-LOADED material here in Swift.
///
/// These helpers are NON-DESTRUCTIVE: they keep the part's authored look (baseColor,
/// roughness, metallic — the clay finish from spatail_style.py) and only add an
/// effect on top — e.g. raise the emissive channel so a highlighted part GLOWS
/// instead of being clobbered by a flat `SimpleMaterial`.
///
/// TEXTURE-PACK SAFE (Meshy): a Meshy asset arrives as a textured PBR (albedo / normal /
/// roughness-metallic / emissive maps baked into the USDZ). Every effect mutates the
/// material's channels IN PLACE — `baseColor.tint`, `emissiveColor.color` — so the
/// imported MAPS are preserved (tint MULTIPLIES the albedo texture rather than replacing
/// it). The same effect therefore works identically on a flat authored part and a fully
/// textured Meshy part.
///
/// Each effect returns a NEW material value; callers keep the originals to restore.
enum SpatailMaterials {

    /// The SPATAIL highlight accent — matched to the marker/callout teal so the
    /// glow, the pulsing dot and the leader line read as one "this part" signal.
    static let accent = UIColor.systemTeal

    /// Non-destructive highlight: keep the surface, add an emissive glow.
    ///
    /// - For a `PhysicallyBasedMaterial` (the usual case from a loaded USDZ, incl. a
    ///   textured Meshy asset) we copy it and raise only the emissive channel in place —
    ///   albedo/roughness/metallic AND all their texture maps survive.
    /// - For a `SimpleMaterial` (placeholder boxes) we rebuild a PBR from its tint so
    ///   it can glow too, since `SimpleMaterial` has no emissive input.
    /// - Anything else (e.g. `UnlitMaterial` UI) is returned unchanged.
    static func highlighted(_ source: any RealityKit.Material,
                            color: UIColor = accent,
                            intensity: Float = 0.6) -> any RealityKit.Material {
        if var pbr = source as? PhysicallyBasedMaterial {
            pbr.emissiveColor.color = color   // in place: keeps any emissive texture
            pbr.emissiveIntensity = intensity
            return pbr
        }
        if let simple = source as? SimpleMaterial {
            var pbr = PhysicallyBasedMaterial()
            pbr.baseColor = .init(tint: simple.color.tint)
            pbr.roughness = 0.55
            pbr.metallic = 0.0
            pbr.emissiveColor = .init(color: color)
            pbr.emissiveIntensity = intensity
            return pbr
        }
        return source
    }

    /// "Active / energized / powered": a strong self-illumination glow. Same machinery
    /// as `highlighted`, just brighter so the part reads as lit from within.
    static func emissive(_ source: any RealityKit.Material,
                         color: UIColor = accent,
                         intensity: Float = 1.8) -> any RealityKit.Material {
        highlighted(source, color: color, intensity: intensity)
    }

    /// "State colour": recolour the surface (hot/cold/charged) while keeping the finish,
    /// plus a faint matching glow so the colour reads against the AR backdrop.
    static func tinted(_ source: any RealityKit.Material,
                       color: UIColor) -> any RealityKit.Material {
        if var pbr = source as? PhysicallyBasedMaterial {
            pbr.baseColor.tint = color        // in place: tint MULTIPLIES the albedo texture
            pbr.emissiveColor.color = color   // keeps any emissive texture
            pbr.emissiveIntensity = 0.25
            return pbr
        }
        if source is SimpleMaterial {
            var pbr = PhysicallyBasedMaterial()
            pbr.baseColor = .init(tint: color)
            pbr.roughness = 0.55
            pbr.metallic = 0.0
            pbr.emissiveColor = .init(color: color)
            pbr.emissiveIntensity = 0.25
            return pbr
        }
        return source
    }

    /// Parse "#RRGGBB" (or "RRGGBB") into a UIColor; nil if malformed.
    static func color(fromHex raw: String) -> UIColor? {
        var s = raw.trimmingCharacters(in: .whitespaces)
        if s.hasPrefix("#") { s.removeFirst() }
        guard s.count == 6, let v = UInt32(s, radix: 16) else { return nil }
        return UIColor(red:   CGFloat((v >> 16) & 0xFF) / 255,
                       green: CGFloat((v >> 8) & 0xFF) / 255,
                       blue:  CGFloat(v & 0xFF) / 255,
                       alpha: 1)
    }
}
#endif
