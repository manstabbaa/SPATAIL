// ObjectRegistry.swift — the meeting point of the three clocks (LIVE_BRAIN_SPEC §3).
//
//   Measurement (1–2 Hz, ARKit)  → ingest(_:now:)          — WHERE + FORM
//   Identity (~1 Hz, VLM)        → applyIdentity(…)        — WHAT (label debounce)
//   Anchoring (60 Hz, ARKit)     → one world ARAnchor per object; placed experiences
//                                  ride the anchor untouched between identity ticks.
//
// Semantics implemented exactly per spec §3:
//   • ingest matching: centers within max(0.15 m, 0.5 × mean extent) → update the
//     existing object with light smoothing; otherwise mint a new id.
//   • expiry: objects unseen for ~10 s are dropped — unless a placed experience has
//     pinned them (`pinnedObjectIds`) or ARKit still tracks their anchor via a
//     placement (the runtime pins ids when it binds an element to an object).
//   • identity attach: project each object's OBB into the identified frame (the
//     pipeline's `projector`), IoU ≥ 0.3 vs the identification's detection boxes,
//     greedy best-match one-to-one.
//   • label debounce: adopt after 2 consecutive ticks agreeing, or immediately at
//     confidence ≥ 0.8; a different label must win the same debounce to replace it.
//   • parts: each `parts[].box` resolved through the depth-grid path (the pipeline's
//     `resolver`), clamped inside the parent OBB; no box + label ∈ {cap, lid, top} →
//     top-20 % slice of the parent. Regions are oriented to the parent (parent yaw).
//   • support surface: refreshed on every ingest from the resolver's linking.
//
// All state mutation is main-actor; `objects` publishes ONCE per ingest/identity pass
// so SwiftUI and the room uplink see coherent snapshots.

import Foundation
import ARKit
import CoreGraphics
import simd

@MainActor
final class ObjectRegistry: ObservableObject {

    // MARK: Tunables (spec §3 defaults — "guidance values, not dogma")

    /// Base center-distance gate for matching a measurement to an existing object.
    private static let matchBaseDistance: Float = 0.15
    /// …or half the mean extent, whichever is larger.
    private static let matchExtentFactor: Float = 0.5
    /// Expiry (v3 §6 — ghost culling): the clock only ticks while an object is
    /// VISIBLE yet unmatched. A couch behind the user persists (up to an
    /// absolute memory bound); a ghost box in plain view dies fast. Established
    /// objects tolerate many visible misses — the ambient detector legitimately
    /// rotates its attention between things in frame.
    private static let candidateMissLimit = 5
    private static let establishedMissLimit = 20
    private static let candidateAbsoluteExpiry: TimeInterval = 30
    private static let establishedAbsoluteExpiry: TimeInterval = 90
    /// Bound on the merged-id alias memory (hysteresis; oldest dropped first).
    private static let aliasCapacity = 256
    /// Blend weight of the NEW measurement when smoothing an existing object.
    private static let smoothing: Float = 0.35
    /// Identity attach needs at least this IoU (spec §3).
    private static let iouThreshold = 0.3
    /// Re-seat the world anchor when the OBB center drifts beyond this (metres).
    private static let anchorMoveThreshold: Float = 0.05
    /// Part labels that earn the top-slice fallback when the VLM gives no box.
    private static let topSliceLabels: Set<String> = ["cap", "lid", "top"]
    /// Fraction of the parent's height the fallback slice occupies.
    private static let topSliceFraction: Float = 0.2
    /// A fitted form older than this no longer pins the OBB (form engine stalled →
    /// revert to plain grid smoothing rather than freeze a stale shape).
    private static let formFreshnessSeconds: TimeInterval = 12
    /// A fresh MEASURED form is not displaced by an incoming PRIOR for this long
    /// (one occluded frame must not demote real measurements to guesses).
    private static let measuredFormRetention: TimeInterval = 15
    /// Image-IoU gate for the prior-object rescue match (world centers can't match
    /// when depth under the object is garbage — the image still can).
    private static let rescueIoU = 0.4
    /// A single-frame measurement this far from a form-pinned OBB means the object
    /// truly moved — drop the pinned form and track normally again.
    private static let formJumpEscape: Float = 0.3

    // MARK: Published state

    /// The live set of measured (and possibly identified) objects, wire-aligned with
    /// `room.update.objects[]`.
    @Published private(set) var objects: [SpatailObject] = []

    /// Objects a placed experience anchors to — exempt from expiry (spec §3:
    /// "unless anchored by a placed experience"). The runtime/AppModel maintains this.
    /// Ids are resolved through the merge-alias map, so a pin on a merged-away
    /// duplicate keeps protecting the survivor.
    var pinnedObjectIds: Set<UUID> = []

    /// Fired (on main) whenever the coherence pass merged duplicates —
    /// loser id → survivor id. The pipeline repoints Form Engine clouds with it.
    var onObjectsMerged: (([UUID: UUID]) -> Void)?

    /// LiDAR mesh-classification clusters (v3 §2) — same-entity evidence for the
    /// merge pass. AppModel points this at the room scanner; nil/empty = no
    /// cluster evidence (tests, no-LiDAR devices).
    var furnitureClustersProvider: (() -> [FurnitureCluster])?

    // MARK: Merge hysteresis (Scene Coherence pass)

    /// Loser → survivor memory, chain-collapsed, bounded. A re-detection, pin,
    /// chip tap or ask that still holds a merged-away id resolves to the survivor
    /// here — merges can never oscillate because the loser no longer exists.
    private(set) var mergedAliases: [UUID: UUID] = [:]
    private var aliasOrder: [UUID] = []

    /// The live id an (possibly merged-away) object id resolves to.
    func canonicalId(_ id: UUID) -> UUID { mergedAliases[id] ?? id }

    /// The live object an id resolves to, through merge aliases.
    func object(for id: UUID) -> SpatailObject? {
        let canonical = canonicalId(id)
        return objects.first { $0.id == canonical }
    }

    private func recordAliases(_ aliases: [UUID: UUID]) {
        for (loser, survivor) in aliases {
            // New survivor may itself alias somewhere from an older merge.
            let target = mergedAliases[survivor] ?? survivor
            if mergedAliases[loser] == nil { aliasOrder.append(loser) }
            mergedAliases[loser] = target
            // Re-target older entries that pointed at the (now merged) loser.
            for (k, v) in mergedAliases where v == loser { mergedAliases[k] = target }
        }
        while aliasOrder.count > Self.aliasCapacity {
            let oldest = aliasOrder.removeFirst()
            mergedAliases.removeValue(forKey: oldest)
        }
    }

    /// pinnedObjectIds with every id resolved through the alias map.
    private func effectivePinnedIds() -> Set<UUID> {
        Set(pinnedObjectIds.map(canonicalId))
    }

    // MARK: Anchors

    private weak var session: ARSession?
    private var anchors: [UUID: ARAnchor] = [:]

    /// Give the registry the ARSession its world anchors live in. Called by the
    /// pipeline on start (idempotent); anchors minted earlier are re-added.
    func attachSession(_ session: ARSession) {
        guard self.session !== session else { return }
        self.session = session
        for anchor in anchors.values { session.add(anchor: anchor) }
    }

    /// The world anchor carrying an object (at its OBB center, rotated by its yaw).
    /// Placed experiences parent to this so geometry rides ARKit's 60 Hz corrections.
    /// Resolves through merge aliases — an anchor request for a merged-away
    /// duplicate returns the survivor's anchor.
    func arAnchor(for id: UUID) -> ARAnchor? { anchors[canonicalId(id)] }

    // MARK: - Measurement ingest (1–2 Hz)

    /// Returns the detection-id → object-id matching, so the Form Engine can
    /// accumulate each detection's masked points into the right object's cloud.
    /// `projector` (world OBB → normalized current-frame rect) powers the rescue
    /// match for prior-form objects: when depth under a transparent object is
    /// garbage, world centers can't match — the image still can.
    @discardableResult
    func ingest(_ resolved: [ResolvedDetection], now: TimeInterval,
                projector: ((OrientedBox) -> CGRect?)? = nil) -> [UUID: UUID] {
        var updated = objects
        var matches: [UUID: UUID] = [:]

        // Greedy one-to-one matching, nearest pair first.
        struct Pair { let detectionIdx: Int; let objectIdx: Int; let distance: Float }
        var pairs: [Pair] = []
        for (di, r) in resolved.enumerated() {
            for (oi, obj) in updated.enumerated() {
                let d = simd_distance(r.obb.center, obj.obb.center)
                let meanExtent = (r.obb.meanExtent + obj.obb.meanExtent) / 2
                let gate = max(Self.matchBaseDistance, Self.matchExtentFactor * meanExtent)
                if d < gate { pairs.append(Pair(detectionIdx: di, objectIdx: oi, distance: d)) }
            }
        }
        pairs.sort { $0.distance < $1.distance }

        var usedDetections = Set<Int>()
        var usedObjects = Set<Int>()
        for pair in pairs {
            guard !usedDetections.contains(pair.detectionIdx),
                  !usedObjects.contains(pair.objectIdx) else { continue }
            usedDetections.insert(pair.detectionIdx)
            usedObjects.insert(pair.objectIdx)

            let r = resolved[pair.detectionIdx]
            var obj = updated[pair.objectIdx]
            Self.fuse(measurement: r, into: &obj, now: now)
            updated[pair.objectIdx] = obj
            matches[r.detection.id] = obj.id
        }

        // Rescue match (Form Engine stage 4): a PRIOR-form object's OBB is seated
        // from the support raycast while the single-frame depth under it stays
        // garbage — center gating would mint a duplicate every tick. Match by
        // image-space IoU instead; never let the garbage OBB touch the seated one.
        if let projector {
            for (di, r) in resolved.enumerated() where !usedDetections.contains(di) {
                var best: (objectIdx: Int, iou: Double)?
                for (oi, obj) in updated.enumerated() where !usedObjects.contains(oi) {
                    guard obj.form?.source == .prior,
                          let at = obj.formUpdatedAt,
                          now - at < Self.formFreshnessSeconds * 2,
                          let rect = projector(obj.obb) else { continue }
                    let iou = detectionIoU(r.detection.box, rect)
                    if iou >= Self.rescueIoU, iou > (best?.iou ?? 0) {
                        best = (oi, iou)
                    }
                }
                guard let best else { continue }
                usedDetections.insert(di)
                usedObjects.insert(best.objectIdx)
                var obj = updated[best.objectIdx]
                obj.lastMeasuredAt = now       // seen — OBB stays prior-seated
                obj.seenStreak += 1
                if obj.seenStreak >= RegistryCoherence.establishTicks {
                    obj.established = true
                }
                Self.applyDetectorLabel(r.detection, into: &obj, now: now)
                updated[best.objectIdx] = obj
                matches[r.detection.id] = obj.id
            }
        }

        // Consecutive-tick discipline: a pre-existing object NOT matched this
        // ingest loses its streak (established objects keep their standing).
        for oi in updated.indices where !usedObjects.contains(oi) {
            updated[oi].seenStreak = 0
        }

        // Visibility clock (v3 §6): matched objects are trivially visible; an
        // UNMATCHED object that projects into this frame accrues a miss — the
        // ghost-culling signal. Miss-counting needs an active detector (an
        // empty pass must not decay everything) and a projector to test with.
        for oi in updated.indices {
            if usedObjects.contains(oi) {
                updated[oi].lastVisibleAt = now
                updated[oi].missedWhileVisible = 0
            } else if !resolved.isEmpty, let projector,
                      projector(updated[oi].obb) != nil {
                updated[oi].lastVisibleAt = now
                updated[oi].missedWhileVisible += 1
            }
        }

        // Unmatched measurements mint new objects (streak 1, birth-stamped).
        // Detector labels: SEMANTIC ones seed the identity debounce (the VLM
        // still outranks via re-debounce); HINTS condition without displaying.
        for (di, r) in resolved.enumerated() where !usedDetections.contains(di) {
            var minted = SpatailObject(obb: r.obb,
                                       supportSurfaceId: r.supportSurfaceId,
                                       seenStreak: 1,
                                       firstSeenAt: now,
                                       lastMeasuredAt: now)
            Self.applyDetectorLabel(r.detection, into: &minted, now: now)
            updated.append(minted)
            matches[r.detection.id] = minted.id
        }

        // Supported-by pass BEFORE the merge pass (v3 §0): the laundry pile on
        // the couch becomes a CHILD — never a merge candidate the class-scaled
        // gates could swallow.
        RegistryCoherence.assignSupportLinks(&updated)

        // Scene Coherence merge pass — the backstop that guarantees ONE object
        // per physical thing (duplicate minting on garbage depth is already
        // reduced by the rescue match above; this closes the rest).
        matches = runMergePass(on: &updated, matches: matches)

        // Expiry (v3 §6): ghost-dead when visible-and-missing too long;
        // forgotten only past the absolute bound. Pinned (through aliases)
        // are exempt. Looking away never expires the couch.
        let pinned = effectivePinnedIds()
        var expired: [UUID] = []
        updated.removeAll { obj in
            guard !pinned.contains(obj.id) else { return false }
            let candidate = obj.label == nil && obj.form == nil
            let ghostDead = obj.missedWhileVisible
                >= (candidate ? Self.candidateMissLimit : Self.establishedMissLimit)
            let forgotten = now - obj.lastMeasuredAt
                > (candidate ? Self.candidateAbsoluteExpiry : Self.establishedAbsoluteExpiry)
            let drop = ghostDead || forgotten
            if drop { expired.append(obj.id) }
            return drop
        }
        for id in expired { removeAnchor(for: id) }

        // ONE shared display-worthiness definition (chips + overlay + asks).
        RegistryCoherence.assignDisplayWorthiness(&updated)

        // Anchor sync: create/update a world anchor per object at its OBB center.
        for obj in updated { syncAnchor(for: obj) }

        objects = updated   // single publish
        return matches
    }

    /// Run the coherence merge over `updated`; record aliases, drop loser anchors,
    /// repoint this pass's detection→object matches and notify the Form Engine so
    /// fused clouds follow the survivor. Returns the repointed matches.
    private func runMergePass(on updated: inout [SpatailObject],
                              matches: [UUID: UUID]) -> [UUID: UUID] {
        // Mesh-cluster same-entity evidence (v3 §2) rides alongside shouldMerge.
        let clusters = furnitureClustersProvider?() ?? []
        let alsoMerge: ((SpatailObject, SpatailObject) -> Bool)? = clusters.isEmpty
            ? nil
            : { RegistryCoherence.sameClusterEvidence($0, $1, clusters: clusters) }
        let (mergedObjects, aliases) = RegistryCoherence.mergePass(updated,
                                                                   alsoMerge: alsoMerge)
        guard !aliases.isEmpty else { return matches }
        updated = mergedObjects
        recordAliases(aliases)
        for loser in aliases.keys { removeAnchor(for: loser) }
        var repointed = matches
        for (detectionId, objectId) in matches {
            if let survivor = aliases[objectId] { repointed[detectionId] = survivor }
        }
        onObjectsMerged?(aliases)
        return repointed
    }

    /// One matched measurement → object update, honoring the fitted form:
    ///   • fresh MEASURED form → smooth the CENTER only (reduced alpha; the profile
    ///     owns extents/yaw — this replaces the light whole-OBB smoothing);
    ///   • fresh PRIOR form → leave the seated OBB alone (single-frame depth under
    ///     a transparent object is garbage by definition);
    ///   • a big jump means the object truly moved → drop the pinned form, smooth
    ///     normally (the Form Engine's cloud resets and refits).
    private static func fuse(measurement r: ResolvedDetection,
                             into obj: inout SpatailObject, now: TimeInterval) {
        let formFresh = obj.form != nil
            && obj.formUpdatedAt.map { now - $0 < formFreshnessSeconds } ?? false
        // Jump-escape scales with the class (v3 §1): a half-couch detection
        // legitimately lands ~0.7 m from the couch center — that is a partial
        // view, not the couch moving.
        let jumpGate = max(Self.formJumpEscape,
                           (FormPriors.furniturePrior(for: obj.label ?? obj.classHint)?
                               .length ?? 0) * 0.5)
        let jumped = simd_distance(r.obb.center, obj.obb.center) > jumpGate

        if formFresh, !jumped, let form = obj.form {
            switch form.source {
            case .measured, .roomplan:
                // The profile/model owns extents+yaw; measurements only nudge
                // the center.
                let smoothed = RegistryFusion.smooth(old: obj.obb, new: r.obb,
                                                     alpha: smoothing * 0.4)
                obj.obb = OrientedBox(center: smoothed.center,
                                      extents: obj.obb.extents,
                                      yaw: obj.obb.yaw)
            case .prior:
                break
            }
        } else {
            if jumped, obj.form != nil {
                obj.form = nil
                obj.formUpdatedAt = nil
                obj.parts.removeAll { $0.isMeasured && $0.box == nil }
            }
            obj.obb = RegistryFusion.smooth(old: obj.obb, new: r.obb, alpha: smoothing)
        }
        // Support surface refresh on ingest; keep the last known link when this
        // measurement's taps missed the surface entirely.
        if let support = r.supportSurfaceId { obj.supportSurfaceId = support }
        applyDetectorLabel(r.detection, into: &obj, now: now)
        obj.lastMeasuredAt = now
        obj.seenStreak += 1
        if obj.seenStreak >= RegistryCoherence.establishTicks { obj.established = true }
    }

    /// v3 §5 — what a detector label is worth. SEMANTIC labels (display-
    /// allowlisted taxonomy at real confidence) fill EMPTY identity through the
    /// weaker local-label debounce — they never dethrone a held label or evict a
    /// VLM candidate's progress (the phone must understand its context without
    /// the PC; the VLM outranks via `debounce`). HINT labels ("machine",
    /// "textile") only fill `classHint` — priors/gates/templates conditioning —
    /// never `label`, never the wire, never a chip.
    private static let genericDetectorLabels: Set<String> = ["object"]

    private static func applyDetectorLabel(_ detection: Detection2D,
                                           into obj: inout SpatailObject,
                                           now: TimeInterval) {
        guard let label = detection.label, !label.isEmpty,
              !genericDetectorLabels.contains(label.lowercased()) else { return }
        if detection.isSemanticLabel {
            RegistryFusion.attachLocalLabel(label, confidence: detection.confidence,
                                            at: now, into: &obj)
        } else if obj.classHint == nil {
            obj.classHint = label
        }
    }

    // MARK: - Form ingest (Form Engine → registry, immutable results on main)

    /// Fold fitted forms in. MEASURED forms replace the OBB with the profile-derived
    /// one and carry the measured "cap" part (which supersedes the §3 heuristic
    /// slice); PRIOR forms re-seat the OBB on the support surface. A fresh measured
    /// form is never displaced by an incoming prior.
    func applyForms(_ results: [FittedFormResult], now: TimeInterval) {
        guard !results.isEmpty else { return }
        var updated = objects
        var changed = false

        for result in results {
            // Merge-alias aware: a pass in flight during a coherence merge can
            // deliver a fit for a merged-away loser — it lands on the survivor
            // (whose cloud the Form Engine already inherited), not the floor.
            let targetId = canonicalId(result.objectId)
            guard let idx = updated.firstIndex(where: { $0.id == targetId })
            else { continue }
            var obj = updated[idx]

            if result.form.source == .prior,
               let existing = obj.form, existing.source == .measured,
               let at = obj.formUpdatedAt, now - at < Self.measuredFormRetention {
                continue   // fresh measurement outranks a guess
            }

            obj.form = result.form
            obj.formUpdatedAt = result.fittedAt
            if let obb = result.obb { obj.obb = obb }

            if let capRegion = result.capRegion {
                upsertMeasuredCap(region: capRegion, into: &obj)
            } else if result.form.source == .measured {
                // A refit that finds NO cap step retires a previously measured cap
                // (VLM-boxed parts fall back to normal identity resolution).
                if let pi = obj.parts.firstIndex(where: {
                    $0.isMeasured && Self.topSliceLabels.contains($0.label.lowercased())
                }) {
                    if obj.parts[pi].box == nil {
                        obj.parts.remove(at: pi)
                    } else {
                        obj.parts[pi].measured = nil
                    }
                }
            }

            updated[idx] = obj
            changed = true
        }

        guard changed else { return }

        // A fitted/prior-seated OBB can land on top of a duplicate the center
        // gate missed — the coherence passes also run after applyForms
        // (support first, v3 §0: fresh extents can change who contains whom).
        RegistryCoherence.assignSupportLinks(&updated)
        _ = runMergePass(on: &updated, matches: [:])
        RegistryCoherence.assignDisplayWorthiness(&updated)
        for obj in updated { syncAnchor(for: obj) }

        objects = updated   // single publish
    }

    /// Keep part identity stable across refits (no id churn for SwiftUI/renderer).
    private func upsertMeasuredCap(region: OrientedBox, into obj: inout SpatailObject) {
        if let pi = obj.parts.firstIndex(where: {
            Self.topSliceLabels.contains($0.label.lowercased())
        }) {
            obj.parts[pi].region = region
            obj.parts[pi].measured = true
            obj.parts[pi].confidence = max(obj.parts[pi].confidence, 0.9)
        } else {
            obj.parts.append(SpatailPart(label: "cap", box: nil, region: region,
                                         confidence: 0.9, measured: true))
        }
    }

    // MARK: - Native furniture ingest (RoomPlan — v3 §2)

    /// One native furniture assertion: Apple's RoomPlan model saying "there is a
    /// sofa HERE, this big". Identity is semantic; geometry is a strong prior
    /// (`.roomplan`), never allowed to displace fresh measured form.
    struct NativeFurniture {
        let label: String
        let obb: OrientedBox
        let confidence: Float
    }

    /// Fold RoomPlan's furniture set in. Matching an existing object adopts the
    /// label (local-label path — the VLM still outranks) and snaps geometry to
    /// the model's box unless a fresh MEASURED form owns it; unmatched furniture
    /// mints established entities directly (the model has been watching longer
    /// than one detector tick).
    func ingestNativeFurniture(_ furniture: [NativeFurniture], now: TimeInterval) {
        guard !furniture.isEmpty else { return }
        var updated = objects

        for native in furniture {
            let gate = max(0.5 * native.obb.meanExtent,
                           FormPriors.furniturePrior(for: native.label)?.gate ?? 0.3)
            let matchIdx = updated.indices.filter { idx in
                let obj = updated[idx]
                guard RegistryCoherence.verticalOverlapRatio(obj.obb, native.obb)
                        > RegistryCoherence.iouVerticalOverlapMin else { return false }
                let dx = obj.obb.center.x - native.obb.center.x
                let dz = obj.obb.center.z - native.obb.center.z
                return sqrt(dx * dx + dz * dz) <= gate
                    || RegistryCoherence.xzIoU(obj.obb, native.obb) >= 0.3
            }.min { a, b in
                simd_distance(updated[a].obb.center, native.obb.center)
                    < simd_distance(updated[b].obb.center, native.obb.center)
            }

            let roomplanForm = ObjectForm(
                kind: .box,
                dimensions: ["width": max(native.obb.extents.x, native.obb.extents.z),
                             "depth": min(native.obb.extents.x, native.obb.extents.z),
                             "height": native.obb.extents.y],
                source: .roomplan, arcCoverage: 0, residual: 0)

            if let idx = matchIdx {
                var obj = updated[idx]
                RegistryFusion.attachLocalLabel(native.label,
                                                confidence: native.confidence,
                                                at: now, into: &obj)
                if obj.classHint == nil { obj.classHint = native.label }
                let measuredFresh = obj.form?.source == .measured
                    && obj.formUpdatedAt.map { now - $0 < Self.formFreshnessSeconds }
                        ?? false
                if !measuredFresh {
                    obj.obb = native.obb
                    obj.form = roomplanForm
                    obj.formUpdatedAt = now
                }
                obj.lastMeasuredAt = now
                obj.established = true
                updated[idx] = obj
            } else {
                var minted = SpatailObject(obb: native.obb,
                                           form: roomplanForm,
                                           established: true,
                                           firstSeenAt: now,
                                           classHint: native.label,
                                           lastMeasuredAt: now)
                minted.formUpdatedAt = now
                RegistryFusion.attachLocalLabel(native.label,
                                                confidence: native.confidence,
                                                at: now, into: &minted)
                updated.append(minted)
            }
        }

        // Native boxes can bridge fragments the gates missed — full coherence.
        RegistryCoherence.assignSupportLinks(&updated)
        _ = runMergePass(on: &updated, matches: [:])
        RegistryCoherence.assignDisplayWorthiness(&updated)
        for obj in updated { syncAnchor(for: obj) }

        objects = updated   // single publish
    }

    // MARK: - Identity attach (~1 Hz)

    /// Fuse a `vision.identification` into the registry (spec §1.3 + §3).
    /// - Parameters:
    ///   - projector: projects a world OBB into the identified frame's normalized
    ///     captured-image space (for IoU against `detections[].box`).
    ///   - resolver: depth-resolves a part box into a world OBB.
    func applyIdentity(primary: String,
                       confidence: Float,
                       detections: [Detection2D],
                       parts: [(label: String, box: CGRect?, confidence: Float)],
                       primaryAttributes: ObjectAttributes? = nil,
                       frameTimestamp: TimeInterval,
                       projector: @escaping (OrientedBox) -> CGRect?,
                       resolver: @escaping (CGRect) -> OrientedBox?) {
        guard !objects.isEmpty else { return }
        var updated = objects

        // Project every object's OBB into the identified frame once.
        var projected: [Int: CGRect] = [:]
        for (oi, obj) in updated.enumerated() {
            if let rect = projector(obj.obb) { projected[oi] = rect }
        }

        // IoU pairing, greedy best-first, one-to-one (spec: attach to the best match
        // with IoU ≥ 0.3).
        struct Pair { let detectionIdx: Int; let objectIdx: Int; let iou: Double }
        var pairs: [Pair] = []
        for (di, det) in detections.enumerated() {
            guard det.label?.isEmpty == false else { continue }
            for (oi, rect) in projected {
                let iou = detectionIoU(det.box, rect)
                if iou >= Self.iouThreshold {
                    pairs.append(Pair(detectionIdx: di, objectIdx: oi, iou: iou))
                }
            }
        }
        pairs.sort { $0.iou > $1.iou }

        var usedDetections = Set<Int>()
        var usedObjects = Set<Int>()
        var primaryObjectIdx: Int?
        var primaryBestIoU = 0.0

        for pair in pairs {
            guard !usedDetections.contains(pair.detectionIdx),
                  !usedObjects.contains(pair.objectIdx) else { continue }
            usedDetections.insert(pair.detectionIdx)
            usedObjects.insert(pair.objectIdx)

            let det = detections[pair.detectionIdx]
            guard let candidate = det.label else { continue }
            var obj = updated[pair.objectIdx]
            RegistryFusion.debounce(label: candidate, confidence: det.confidence,
                                    at: frameTimestamp, into: &obj)
            updated[pair.objectIdx] = obj

            // Track which object carries the PRIMARY identification — parts attach
            // to it (spec §1.3: parts describe sub-parts of the primary object).
            if candidate.caseInsensitiveCompare(primary) == .orderedSame,
               pair.iou > primaryBestIoU {
                primaryBestIoU = pair.iou
                primaryObjectIdx = pair.objectIdx
            }
        }

        // Parts + dossier land on the PRIMARY object (spec §1.3: parts/attributes
        // describe the primary).
        if let pi = primaryObjectIdx {
            var parent = updated[pi]
            if !parts.isEmpty {
                Self.mergeIdentifiedParts(parts, into: &parent, resolver: resolver)
            }
            if let primaryAttributes, !primaryAttributes.isEmpty {
                var dossier = parent.attributes ?? ObjectAttributes()
                dossier.merge(primaryAttributes)
                parent.attributes = dossier
                parent.attributesUpdatedAt = frameTimestamp
            }
            updated[pi] = parent
        }

        // Label adoption flips display eligibility — reassign before publishing.
        RegistryCoherence.assignDisplayWorthiness(&updated)

        objects = updated   // single publish
    }

    /// Parts → sub-regions of a parent object. MEASURED parts (Form Engine —
    /// e.g. the cap found as a radius step in the fitted profile) survive every
    /// identity tick and SUPERSEDE the VLM box / §3 heuristic slice for the same
    /// part; only unmeasured labels resolve through the depth-grid path.
    private static func mergeIdentifiedParts(
        _ parts: [(label: String, box: CGRect?, confidence: Float)],
        into parent: inout SpatailObject,
        resolver: (CGRect) -> OrientedBox?) {
        let measured = parent.parts.filter(\.isMeasured)
        var newParts = measured
        for part in parts {
            let partLabel = part.label.lowercased()
            let superseded = measured.contains {
                let m = $0.label.lowercased()
                return m == partLabel
                    || (Self.topSliceLabels.contains(m)
                        && Self.topSliceLabels.contains(partLabel))
            }
            if superseded { continue }
            let region: OrientedBox?
            if let box = part.box, let raw = resolver(box) {
                region = RegistryFusion.clamp(region: raw, into: parent.obb)
            } else if part.box == nil,
                      Self.topSliceLabels.contains(partLabel) {
                region = RegistryFusion.topSlice(of: parent.obb,
                                                 fraction: Self.topSliceFraction)
            } else {
                region = nil
            }
            newParts.append(SpatailPart(label: part.label, box: part.box,
                                        region: region, confidence: part.confidence))
        }
        parent.parts = newParts
    }

    // MARK: - Focus results (v3 §7 — one object, deep identity)

    /// Fold a `vision.focus.result` into ONE object: label through the full VLM
    /// debounce, dossier merged, parts resolved through the caller's
    /// (keyframe-true) resolver. Alias-aware — a result for a merged-away id
    /// lands on the survivor.
    func applyFocusResult(objectId: UUID,
                          label: String?, confidence: Float,
                          attributes: ObjectAttributes?,
                          parts: [(label: String, box: CGRect?, confidence: Float)],
                          now: TimeInterval,
                          resolver: (CGRect) -> OrientedBox?) {
        let target = canonicalId(objectId)
        guard let idx = objects.firstIndex(where: { $0.id == target }) else { return }
        var updated = objects
        var obj = updated[idx]

        if let label, !label.isEmpty {
            RegistryFusion.debounce(label: label, confidence: confidence,
                                    at: now, into: &obj)
        }
        if let attributes, !attributes.isEmpty {
            var dossier = obj.attributes ?? ObjectAttributes()
            dossier.merge(attributes)
            obj.attributes = dossier
            obj.attributesUpdatedAt = now
        }
        if !parts.isEmpty {
            Self.mergeIdentifiedParts(parts, into: &obj, resolver: resolver)
        }

        updated[idx] = obj
        RegistryCoherence.assignDisplayWorthiness(&updated)
        objects = updated   // single publish
    }

    // MARK: - Anchor management

    /// Create the object's world anchor, or re-seat it when the center drifted.
    /// ARAnchors are immutable → moving one is remove + add with the same identity key.
    private func syncAnchor(for obj: SpatailObject) {
        let transform = Self.anchorTransform(for: obj.obb)
        if let existing = anchors[obj.id] {
            let current = SIMD3<Float>(existing.transform.columns.3.x,
                                       existing.transform.columns.3.y,
                                       existing.transform.columns.3.z)
            guard simd_distance(current, obj.obb.center) > Self.anchorMoveThreshold
            else { return }
            session?.remove(anchor: existing)
        }
        let anchor = ARAnchor(name: "spatail.object.\(obj.id.uuidString)",
                              transform: transform)
        anchors[obj.id] = anchor
        session?.add(anchor: anchor)
    }

    private func removeAnchor(for id: UUID) {
        guard let anchor = anchors.removeValue(forKey: id) else { return }
        session?.remove(anchor: anchor)
    }

    /// Translation to the OBB center + rotation about +Y by its yaw.
    private static func anchorTransform(for obb: OrientedBox) -> simd_float4x4 {
        let c = cos(obb.yaw), s = sin(obb.yaw)
        return simd_float4x4(
            SIMD4<Float>(c, 0, -s, 0),
            SIMD4<Float>(0, 1, 0, 0),
            SIMD4<Float>(s, 0, c, 0),
            SIMD4<Float>(obb.center.x, obb.center.y, obb.center.z, 1))
    }
}
