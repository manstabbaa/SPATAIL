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
    func bestTable() -> Surface? {
        surfaces(of: "table").max { $0.size.x * $0.size.y < $1.size.x * $1.size.y }
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

    private static func classify(_ p: ARPlaneAnchor) -> String {
        switch p.classification {
        case .floor:   return "floor"
        case .table:   return "table"
        case .wall:    return "wall"
        case .ceiling: return "ceiling"
        case .seat:    return "seat"
        default:
            // unclassified: infer from alignment + height (the current heuristic)
            if p.alignment == .horizontal {
                return p.transform.columns.3.y < 0.3 ? "floor" : "table"
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
        for (_, p) in planes {
            let c = p.transform.columns.3
            let ext = p.planeExtent
            rm.surfaces.append(RoomModel.Surface(
                cls: Self.classify(p),
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
