import Foundation
import simd

// RoomModel — the SPATAIL placement solver's INPUT on device (mirrors
// studio/spatail/room_model.py). A compact semantic description of the user's
// space, built on-device from ARKit and consumed by PlacementSolver. NOT a mesh.
//
// Frame: metres, +Y up, user near origin looking -Z (the AR convention).

struct RoomModel {
    struct Surface {
        var cls: String                 // floor | table | wall | ceiling | seat | unknown
        var center: SIMD3<Float>
        var size: SIMD2<Float>          // extent along the surface (w, d)
        var normal: SIMD3<Float>
        var yaw: Float
        var height: Float               // height above floor (table ≈ 0.74)
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
    var user = (position: SIMD3<Float>(repeating: 0), forward: SIMD3<Float>(0, 0, -1), eyeHeight: Float(1.45))
    var light = Light(intensity: 1000, colorTempK: 6500, keyDirection: [0, -1, 0])
    var bounds = SIMD3<Float>(4, 3, 2.6)
    var source = "default"

    func surfaces(of cls: String) -> [Surface] { surfaces.filter { $0.cls == cls } }
    /// The table the USER is engaging with — not the biggest slab in the room.
    /// Gate out implausible "tables" first (ARKit's ML sometimes labels floor-level
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

#if os(iOS)
import ARKit

// Accumulates ARKit readings over the session and distills a RoomModel — the
// on-device twin of analysis.py's plane→RoomProfile path, upgraded to carry
// semantic plane classification + light. (Raw LiDAR-mesh occlusion/obstacles are
// a later refinement; planes + classification already give a solid model.)
@MainActor
final class RoomModelBuilder {
    private var planes: [UUID: ARPlaneAnchor] = [:]
    private var light = RoomModel.Light(intensity: 1000, colorTempK: 6500, keyDirection: [0, -1, 0])
    private var userPos = SIMD3<Float>(repeating: 0)
    private var userFwd = SIMD3<Float>(0, 0, -1)

    func update(planes anchors: [ARPlaneAnchor]) { for a in anchors { planes[a.identifier] = a } }
    func remove(_ ids: [UUID]) { for id in ids { planes[id] = nil } }

    func update(light est: ARLightEstimate?) {
        guard let e = est else { return }
        light = RoomModel.Light(intensity: Float(e.ambientIntensity),
                                colorTempK: Float(e.ambientColorTemperature),
                                keyDirection: [0, -1, 0])
    }

    func update(cameraTransform t: simd_float4x4) {
        userPos = SIMD3(t.columns.3.x, t.columns.3.y, t.columns.3.z)
        let f = -SIMD3(t.columns.2.x, t.columns.2.y, t.columns.2.z)   // -Z is forward
        let flat = SIMD3(f.x, 0, f.z)
        userFwd = simd_length(flat) > 1e-4 ? simd_normalize(flat) : SIMD3(0, 0, -1)
    }

    private static func classify(_ p: ARPlaneAnchor, floorY: Float, cameraY: Float) -> String {
        switch p.classification {
        case .floor:   return "floor"
        case .table:   return "table"
        case .wall:    return "wall"
        case .ceiling: return "ceiling"
        case .seat:    return "seat"
        default:
            // unclassified: infer from height ABOVE THE FLOOR. ARKit world y=0 is the
            // session-start phone pose (~1.4 m up), so an absolute threshold labels
            // every table "floor" and the solver shoves content past the real table.
            if p.alignment == .horizontal {
                let y = p.transform.columns.3.y
                if y > cameraY + 0.5 { return "ceiling" }
                return y - floorY < 0.35 ? "floor" : "table"
            }
            return "wall"
        }
    }

    func build() -> RoomModel {
        var rm = RoomModel()
        rm.source = "arkit"
        rm.light = light
        rm.user = (position: userPos, forward: userFwd,
                   eyeHeight: max(Float(1.2), userPos.y > 0.1 ? userPos.y : Float(1.45)))
        var minX: Float = 0, maxX: Float = 0, minZ: Float = 0, maxZ: Float = 0
        // floor reference for the height heuristic: the lowest horizontal plane seen,
        // or the camera's typical handheld height above the floor when none is lower
        // (a lone table plane must not become its own "floor")
        let lowestHorizontal = planes.values
            .filter { $0.alignment == .horizontal }
            .map { $0.transform.columns.3.y }.min() ?? .greatestFiniteMagnitude
        let floorY = lowestHorizontal <= userPos.y - 1.0 ? lowestHorizontal : userPos.y - 1.4
        for (_, p) in planes {
            let c = p.transform.columns.3
            let ext = p.planeExtent
            rm.surfaces.append(RoomModel.Surface(
                cls: Self.classify(p, floorY: floorY, cameraY: userPos.y),
                center: SIMD3(c.x, c.y, c.z),
                size: SIMD2(ext.width, ext.height),
                normal: SIMD3(0, 1, 0),
                yaw: ext.rotationOnYAxis,
                height: c.y,
                confidence: 1))
            minX = min(minX, c.x - ext.width / 2);  maxX = max(maxX, c.x + ext.width / 2)
            minZ = min(minZ, c.z - ext.height / 2); maxZ = max(maxZ, c.z + ext.height / 2)
        }
        rm.bounds = SIMD3(max(4, maxX - minX), max(3, maxZ - minZ), 2.6)
        return rm
    }
}
#endif
