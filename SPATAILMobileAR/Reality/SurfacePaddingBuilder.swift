// SurfacePaddingBuilder.swift
//
// Renders the fusion brain's surface-relative placements — the output of
// surface_placement.js — as real geometry hugging a scanned surface:
//   • surface_edge   → N soft "foam strip" segments laid along the edge
//                      (the segment COUNT visualizes "how much you need")
//   • surface_corner → a rounded corner guard seated on the corner vertex
//
// Both are drawn in a translucent "foam ghost" material because the element
// ships at fidelity `ghost` — a proposal the user can accept/move, not a
// committed object. Local-space geometry, so ARSceneRenderer's transform
// (element.placement.position) drops it onto the real surface.

import Foundation
import RealityKit
import UIKit
import simd

@MainActor
struct SurfacePaddingBuilder {
    /// Soft mint "foam ghost" — reads as padding, and translucent because the
    /// element is an un-committed proposal.
    static let foamColor = UIColor(red: 0.42, green: 0.85, blue: 0.74, alpha: 0.60)

    func build(for element: SpatialElement) -> Entity {
        switch element.placementKindEnum {
        case .surface_corner: return cornerGuard(for: element)
        default:              return edgeStrips(for: element)   // surface_edge
        }
    }

    // MARK: - Edge: N foam strips along from→to

    private func edgeStrips(for element: SpatialElement) -> Entity {
        let group = Entity()
        let p = element.placement
        guard let f = p.simdFrom, let t = p.simdTo,
              let midArr = p.position, midArr.count >= 3 else { return group }

        // ARSceneRenderer positions the group at the edge midpoint, so build the
        // strip in local space relative to that midpoint.
        let mid = SIMD3<Float>(Float(midArr[0]), Float(midArr[1]), Float(midArr[2]))
        let a = f - mid
        let b = t - mid
        let diff = b - a
        let length = simd_length(diff)
        guard length > 1e-4 else { return group }
        let dir = diff / length

        // Cross-section = edge depth (sizeMeters == [span, depth, depth]).
        let s = p.sizeMeters ?? []
        let depth: Float = s.count > 1 ? Float(s[1]) : 0.04

        // One box per foam strip, with a small gap between them so the COUNT is
        // legible ("6 strips" is visibly six things).
        let count = max(1, p.count ?? Int((length / 0.5).rounded(.up)))
        let gap: Float = 0.02
        let segLen = (length - gap * Float(max(0, count - 1))) / Float(count)
        guard segLen > 1e-4 else { return group }

        for i in 0..<count {
            let start = Float(i) * (segLen + gap)
            let center = a + dir * (start + segLen / 2)
            // Fresh material per entity to satisfy Swift 6 region isolation
            // (a non-Sendable material can't cross entities in a loop).
            let mat = SimpleMaterial(color: Self.foamColor, roughness: 0.95, isMetallic: false)
            let seg = ModelEntity(
                mesh: .generateBox(size: SIMD3<Float>(depth, segLen, depth),
                                   cornerRadius: depth * 0.45),
                materials: [mat])
            seg.position = center
            seg.orientation = simd_quatf(from: SIMD3<Float>(0, 1, 0), to: dir)
            group.addChild(seg)
        }
        return group
    }

    // MARK: - Corner: a rounded guard on the vertex

    private func cornerGuard(for element: SpatialElement) -> Entity {
        let s = element.placement.sizeMeters ?? []
        let size = max(0.02, Float(s.first ?? 0.08))
        let mat = SimpleMaterial(color: Self.foamColor, roughness: 0.95, isMetallic: false)
        // Positioned at placement.position (the corner vertex) by ARSceneRenderer.
        return ModelEntity(
            mesh: .generateBox(size: SIMD3<Float>(repeating: size), cornerRadius: size * 0.4),
            materials: [mat])
    }
}
