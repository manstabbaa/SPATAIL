// RoomModel.swift — the placement solver's INPUT (mirrors studio/spatail/room_model.py).
// A compact semantic description of the user's space, distilled from the live scanner's
// RoomSurfaces + the ObjectRegistry's measured objects, consumed by PlacementSolver.
// NOT a mesh. Pure math — no ARKit/RealityKit here, so the same model is loadable from
// the parity fixtures (studio/spatail/tests) in an XCTest.
//
// Frame: metres, +Y up. Surfaces/obstacles arrive in ARKit WORLD space; the solver's
// arc math runs in the user frame (user at origin looking -Z) and PlacementPlanner
// pins the result back onto the real surfaces — same split the legacy app proved.

import Foundation
import simd

struct RoomModel {
    struct Surface {
        var cls: String                 // floor | table | wall | ceiling | seat | unknown
        var center: SIMD3<Float>
        var size: SIMD2<Float>          // extent along the surface (w, d)
        var normal: SIMD3<Float>
        var yaw: Float
        var height: Float               // world Y of the surface plane
        var confidence: Float
    }

    struct Obstacle {
        var bboxMin: SIMD3<Float>
        var bboxMax: SIMD3<Float>
        var category: String
    }

    struct Light {
        var intensity: Float            // ARLightEstimate.ambientIntensity (lumens)
        var colorTempK: Float
        var keyDirection: SIMD3<Float>
    }

    var surfaces: [Surface] = []
    var obstacles: [Obstacle] = []
    var user = (position: SIMD3<Float>(repeating: 0),
                forward: SIMD3<Float>(0, 0, -1),
                eyeHeight: Float(1.45))
    var light = Light(intensity: 1000, colorTempK: 6500, keyDirection: [0, -1, 0])
    var bounds = SIMD3<Float>(4, 3, 2.6)
    var source = "default"

    func surfaces(of cls: String) -> [Surface] { surfaces.filter { $0.cls == cls } }

    /// The table the USER is engaging with — not the biggest slab in the room.
    /// Gate out implausible "tables" first (classification sometimes labels floor-level
    /// planes .table), then prefer near + in-front of the user (§6: centre on the
    /// detected table, main content in front of the user); area is only a tiebreak.
    func bestTable() -> Surface? {
        let floorH = floor()?.height
        let plausible = surfaces(of: "table").filter { t in
            let above = floorH.map { t.height - $0 } ?? 0.7   // no floor yet → trust the label
            return above > 0.25 && above < 1.3 && t.size.x * t.size.y > 0.05
        }
        func score(_ t: Surface) -> Float {
            let to = SIMD3(t.center.x - user.position.x, 0, t.center.z - user.position.z)
            let d = simd_length(to)
            let behind: Float = d > 0.3 && simd_dot(to / d, user.forward) < 0 ? 2.0 : 0
            return d + behind - min(t.size.x * t.size.y, 1.0) * 0.3
        }
        return plausible.min { score($0) < score($1) }
    }

    func floor() -> Surface? {
        surfaces(of: "floor").max { $0.size.x * $0.size.y < $1.size.x * $1.size.y }
    }
}

// MARK: - Adapter: Core types → solver model

extension RoomModel {
    /// Distill the live app state into the solver's model:
    ///   • `RoomSurface`s (scanner) → semantic surfaces (boundary polygon → XZ extent)
    ///   • `SpatailObject`s (registry) → keep-out obstacle AABBs (yawed OBB → AABB)
    ///   • the camera pose → the user
    init(surfaces roomSurfaces: [RoomSurface],
         objects: [SpatailObject],
         userPosition: SIMD3<Float>,
         userForward: SIMD3<Float>,
         source: String = "arkit-live") {
        self.init()
        self.source = source

        let flat = SIMD3(userForward.x, 0, userForward.z)
        self.user = (position: userPosition,
                     forward: simd_length(flat) > 1e-4 ? simd_normalize(flat) : SIMD3(0, 0, -1),
                     eyeHeight: max(1.2, userPosition.y > 0.1 ? userPosition.y : 1.45))

        var minX = Float.greatestFiniteMagnitude, maxX = -Float.greatestFiniteMagnitude
        var minZ = Float.greatestFiniteMagnitude, maxZ = -Float.greatestFiniteMagnitude

        for s in roomSurfaces {
            guard !s.boundary.isEmpty else { continue }
            var lo = SIMD3<Float>(repeating: .greatestFiniteMagnitude)
            var hi = SIMD3<Float>(repeating: -.greatestFiniteMagnitude)
            for p in s.boundary {
                lo = simd_min(lo, p); hi = simd_max(hi, p)
            }
            let centerXZ = (lo + hi) / 2
            let isVertical = (s.kind == .wall || s.kind == .door || s.kind == .window)
            // Horizontal surfaces: extent is the XZ span. Vertical: width is the larger
            // horizontal span, depth is the height span (the solver only reads .x today).
            let size: SIMD2<Float> = isVertical
                ? SIMD2(max(hi.x - lo.x, hi.z - lo.z), hi.y - lo.y)
                : SIMD2(hi.x - lo.x, hi.z - lo.z)
            surfaces.append(Surface(
                cls: s.kind.rawValue,
                center: SIMD3(centerXZ.x, s.y, centerXZ.z),
                size: size,
                normal: isVertical ? SIMD3(0, 0, 1) : SIMD3(0, 1, 0),
                yaw: 0,
                height: s.y,
                confidence: s.confidence))
            minX = min(minX, lo.x); maxX = max(maxX, hi.x)
            minZ = min(minZ, lo.z); maxZ = max(maxZ, hi.z)
        }

        // Measured real objects are the keep-out volumes (the registry already fused
        // depth + identity — far better than raw mesh chunks). Conservative AABB of
        // the yawed OBB so a diagonal object still blocks its full footprint.
        for o in objects {
            let obb = o.obb
            let c = cos(obb.yaw), s = sin(obb.yaw)
            let hx = (abs(c) * obb.extents.x + abs(s) * obb.extents.z) / 2
            let hz = (abs(s) * obb.extents.x + abs(c) * obb.extents.z) / 2
            let hy = obb.extents.y / 2
            obstacles.append(Obstacle(
                bboxMin: SIMD3(obb.center.x - hx, obb.center.y - hy, obb.center.z - hz),
                bboxMax: SIMD3(obb.center.x + hx, obb.center.y + hy, obb.center.z + hz),
                category: o.label ?? "object"))
        }

        if maxX > minX, maxZ > minZ {
            bounds = SIMD3(max(4, maxX - minX), max(3, maxZ - minZ), 2.6)
        }
    }
}
