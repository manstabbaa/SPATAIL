import Foundation
import simd

// PlacementSolver — the SPATAIL Placement Design System on device. Direct port of
// the verified studio/spatail/placement_solver.py, so what authoring previews is
// what the phone places. Pure math over a RoomModel; no ARKit/RealityKit here.
//
// Frame: metres, +Y up, user near origin looking -Z.

struct PlacementSolver {
    // comfort constants mirror studio/xr_design.py (single source of truth)
    static let readNear: Float = 1.0
    static let readFar: Float = 1.5
    static let coneDeg: Float = 30.0

    struct AssetReq {
        let id: String
        let footprint: SIMD3<Float>     // true authored size (w, h, d) metres
        let role: String
    }

    struct Placement {
        let assetId: String
        let position: SIMD3<Float>
        let yaw: Float
        let scale: Float
        let anchor: String              // table | floor
        let zone: String                // hero | secondary | peripheral
        let fits: Bool
    }

    struct Plan {
        let placements: [Placement]
        let anchor: String
        let scaleVariant: String        // tabletop | real
    }

    // n slots on a horizontal arc curved toward the user, centre-out ordered so the
    // hero (index 0) lands dead ahead. Returns (position, yaw) per slot.
    private static func arc(_ n: Int, distance: Float, height: Float,
                            spreadDeg: Float) -> [(SIMD3<Float>, Float)] {
        if n <= 0 { return [] }
        if n == 1 { return [(SIMD3(0, height, -distance), 0)] }
        let half = spreadDeg / 2
        var order = [0]
        for k in 1..<n { order.append((k + 1) / 2 * (k % 2 == 1 ? 1 : -1)) }
        let lo = order.min()!, hi = order.max()!
        return order.map { slot in
            let t = hi != lo ? Float(slot - lo) / Float(hi - lo) : 0.5
            let ang = (-half + t * spreadDeg) * .pi / 180
            let x = distance * sin(ang)
            let z = -distance * cos(ang)
            let yaw = atan2(x, distance)                 // turn to face the user
            return (SIMD3(x, height, z), yaw)
        }
    }

    static func solve(room: RoomModel, assets: [AssetReq],
                      anchorPreference: String = "table",
                      scaleMode: String = "dynamic",
                      primary: String? = nil,
                      coverage: Float = 0.8) -> Plan {   // design system §5: ≤60–70% of surface
        let n = assets.count
        guard n > 0 else { return Plan(placements: [], anchor: "table", scaleVariant: "tabletop") }
        let hero = primary ?? assets[0].id
        let ordered = assets.sorted { ($0.id == hero ? 0 : 1) < ($1.id == hero ? 0 : 1) }

        let table = room.bestTable()
        let floor = room.floor()
        let useTable = (anchorPreference == "table" || anchorPreference == "free")
            && table != nil && scaleMode != "real"

        let anchor: String, surfW: Float, surfD: Float, baseH: Float, distance: Float, variant: String
        if useTable, let t = table {
            anchor = "table"; surfW = t.size.x; surfD = t.size.y; baseH = t.height
            distance = max(readNear * 0.6, min(0.7, readNear)); variant = "tabletop"
        } else {
            anchor = "floor"
            surfW = floor?.size.x ?? room.bounds.x
            surfD = floor?.size.y ?? room.bounds.y
            baseH = 0
            distance = max(readFar, 1.2)
            variant = scaleMode == "real" ? "real" : "tabletop"
        }

        let widths = ordered.map { max($0.footprint.x, 0.03) }
        let gap: Float = 0.12
        let neededW = widths.reduce(0, +) + gap * Float(n - 1)
        let availW = max(0.2, surfW * min(max(coverage, 0.2), 0.95))
        let fitScale = neededW > 0 ? min(1.0, availW / neededW) : 1.0
        let scale: Float = variant == "real" ? 1.0 : fitScale

        let spread = min(coneDeg, 8.0 + 7.0 * Float(max(0, n - 1)))
        let slots = arc(n, distance: distance, height: baseH, spreadDeg: spread)

        // keep-out only against obstacles that REST ON the chosen surface — floor
        // furniture (a chair tucked under a table) is shielded by the tabletop, so it
        // must not block a table placement (mirrors the Blender placer's _on_support
        // gate). For a floor placement baseH = 0, so floor obstacles still count.
        let obstaclesXZ = room.obstacles
            .filter { $0.bboxMin.y >= baseH - 0.05 }
            .map { (SIMD2($0.bboxMin.x, $0.bboxMin.z), SIMD2($0.bboxMax.x, $0.bboxMax.z)) }

        var placements: [Placement] = []
        for (i, a) in ordered.enumerated() {
            let (pos, yaw) = slots[i]
            let zone = i == 0 ? "hero" : (i <= 2 ? "secondary" : "peripheral")
            let halfW = surfW / 2
            var fits = abs(pos.x) <= halfW && (variant != "real" || surfD >= distance)
            if obstaclesXZ.contains(where: { pair in
                pos.x >= pair.0.x && pos.x <= pair.1.x && pos.z >= pair.0.y && pos.z <= pair.1.y }) {
                fits = false
            }
            placements.append(Placement(assetId: a.id, position: pos, yaw: yaw, scale: scale,
                                        anchor: anchor, zone: zone, fits: fits))
        }
        return Plan(placements: placements, anchor: anchor, scaleVariant: variant)
    }
}
