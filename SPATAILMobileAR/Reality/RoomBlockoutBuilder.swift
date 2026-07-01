// RoomBlockoutBuilder.swift
//
// The "engine viewer" geometry: turns the scanner's classified surfaces into
// color-coded blockout slabs so a human can SEE what the camera/engine
// currently believes the room is made of — greybox-style, one color per
// SurfaceKind, with a small kind + m² label floating over each slab.
//
// One color mapping lives here and the SwiftUI legend reads the same mapping,
// so the AR view and the HUD can never drift apart.
//
// SPATAIL_NEEDS_MAC_BUILD_VERIFY: RealityKit mesh + text generation — authored
// against the existing builder patterns (GuideLineBuilder / SurfacePaddingBuilder).

import Foundation
import RealityKit
import UIKit
import simd

@MainActor
struct RoomBlockoutBuilder {

    /// The single source of truth for kind → color. The legend in
    /// EngineViewerView reads this same table.
    static func color(for kind: SurfaceKind) -> UIColor {
        switch kind {
        case .floor:   return UIColor.systemGreen.withAlphaComponent(0.42)
        case .ceiling: return UIColor.systemGray.withAlphaComponent(0.30)
        case .wall:    return UIColor.systemBlue.withAlphaComponent(0.35)
        case .table:   return UIColor.systemOrange.withAlphaComponent(0.55)
        case .seat:    return UIColor.systemPurple.withAlphaComponent(0.50)
        case .window:  return UIColor.systemCyan.withAlphaComponent(0.40)
        case .door:    return UIColor.systemYellow.withAlphaComponent(0.45)
        case .unknown: return UIColor.white.withAlphaComponent(0.20)
        }
    }

    /// Build one entity containing a blockout slab (+ label) per surface.
    /// All geometry is in WORLD coordinates — anchor the result at the world
    /// origin (the scan and the contract share that frame).
    func build(surfaces: [RoomSurface], labels: Bool = true) -> Entity {
        let root = Entity()
        root.name = "RoomBlockout"
        for surface in surfaces {
            let slab = slabEntity(for: surface)
            slab.name = "blockout:\(surface.id)"
            root.addChild(slab)
            if labels, surface.kind != .unknown {
                root.addChild(labelEntity(for: surface))
            }
        }
        return root
    }

    // MARK: - Slab

    private func slabEntity(for surface: RoomSurface) -> Entity {
        let pts = surface.polygon.compactMap { v -> SIMD3<Float>? in
            v.count >= 3 ? SIMD3(v[0], v[1], v[2]) : nil
        }
        guard pts.count >= 3 else { return Entity() }
        let centroid = pts.reduce(SIMD3<Float>(0, 0, 0), +) / Float(pts.count)
        let n = surface.simdNormal
        let horizontal = abs(n.y) > 0.7

        let mat = SimpleMaterial(color: Self.color(for: surface.kind),
                                 roughness: 1.0, isMetallic: false)

        if horizontal {
            // Horizontal surfaces (floor / ceiling / table / seat): a thin
            // slab over the polygon's XZ bounding box at the surface height.
            let xs = pts.map(\.x), zs = pts.map(\.z)
            let w = max(0.05, (xs.max() ?? 0) - (xs.min() ?? 0))
            let d = max(0.05, (zs.max() ?? 0) - (zs.min() ?? 0))
            let slab = ModelEntity(
                mesh: .generateBox(size: SIMD3<Float>(w, 0.03, d)),
                materials: [mat])
            slab.position = centroid
            return slab
        } else {
            // Vertical surfaces (wall / window / door): a thin panel whose
            // local +Z is rotated onto the surface normal; width spans the
            // in-plane horizontal run, height spans the polygon's Y run.
            let up = SIMD3<Float>(0, 1, 0)
            let right = simd_normalize(simd_cross(up, n))
            let widths = pts.map { simd_dot($0 - centroid, right) }
            let ys = pts.map(\.y)
            let w = max(0.05, (widths.max() ?? 0) - (widths.min() ?? 0))
            let h = max(0.05, (ys.max() ?? 0) - (ys.min() ?? 0))
            let slab = ModelEntity(
                mesh: .generateBox(size: SIMD3<Float>(w, h, 0.025)),
                materials: [mat])
            slab.position = centroid
            slab.orientation = simd_quatf(from: SIMD3<Float>(0, 0, 1), to: n)
            return slab
        }
    }

    // MARK: - Label ("table · 1.0 m²")

    private func labelEntity(for surface: RoomSurface) -> Entity {
        let text = String(format: "%@ · %.1f m²", surface.kind.rawValue, surface.area)
        let mesh = MeshResource.generateText(
            text,
            extrusionDepth: 0.001,
            font: .systemFont(ofSize: 0.055, weight: .semibold),
            containerFrame: .zero,
            alignment: .center,
            lineBreakMode: .byTruncatingTail)
        let solid = Self.color(for: surface.kind).withAlphaComponent(1.0)
        let entity = ModelEntity(
            mesh: mesh,
            materials: [UnlitMaterial(color: solid)])
        // Center the text mesh over the surface centroid, floated slightly up.
        let bounds = entity.visualBounds(relativeTo: nil)
        var pos = surface.simdCentroid
        pos.y += surface.kind == .wall ? 0.0 : 0.10
        pos.x -= bounds.extents.x / 2
        entity.position = pos
        return entity
    }
}
