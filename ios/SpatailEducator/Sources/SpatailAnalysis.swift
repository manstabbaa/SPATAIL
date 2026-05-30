import Foundation
import simd

// Swift port of studio/spatail/analysis.py — the SAME spatial brain, so the
// scale/placement the phone chooses matches what the studio previews on Windows.
// "Show me your space → tell me what you want → I place it so it fits."

enum XR {
    static let nearClip = 0.37
    static let focal = 0.74
    static let farMax = 10.0
    static let readNear = 1.0
    static let readFar = 1.5
    static let coneDeg = 30.0
    static let gazeDownDeg = 12.0
    static let eyeHeight = 1.45
}

// Filled from ARKit plane detection (or sensible defaults before a scan).
struct RoomProfile {
    var floorClearW: Double = 3.0
    var floorClearD: Double = 3.0
    var tablePresent: Bool = true
    var tableTopH: Double = 0.74
    var tableW: Double = 1.2
    var eyeHeight: Double = XR.eyeHeight
    var source: String = "default"
}

struct ScaleVariant: Identifiable {
    let id = UUID()
    let name: String          // "tabletop" | "real"
    let scale: Double
    let anchor: String        // "table" | "floor"
    let position: SIMD3<Float> // y-up metres, exhibit origin
    let fits: Bool
    let reason: String
    let comfortDistance: Double
}

enum SpatailAnalysis {
    /// Viable scale variants for an exhibit (real footprint, metres) in a room.
    static func variants(footprintW w: Double, depth d: Double, height h: Double,
                         room: RoomProfile) -> [ScaleVariant] {
        var out: [ScaleVariant] = []

        // TABLETOP — shrink to a diorama on the surface, viewed at reading distance
        let targetW = room.tablePresent ? min(0.6, room.tableW * 0.7) : 0.6
        let tScale = w > 0 ? min(1.0, targetW / w) : 1.0
        let tDist = max(XR.readNear, min(XR.readFar, 0.6))
        let tableH = room.tablePresent ? room.tableTopH : 0.74
        out.append(ScaleVariant(
            name: "tabletop", scale: tScale,
            anchor: room.tablePresent ? "table" : "floor",
            position: SIMD3<Float>(0, Float(tableH), Float(-tDist)),
            fits: true,
            reason: String(format: "Shrunk ×%.2f to a %.2f m diorama on the %@ at %.2f m.",
                           tScale, w * tScale,
                           room.tablePresent ? "table" : "floor", tDist),
            comfortDistance: tDist))

        // REAL — true size on the floor, far enough to sit inside the comfort cone
        let halfCone = (XR.coneDeg / 2) * .pi / 180
        let needDist = w > 0 ? (w / 2) / tan(halfCone) : XR.nearClip
        let rDist = max(XR.nearClip + w / 2, needDist)
        let realFits = (rDist + d / 2) <= room.floorClearD
            && w <= room.floorClearW && rDist <= XR.farMax
        out.append(ScaleVariant(
            name: "real", scale: 1.0, anchor: "floor",
            position: SIMD3<Float>(0, 0, Float(-rDist)),
            fits: realFits,
            reason: realFits
                ? String(format: "True size on the floor at %.2f m — fits your space.", rDist)
                : String(format: "Needs ~%.1f m depth; your clear space is ~%.1f m — move back or pick tabletop.",
                         rDist + d / 2, room.floorClearD),
            comfortDistance: rDist))

        return out.sorted { a, b in
            let ra = (a.fits && a.name == "real") ? 0 : (a.name == "tabletop" ? 1 : 2)
            let rb = (b.fits && b.name == "real") ? 0 : (b.name == "tabletop" ? 1 : 2)
            return ra < rb
        }
    }
}
