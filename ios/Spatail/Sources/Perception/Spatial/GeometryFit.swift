// GeometryFit.swift — the pure spatial math of the measurement path (spec §3).
// OrientedBoxFitter: world points → gravity-aligned OrientedBox (XZ min-area
// rectangle via rotating-calipers-lite over the convex hull + Y extent).
// SupportSurfaceLinker: OBB → the RoomSurface it sits on.
// Foundation + simd ONLY — no ARKit/RealityKit/UIKit — so every branch runs in an
// off-device test harness exactly as it runs on the phone.

import Foundation
import CoreGraphics
import simd

// MARK: - Oriented-box fitting (pure math, testable off-device)

enum OrientedBoxFitter {

    /// Extent floor so degenerate clouds still produce a usable, matchable box.
    private static let minExtent: Float = 0.02

    /// Fit an OrientedBox about gravity-aligned +Y: XZ min-area rectangle
    /// (rotating-calipers-lite over the convex hull of the XZ projections) + Y extent.
    static func fit(points: [SIMD3<Float>]) -> OrientedBox? {
        guard !points.isEmpty else { return nil }

        var minY = Float.greatestFiniteMagnitude
        var maxY = -Float.greatestFiniteMagnitude
        for p in points {
            minY = min(minY, p.y)
            maxY = max(maxY, p.y)
        }

        let xz = points.map { SIMD2<Float>($0.x, $0.z) }
        let hull = convexHull(xz)

        // Degenerate clouds (1–2 unique points / collinear) → axis-aligned box.
        guard hull.count >= 3 else {
            var lo = SIMD2<Float>(repeating: .greatestFiniteMagnitude)
            var hi = SIMD2<Float>(repeating: -.greatestFiniteMagnitude)
            for p in xz {
                lo = simd_min(lo, p)
                hi = simd_max(hi, p)
            }
            let center2 = (lo + hi) / 2
            return OrientedBox(
                center: SIMD3(center2.x, (minY + maxY) / 2, center2.y),
                extents: SIMD3(max(hi.x - lo.x, minExtent),
                               max(maxY - minY, minExtent),
                               max(hi.y - lo.y, minExtent)),
                yaw: 0)
        }

        // Rotating-calipers-lite: the min-area rectangle has an edge collinear with
        // some hull edge — try each hull edge's direction, take the smallest AABB.
        var bestArea = Float.greatestFiniteMagnitude
        var bestPhi: Float = 0
        var bestLo = SIMD2<Float>.zero
        var bestHi = SIMD2<Float>.zero

        for i in 0..<hull.count {
            let a = hull[i]
            let b = hull[(i + 1) % hull.count]
            let edge = b - a
            guard simd_length(edge) > 1e-6 else { continue }
            let phi = atan2(edge.y, edge.x)
            let u = SIMD2<Float>(cos(phi), sin(phi))      // along the edge
            let w = SIMD2<Float>(-sin(phi), cos(phi))     // perpendicular

            var lo = SIMD2<Float>(repeating: .greatestFiniteMagnitude)
            var hi = SIMD2<Float>(repeating: -.greatestFiniteMagnitude)
            for p in hull {
                let q = SIMD2<Float>(simd_dot(p, u), simd_dot(p, w))
                lo = simd_min(lo, q)
                hi = simd_max(hi, q)
            }
            let area = (hi.x - lo.x) * (hi.y - lo.y)
            if area < bestArea {
                bestArea = area
                bestPhi = phi
                bestLo = lo
                bestHi = hi
            }
        }

        let u = SIMD2<Float>(cos(bestPhi), sin(bestPhi))
        let w = SIMD2<Float>(-sin(bestPhi), cos(bestPhi))
        let mid = (bestLo + bestHi) / 2
        let center2 = mid.x * u + mid.y * w

        // Rotation about +Y maps local X (1,0,0) → (cos yaw, 0, -sin yaw); the fitted
        // rectangle's local X in (x,z) is (cos phi, sin phi) → yaw = -phi.
        var yaw = -bestPhi
        // Rectangles are π-symmetric; normalize to (-π/2, π/2].
        while yaw > .pi / 2 { yaw -= .pi }
        while yaw <= -.pi / 2 { yaw += .pi }

        return OrientedBox(
            center: SIMD3(center2.x, (minY + maxY) / 2, center2.y),
            extents: SIMD3(max(bestHi.x - bestLo.x, minExtent),
                           max(maxY - minY, minExtent),
                           max(bestHi.y - bestLo.y, minExtent)),
            yaw: yaw)
    }

    /// Andrew's monotone-chain convex hull, counter-clockwise, no duplicate endpoint.
    static func convexHull(_ input: [SIMD2<Float>]) -> [SIMD2<Float>] {
        // Deduplicate near-identical points so collinear/degenerate sets fall out.
        var pts = input.sorted { $0.x == $1.x ? $0.y < $1.y : $0.x < $1.x }
        var unique: [SIMD2<Float>] = []
        for p in pts where unique.last.map({ simd_distance($0, p) > 1e-6 }) ?? true {
            unique.append(p)
        }
        pts = unique
        guard pts.count >= 3 else { return pts }

        func cross(_ o: SIMD2<Float>, _ a: SIMD2<Float>, _ b: SIMD2<Float>) -> Float {
            (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x)
        }

        var lower: [SIMD2<Float>] = []
        for p in pts {
            while lower.count >= 2,
                  cross(lower[lower.count - 2], lower[lower.count - 1], p) <= 0 {
                lower.removeLast()
            }
            lower.append(p)
        }
        var upper: [SIMD2<Float>] = []
        for p in pts.reversed() {
            while upper.count >= 2,
                  cross(upper[upper.count - 2], upper[upper.count - 1], p) <= 0 {
                upper.removeLast()
            }
            upper.append(p)
        }
        lower.removeLast()
        upper.removeLast()
        let hull = lower + upper
        return hull.count >= 3 ? hull : pts
    }
}

// MARK: - Support-surface linking (spec §3, last bullet)

enum SupportSurfaceLinker {

    /// "An object's supportSurfaceId is the surface whose top plane is within 4 cm
    /// below the OBB's bottom face and overlaps it in XZ."
    private static let maxGapBelow: Float = 0.04
    /// Small tolerance the other way for depth noise (OBB bottom slightly below top).
    private static let maxPenetration: Float = 0.02

    static func link(obb: OrientedBox, surfaces: [RoomSurface]) -> String? {
        let bottom = obb.bottomY
        var best: (id: String, gap: Float)?

        for surface in surfaces where isHorizontal(surface) {
            let gap = bottom - surface.y            // + = surface below the bottom face
            guard gap <= maxGapBelow, gap >= -maxPenetration else { continue }
            guard overlapsXZ(obb: obb, surface: surface) else { continue }
            if best == nil || abs(gap) < abs(best!.gap) {
                best = (surface.id, gap)
            }
        }
        return best?.id
    }

    /// Horizontal = a kind that has a meaningful top plane, or an unknown whose
    /// boundary is flat at the surface's Y.
    private static func isHorizontal(_ s: RoomSurface) -> Bool {
        switch s.kind {
        case .floor, .table, .seat: return true
        case .wall, .door, .window: return false
        case .ceiling: return false
        case .unknown:
            guard !s.boundary.isEmpty else { return false }
            return s.boundary.allSatisfy { abs($0.y - s.y) < 0.08 }
        }
    }

    /// XZ overlap test: the OBB's center or any bottom corner inside the surface's
    /// boundary polygon (concave allowed).
    private static func overlapsXZ(obb: OrientedBox, surface: RoomSurface) -> Bool {
        let polygon = surface.boundary.map { SIMD2<Float>($0.x, $0.z) }
        guard polygon.count >= 3 else { return false }

        var probes = [SIMD2<Float>(obb.center.x, obb.center.z)]
        let hx = obb.extents.x / 2, hz = obb.extents.z / 2
        let c = cos(obb.yaw), s = sin(obb.yaw)
        for (lx, lz) in [(hx, hz), (hx, -hz), (-hx, hz), (-hx, -hz)] {
            // R_y(yaw): world = (c*lx + s*lz, …, -s*lx + c*lz) + center
            probes.append(SIMD2<Float>(obb.center.x + c * lx + s * lz,
                                       obb.center.z - s * lx + c * lz))
        }
        return probes.contains { contains(polygon: polygon, point: $0) }
    }

    /// Even-odd ray-casting point-in-polygon.
    private static func contains(polygon: [SIMD2<Float>], point: SIMD2<Float>) -> Bool {
        var inside = false
        var j = polygon.count - 1
        for i in 0..<polygon.count {
            let pi = polygon[i], pj = polygon[j]
            if (pi.y > point.y) != (pj.y > point.y),
               point.x < (pj.x - pi.x) * (point.y - pi.y) / (pj.y - pi.y) + pi.x {
                inside.toggle()
            }
            j = i
        }
        return inside
    }
}
