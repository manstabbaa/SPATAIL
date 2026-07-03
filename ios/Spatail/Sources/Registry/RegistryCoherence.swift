// RegistryCoherence.swift — the Scene Coherence pass, pure math.
//
// The field report behind this (founder, live Truth Overlay session): the registry
// minted DUPLICATE objects — several boxes per real thing, and asks could bind to an
// offset duplicate. This file is the backstop that guarantees ONE object per physical
// thing, plus the shared "display-worthy" definition and the generic named-slice
// geometry (part-level asks on arbitrary objects — the headphones-speaker fix).
//
//   • mergePass    — deduplicate overlapping objects; deterministic survivor
//                    selection; the loser's identity/parts/form are absorbed and its
//                    id → survivor alias is returned (hysteresis: merges never
//                    oscillate because the loser is gone and later references
//                    resolve through the alias).
//   • assignDisplayWorthiness — the ONE definition Lens chips, overlay boxes and
//                    ask-matching all share (labeled OR measured-form OR ≥3
//                    consecutive ticks; capped at 12).
//   • namedSlice   — on-demand sub-OBB slices (top/base/left_side/right_side/
//                    front/back) so EVERY object answers part asks even without
//                    VLM part boxes. Never precomputed into the wire payload.
//
// Foundation + simd ONLY — no ARKit — the exact shipped logic runs in the
// off-device harness (spec §0 law 5: if you can't see what it saw, it doesn't ship).

import Foundation
import simd

enum RegistryCoherence {

    // MARK: Merge tunables

    /// Two OBBs whose XZ footprints overlap at/above this IoU are the same thing.
    static let xzIoUThreshold: Float = 0.4
    /// Sanity for the IoU branch: SOME vertical overlap (¼ of the smaller height),
    /// so stacked shelf levels can't merge through a shared footprint.
    static let iouVerticalOverlapMin: Float = 0.25
    /// Center-distance branch: base gate (metres) …
    static let centerBaseDistance: Float = 0.12
    /// … or half the SMALLER mean extent, whichever is larger.
    static let centerExtentFactor: Float = 0.5
    /// Center-distance branch requires compatible heights: vertical overlap of the
    /// smaller box above this fraction.
    static let centerVerticalOverlapMin: Float = 0.5

    // MARK: Display tunables

    /// Consecutive ingest ticks that establish an unlabeled, formless object.
    static let establishTicks = 3
    /// Display-worthy objects are capped here (labeled first, then confidence,
    /// then recency) — the rest stay internal.
    static let displayCap = 12
    /// Unlabeled + no-form objects expire this fast (labeled/formed keep the
    /// spec-§3 ~10 s; pinned are exempt entirely).
    static let unlabeledExpirySeconds: TimeInterval = 5

    // MARK: - XZ footprint IoU (yaw-true rotated rectangles)

    /// IoU of the two OBBs' XZ footprints — exact convex intersection
    /// (Sutherland–Hodgman) of the yaw-rotated rectangles, not an AABB guess.
    static func xzIoU(_ a: OrientedBox, _ b: OrientedBox) -> Float {
        let areaA = a.extents.x * a.extents.z
        let areaB = b.extents.x * b.extents.z
        guard areaA > 0, areaB > 0 else { return 0 }
        let inter = polygonArea(clip(subject: xzCorners(a), by: xzCorners(b)))
        let union = areaA + areaB - inter
        return union > 0 ? inter / union : 0
    }

    /// Fraction of the SMALLER box's height shared by both boxes (0 = disjoint).
    static func verticalOverlapRatio(_ a: OrientedBox, _ b: OrientedBox) -> Float {
        let aBot = a.center.y - a.extents.y / 2, aTop = a.center.y + a.extents.y / 2
        let bBot = b.center.y - b.extents.y / 2, bTop = b.center.y + b.extents.y / 2
        let overlap = min(aTop, bTop) - max(aBot, bBot)
        let minH = min(a.extents.y, b.extents.y)
        guard minH > 0 else { return overlap >= 0 ? 1 : 0 }
        return max(0, overlap / minH)
    }

    /// XZ corners of the OBB footprint, same rotation convention as the registry's
    /// anchor transform (world.x = c·lx + s·lz, world.z = −s·lx + c·lz).
    private static func xzCorners(_ o: OrientedBox) -> [SIMD2<Float>] {
        let c = cos(o.yaw), s = sin(o.yaw)
        let hx = o.extents.x / 2, hz = o.extents.z / 2
        let locals: [(Float, Float)] = [(-hx, -hz), (hx, -hz), (hx, hz), (-hx, hz)]
        return locals.map { lx, lz in
            SIMD2(o.center.x + c * lx + s * lz, o.center.z - s * lx + c * lz)
        }
    }

    private static func polygonArea(_ poly: [SIMD2<Float>]) -> Float {
        guard poly.count >= 3 else { return 0 }
        var s: Float = 0
        for i in 0..<poly.count {
            let p = poly[i], q = poly[(i + 1) % poly.count]
            s += p.x * q.y - q.x * p.y
        }
        return abs(s) / 2
    }

    /// Sutherland–Hodgman: clip `subject` by the convex polygon `clipPoly`.
    private static func clip(subject: [SIMD2<Float>],
                             by clipPolyIn: [SIMD2<Float>]) -> [SIMD2<Float>] {
        var clipPoly = clipPolyIn
        // The half-plane test below assumes CCW winding — enforce it.
        var signed: Float = 0
        for i in 0..<clipPoly.count {
            let p = clipPoly[i], q = clipPoly[(i + 1) % clipPoly.count]
            signed += p.x * q.y - q.x * p.y
        }
        if signed < 0 { clipPoly.reverse() }

        var output = subject
        for i in 0..<clipPoly.count {
            guard !output.isEmpty else { return [] }
            let a = clipPoly[i], b = clipPoly[(i + 1) % clipPoly.count]
            func side(_ p: SIMD2<Float>) -> Float {
                (b.x - a.x) * (p.y - a.y) - (b.y - a.y) * (p.x - a.x)
            }
            let input = output
            output = []
            for j in 0..<input.count {
                let p = input[j], q = input[(j + 1) % input.count]
                let sp = side(p), sq = side(q)
                if sp >= 0 {
                    output.append(p)
                    if sq < 0 { output.append(p + (q - p) * (sp / (sp - sq))) }
                } else if sq >= 0 {
                    output.append(p + (q - p) * (sp / (sp - sq)))
                }
            }
        }
        return output
    }

    // MARK: - Merge decision

    /// Two objects are the same physical thing when their OBBs overlap in XZ with
    /// IoU ≥ 0.4 (some vertical overlap required — shelf levels stay apart), OR
    /// their centers are within max(0.12 m, 0.5 × the smaller mean extent) at
    /// compatible heights (vertical overlap > 50 %).
    static func shouldMerge(_ a: SpatailObject, _ b: SpatailObject) -> Bool {
        let v = verticalOverlapRatio(a.obb, b.obb)
        if v > iouVerticalOverlapMin, xzIoU(a.obb, b.obb) >= xzIoUThreshold {
            return true
        }
        guard v > centerVerticalOverlapMin else { return false }
        let dx = a.obb.center.x - b.obb.center.x
        let dz = a.obb.center.z - b.obb.center.z
        let gate = max(centerBaseDistance,
                       centerExtentFactor * min(a.obb.meanExtent, b.obb.meanExtent))
        return sqrt(dx * dx + dz * dz) <= gate
    }

    /// Survivor ordering: labeled beats unlabeled; measured form beats prior beats
    /// none; then higher label confidence; then the OLDER object (stability); then
    /// deterministic id tiebreak. Returns true when `a` should survive over `b`.
    static func survives(_ a: SpatailObject, over b: SpatailObject) -> Bool {
        let aLabeled = a.label != nil, bLabeled = b.label != nil
        if aLabeled != bLabeled { return aLabeled }
        let fa = formRank(a), fb = formRank(b)
        if fa != fb { return fa > fb }
        if a.confidence != b.confidence { return a.confidence > b.confidence }
        let aBorn = a.firstSeenAt ?? a.lastMeasuredAt
        let bBorn = b.firstSeenAt ?? b.lastMeasuredAt
        if aBorn != bBorn { return aBorn < bBorn }
        return a.id.uuidString < b.id.uuidString
    }

    private static func formRank(_ o: SpatailObject) -> Int {
        switch o.form?.source {
        case .measured: return 2
        case .prior:    return 1
        case nil:       return 0
        }
    }

    /// The survivor absorbs everything the loser knew that it doesn't:
    /// label + debounce state, better-provenance form (and its OBB), parts by name,
    /// support link, seen-streak, and freshness stamps.
    static func absorb(loser: SpatailObject, into survivor: inout SpatailObject) {
        // Identity: an unlabeled survivor adopts the loser's label wholesale; equal
        // labels keep the freshest confirmation and best confidence.
        if survivor.label == nil, let label = loser.label {
            survivor.label = label
            survivor.confidence = loser.confidence
            survivor.lastIdentifiedAt = loser.lastIdentifiedAt
        } else if let sl = survivor.label, let ll = loser.label,
                  sl.caseInsensitiveCompare(ll) == .orderedSame {
            survivor.confidence = max(survivor.confidence, loser.confidence)
            switch (survivor.lastIdentifiedAt, loser.lastIdentifiedAt) {
            case let (s?, l?): survivor.lastIdentifiedAt = max(s, l)
            case (nil, let l?): survivor.lastIdentifiedAt = l
            default: break
            }
        }
        // Debounce state rides along when the survivor has none of its own.
        if survivor.pendingLabel == nil, let pending = loser.pendingLabel {
            survivor.pendingLabel = pending
            survivor.pendingCount = loser.pendingCount
        }
        // Form: better provenance wins, and its OBB (the geometry truth) with it.
        if formRank(loser) > formRank(survivor) {
            survivor.form = loser.form
            survivor.formUpdatedAt = loser.formUpdatedAt
            survivor.obb = loser.obb
        }
        // Parts merge by (lowercased) name — the survivor's own always win.
        for part in loser.parts {
            let name = part.label.lowercased()
            if !survivor.parts.contains(where: { $0.label.lowercased() == name }) {
                survivor.parts.append(part)
            }
        }
        if survivor.supportSurfaceId == nil {
            survivor.supportSurfaceId = loser.supportSurfaceId
        }
        survivor.seenStreak = max(survivor.seenStreak, loser.seenStreak)
        survivor.established = survivor.established || loser.established
        survivor.lastMeasuredAt = max(survivor.lastMeasuredAt, loser.lastMeasuredAt)
        switch (survivor.firstSeenAt, loser.firstSeenAt) {
        case let (s?, l?): survivor.firstSeenAt = min(s, l)
        case (nil, let l?): survivor.firstSeenAt = l
        default: break
        }
    }

    /// One full merge pass: repeatedly collapse the first mergeable pair until the
    /// set is stable (each merge strictly shrinks the set — always terminates).
    /// Returns the merged set plus a CHAIN-COLLAPSED loser → survivor alias map.
    static func mergePass(_ objects: [SpatailObject])
        -> (objects: [SpatailObject], aliases: [UUID: UUID]) {
        var objs = objects
        var aliases: [UUID: UUID] = [:]
        var merged = true
        while merged {
            merged = false
            outer: for i in 0..<objs.count {
                for j in (i + 1)..<objs.count where shouldMerge(objs[i], objs[j]) {
                    let iSurvives = survives(objs[i], over: objs[j])
                    let s = iSurvives ? i : j
                    let l = iSurvives ? j : i
                    var survivor = objs[s]
                    absorb(loser: objs[l], into: &survivor)
                    aliases[objs[l].id] = survivor.id
                    objs[s] = survivor
                    objs.remove(at: l)
                    merged = true
                    break outer
                }
            }
        }
        // Collapse alias chains (A→B, B→C ⇒ A→C) so lookups are single-hop.
        for (loser, target) in aliases {
            var final = target
            while let next = aliases[final] { final = next }
            aliases[loser] = final
        }
        return (objs, aliases)
    }

    // MARK: - Display worthiness (the ONE shared definition)

    /// Eligible = labeled OR fitted form OR established by ≥ `establishTicks`
    /// consecutive ingest ticks. At most `displayCap` are flagged, ranked labeled
    /// first, then label confidence, then recency. Everything else stays internal:
    /// no chip, no overlay box, no ask binding, no wire.
    static func assignDisplayWorthiness(_ objects: inout [SpatailObject]) {
        var eligible: [Int] = []
        for i in objects.indices {
            objects[i].displayWorthy = false
            let o = objects[i]
            if o.label != nil || o.form != nil || o.established
                || o.seenStreak >= establishTicks {
                eligible.append(i)
            }
        }
        let ranked = eligible.sorted { i, j in
            let a = objects[i], b = objects[j]
            let al = a.label != nil, bl = b.label != nil
            if al != bl { return al }
            if a.confidence != b.confidence { return a.confidence > b.confidence }
            return a.lastMeasuredAt > b.lastMeasuredAt
        }
        for i in ranked.prefix(displayCap) { objects[i].displayWorthy = true }
    }

    // MARK: - Named OBB slices (generic part regions, synthesized ON DEMAND)

    /// Canonical slice names + the ask-side aliases that resolve to them.
    private static let sliceAliases: [String: String] = [
        "cap": "top", "lid": "top",
        "bottom": "base", "stand": "base",
        "screen": "front", "face": "front",
        "rear": "back",
        "left": "left_side", "right": "right_side",
    ]

    /// A named sub-OBB of `parent`, oriented to it:
    ///   top / base            — outer 20 % along Y (§3 top-slice math, byte-compatible
    ///                           with PlacementSolver's cap/lid/top fallback);
    ///   left_side / right_side — outer 30 % along the MINOR horizontal axis;
    ///   front / back          — outer 30 % along the MAJOR horizontal axis; with a
    ///                           camera position, "front" is the end nearer the
    ///                           camera, else axis-aligned (+axis = front).
    /// nil for names this geometry can't answer.
    static func namedSlice(_ rawName: String, of parent: OrientedBox,
                           cameraPosition: SIMD3<Float>? = nil) -> OrientedBox? {
        let lowered = rawName.lowercased()
            .trimmingCharacters(in: .whitespacesAndNewlines)
        let name = sliceAliases[lowered] ?? lowered
        let e = parent.extents
        let c = cos(parent.yaw), s = sin(parent.yaw)
        // Parent-local → world, same convention as RegistryFusion.clamp.
        func world(_ lx: Float, _ ly: Float, _ lz: Float) -> SIMD3<Float> {
            SIMD3(parent.center.x + c * lx + s * lz,
                  parent.center.y + ly,
                  parent.center.z - s * lx + c * lz)
        }

        switch name {
        case "top", "base":
            let h = max(e.y * 0.2, 0.01)          // §3 slice, PlacementSolver-identical
            let off = (e.y - h) / 2
            return OrientedBox(center: world(0, name == "top" ? off : -off, 0),
                               extents: SIMD3(e.x, h, e.z), yaw: parent.yaw)

        case "left_side", "right_side":
            let alongX = e.x <= e.z               // minor horizontal axis
            let extent = alongX ? e.x : e.z
            let w = max(extent * 0.3, 0.01)
            let off = (extent - w) / 2
            let sign: Float = name == "left_side" ? -1 : 1
            let center = alongX ? world(sign * off, 0, 0) : world(0, 0, sign * off)
            let extents = alongX ? SIMD3(w, e.y, e.z) : SIMD3(e.x, e.y, w)
            return OrientedBox(center: center, extents: extents, yaw: parent.yaw)

        case "front", "back":
            let alongX = e.x > e.z                // major horizontal axis
            let extent = alongX ? e.x : e.z
            let w = max(extent * 0.3, 0.01)
            let off = (extent - w) / 2
            var sign: Float = 1                   // no camera → +axis is "front"
            if let cam = cameraPosition {
                let plus = alongX ? world(off, 0, 0) : world(0, 0, off)
                let minus = alongX ? world(-off, 0, 0) : world(0, 0, -off)
                sign = simd_distance_squared(cam, plus)
                    <= simd_distance_squared(cam, minus) ? 1 : -1
            }
            if name == "back" { sign = -sign }
            let center = alongX ? world(sign * off, 0, 0) : world(0, 0, sign * off)
            let extents = alongX ? SIMD3(w, e.y, e.z) : SIMD3(e.x, e.y, w)
            return OrientedBox(center: center, extents: extents, yaw: parent.yaw)

        default:
            return nil
        }
    }
}
