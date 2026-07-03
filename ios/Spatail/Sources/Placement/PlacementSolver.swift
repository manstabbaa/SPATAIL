// PlacementSolver.swift — the SPATAIL Placement Design System on device. Port of the
// canonical studio/spatail/placement_solver.py WITH the per-placement `reason` and the
// plan `diagnostics` block restored (parity with the python's richness: surface size,
// needed width, fit scale, obstacle count), and the Swift-side improvements kept:
// obstacle-on-surface filtering, the coverage parameter, baked-scale pinning.
//
// NEW: part-targeted placement. `solve(target:)` places content in the room, ON a
// registry object, or ON a named part of it (the bottle-cap test's landing pad):
// object/part targets anchor at the OBB/region center, oriented to the parent's yaw.
//
// Pure math over a RoomModel / SpatailObject; no ARKit/RealityKit here.
// Frame: room solves are in the user frame (metres, +Y up, user at origin looking -Z,
// same as the python); object/part solves return WORLD-space positions.

import Foundation
import simd

/// What the experience is anchored to.
enum PlacementTarget {
    case room
    case object(SpatailObject)
    case part(SpatailObject, SpatailPart)
}

struct PlacementSolver {
    // comfort constants mirror studio/xr_design.py (single source of truth)
    static let readNear: Float = 1.0
    static let readFar: Float = 1.5
    static let coneDeg: Float = 30.0

    struct AssetReq {
        let id: String
        let footprint: SIMD3<Float>     // real-world size (w, h, d) metres when known
        let role: String
        var realScaleBaked = false      // GLB already metric → keep its scale at 1.0
    }

    struct Placement {
        let assetId: String
        let position: SIMD3<Float>
        let yaw: Float
        let scale: Float
        let anchor: String              // table | floor | object
        let zone: String                // hero | secondary | peripheral
        let fits: Bool
        /// Human-readable WHY for the attribution trace (parity with the python
        /// canonical): "hero on table at 0.70 m, scaled x0.62".
        let reason: String
    }

    /// Diagnostics block (parity with the python plan's `diagnostics` dict — same
    /// keys via `asDictionary` so traces from device and studio line up).
    struct Diagnostics {
        var assetCount: Int = 0
        var surfaceSize = SIMD2<Float>(0, 0)     // (w, d) of the chosen surface/region
        var neededWidthM: Float = 0              // total footprint width + gaps at scale 1
        var fitScale: Float = 1
        var distanceM: Float = 0
        var spreadDeg: Float = 0
        var obstacleCount: Int = 0
        var source = "default"
        var note = ""

        var asDictionary: [String: Any] {
            var d: [String: Any] = [
                "assets": assetCount,
                "surface_m": [Double(round(surfaceSize.x * 1000) / 1000),
                              Double(round(surfaceSize.y * 1000) / 1000)],
                "needed_w_m": Double(round(neededWidthM * 1000) / 1000),
                "fit_scale": Double(round(fitScale * 1000) / 1000),
                "distance_m": Double(round(distanceM * 1000) / 1000),
                "spread_deg": Double(round(spreadDeg * 10) / 10),
                "obstacles": obstacleCount,
                "source": source,
            ]
            if !note.isEmpty { d["note"] = note }
            return d
        }
    }

    struct Plan {
        let placements: [Placement]
        let anchor: String              // table | floor | object
        let scaleVariant: String        // tabletop | real
        let diagnostics: Diagnostics
    }

    // MARK: - arc (shared slot math)

    /// n slots on a horizontal arc curved toward the user, centre-out ordered so the
    /// hero (index 0) lands dead ahead. Returns (position, yaw) per slot.
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

    // MARK: - target dispatch

    /// Solve against an explicit target. `.room` needs a RoomModel; `.object`/`.part`
    /// need only the registry object (positions come back in WORLD space).
    static func solve(target: PlacementTarget,
                      room: RoomModel? = nil,
                      assets: [AssetReq],
                      anchorPreference: String = "table",
                      scaleMode: String = "dynamic",
                      primary: String? = nil,
                      coverage: Float = 0.8) -> Plan {
        switch target {
        case .room:
            return solve(room: room ?? RoomModel(), assets: assets,
                         anchorPreference: anchorPreference, scaleMode: scaleMode,
                         primary: primary, coverage: coverage)
        case .object(let obj):
            return solveOnObject(obj, part: nil, assets: assets,
                                 primary: primary, coverage: coverage)
        case .part(let obj, let part):
            return solveOnObject(obj, part: part, assets: assets,
                                 primary: primary, coverage: coverage)
        }
    }

    // MARK: - room solve (the python canonical, reasons + diagnostics restored)

    static func solve(room: RoomModel, assets: [AssetReq],
                      anchorPreference: String = "table",
                      scaleMode: String = "dynamic",
                      primary: String? = nil,
                      coverage: Float = 0.8) -> Plan {   // design system §5: ≤60–70% of surface
        let n = assets.count
        guard n > 0 else {
            return Plan(placements: [], anchor: "table", scaleVariant: "tabletop",
                        diagnostics: Diagnostics(source: room.source, note: "no assets"))
        }
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
            baseH = floor?.height ?? 0
            distance = max(readFar, 1.2)
            variant = scaleMode == "real" ? "real" : "tabletop"
        }

        let widths = ordered.map { max($0.footprint.x, 0.03) }
        let gap: Float = 0.12
        let neededW = widths.reduce(0, +) + gap * Float(n - 1)
        let availW = max(0.2, surfW * min(max(coverage, 0.2), 0.95))
        let fitScale = neededW > 0 ? min(1.0, availW / neededW) : 1.0
        // A metric-baked hero renders at scale 1.0 (the render path enforces it), so keep
        // the solver in agreement — don't coverage-shrink the holder out from under it.
        // (Anchor choice is unchanged: a baked asset still sits on the table/floor it was
        // assigned; only the uniform fit scale is pinned to 1.0.)
        let heroBaked = ordered.first?.realScaleBaked ?? false
        let scale: Float = (variant == "real" || heroBaked) ? 1.0 : fitScale

        let spread = min(coneDeg, 8.0 + 7.0 * Float(max(0, n - 1)))
        let slots = arc(n, distance: distance, height: baseH, spreadDeg: spread)

        // keep-out only against obstacles that REST ON the chosen surface — floor
        // furniture (a chair tucked under a table) is shielded by the tabletop, so it
        // must not block a table placement (mirrors the Blender placer's _on_support
        // gate). For a floor placement baseH ≈ floor height, so floor obstacles count.
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
            let reason = String(format: "%@ on %@ at %.2f m, scaled x%.2f", zone, anchor, distance, scale)
                + (fits ? "" : "; tight/out-of-bounds — consider fewer items or floor scale")
            placements.append(Placement(assetId: a.id, position: pos, yaw: yaw, scale: scale,
                                        anchor: anchor, zone: zone, fits: fits, reason: reason))
        }

        return Plan(placements: placements, anchor: anchor, scaleVariant: variant,
                    diagnostics: Diagnostics(
                        assetCount: n,
                        surfaceSize: SIMD2(surfW, surfD),
                        neededWidthM: neededW,
                        fitScale: fitScale,
                        distanceM: distance,
                        spreadDeg: spread,
                        obstacleCount: room.obstacles.count,
                        source: room.source))
    }

    // MARK: - object / part solve (the landing pad)

    /// Anchor content ON a measured object (or a named part of it). The landing pad is
    /// the part's resolved region — or, per spec §3, the top-20% slice of the parent OBB
    /// when the part carries no region and reads like a top ("cap", "lid", "top").
    /// Positions are WORLD space; yaw is the parent's, so content sits oriented to it.
    private static func solveOnObject(_ obj: SpatailObject, part: SpatailPart?,
                                      assets: [AssetReq],
                                      primary: String?,
                                      coverage: Float) -> Plan {
        let n = assets.count
        let targetName = obj.label ?? "object"
        let partName = part?.label
        guard n > 0 else {
            return Plan(placements: [], anchor: "object", scaleVariant: "tabletop",
                        diagnostics: Diagnostics(source: "registry", note: "no assets"))
        }
        let hero = primary ?? assets[0].id
        let ordered = assets.sorted { ($0.id == hero ? 0 : 1) < ($1.id == hero ? 0 : 1) }

        let region = landingRegion(for: obj, part: part)
        // Content mounts at the region center, oriented to the parent's yaw.
        let padW = max(region.extents.x, 0.02)
        let padD = max(region.extents.z, 0.02)

        let widths = ordered.map { max($0.footprint.x, 0.03) }
        let gap: Float = 0.02                     // parts are small — tight packing
        let neededW = widths.reduce(0, +) + gap * Float(n - 1)
        let availW = max(0.02, padW * min(max(coverage, 0.2), 0.95))
        let fitScale = neededW > 0 ? min(1.0, availW / neededW) : 1.0
        let heroBaked = ordered.first?.realScaleBaked ?? false
        let scale: Float = heroBaked ? 1.0 : fitScale

        // hero dead-centre on the pad; secondaries ring around it in the parent's frame
        let rot = simd_quatf(angle: obj.obb.yaw, axis: SIMD3(0, 1, 0))
        let ringR = max(padW, padD) * 0.6 + 0.02
        var placements: [Placement] = []
        for (i, a) in ordered.enumerated() {
            let zone = i == 0 ? "hero" : (i <= 2 ? "secondary" : "peripheral")
            var local = SIMD3<Float>(0, 0, 0)
            if i > 0 {
                let ang = 2 * Float.pi * Float(i - 1) / Float(max(n - 1, 1))
                local = SIMD3(ringR * cos(ang), 0, ringR * sin(ang))
            }
            let world = region.center + rot.act(local)
            let fits = Float(widths[i]) * scale <= padW + 0.001 && scale > 0.01
            let where_ = partName.map { "\(targetName) · \($0)" } ?? targetName
            let reason = String(format: "%@ on %@ (%.2f×%.2f m pad), scaled x%.2f",
                                zone, where_, padW, padD, scale)
                + (fits ? "" : "; pad too small — content clamped to fit scale")
            placements.append(Placement(assetId: a.id, position: world, yaw: obj.obb.yaw,
                                        scale: scale, anchor: "object", zone: zone,
                                        fits: fits, reason: reason))
        }

        var note = "target=\(targetName)"
        if let p = partName { note += " part=\(p)" }
        return Plan(placements: placements, anchor: "object", scaleVariant: "tabletop",
                    diagnostics: Diagnostics(
                        assetCount: n,
                        surfaceSize: SIMD2(padW, padD),
                        neededWidthM: neededW,
                        fitScale: fitScale,
                        distanceM: 0,
                        spreadDeg: 0,
                        obstacleCount: 0,
                        source: "registry",
                        note: note))
    }

    /// The world-space landing region: the part's resolved region, the §3 fallback
    /// slice for cap/lid/top parts, a generic NAMED slice (base/left_side/right_side/
    /// front/back + aliases — the Scene Coherence generalization; axis-aligned at
    /// this depth, no camera), or the parent OBB's TOP face (mounting on a whole
    /// object means resting on it, so the pad rides the top plane, centered).
    static func landingRegion(for obj: SpatailObject, part: SpatailPart?) -> OrientedBox {
        if let region = part?.region { return region }
        let obb = obj.obb
        let topSlice = max(obb.extents.y * 0.2, 0.01)
        if let part, ["cap", "lid", "top"].contains(part.label.lowercased()) {
            // spec §3 fallback: top 20% slice of the parent OBB (byte-compatible)
            return OrientedBox(
                center: SIMD3(obb.center.x, obb.center.y + obb.extents.y / 2 - topSlice / 2, obb.center.z),
                extents: SIMD3(obb.extents.x, topSlice, obb.extents.z),
                yaw: obb.yaw)
        }
        if let part {
            // generalized §3: any other slice name the geometry can answer
            if let slice = RegistryCoherence.namedSlice(part.label, of: obb) {
                return slice
            }
            // named part with no region/slice yet → parent center (identity will refine it)
            return obb
        }
        // whole object → the top face as a zero-height pad
        return OrientedBox(
            center: SIMD3(obb.center.x, obb.center.y + obb.extents.y / 2, obb.center.z),
            extents: SIMD3(obb.extents.x, 0, obb.extents.z),
            yaw: obb.yaw)
    }
}
