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
    /// Seconds unseen before an unpinned object expires.
    private static let expirySeconds: TimeInterval = 10
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

    // MARK: Published state

    /// The live set of measured (and possibly identified) objects, wire-aligned with
    /// `room.update.objects[]`.
    @Published private(set) var objects: [SpatailObject] = []

    /// Objects a placed experience anchors to — exempt from expiry (spec §3:
    /// "unless anchored by a placed experience"). The runtime/AppModel maintains this.
    var pinnedObjectIds: Set<UUID> = []

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
    func arAnchor(for id: UUID) -> ARAnchor? { anchors[id] }

    // MARK: - Measurement ingest (1–2 Hz)

    func ingest(_ resolved: [ResolvedDetection], now: TimeInterval) {
        var updated = objects

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
            obj.obb = RegistryFusion.smooth(old: obj.obb, new: r.obb, alpha: Self.smoothing)
            // Support surface refresh on ingest; keep the last known link when this
            // measurement's taps missed the surface entirely.
            if let support = r.supportSurfaceId { obj.supportSurfaceId = support }
            obj.lastMeasuredAt = now
            updated[pair.objectIdx] = obj
        }

        // Unmatched measurements mint new objects.
        for (di, r) in resolved.enumerated() where !usedDetections.contains(di) {
            updated.append(SpatailObject(obb: r.obb,
                                         supportSurfaceId: r.supportSurfaceId,
                                         lastMeasuredAt: now))
        }

        // Expiry — ~10 s unseen, unless pinned by a placed experience.
        var expired: [UUID] = []
        updated.removeAll { obj in
            let stale = now - obj.lastMeasuredAt > Self.expirySeconds
            let drop = stale && !pinnedObjectIds.contains(obj.id)
            if drop { expired.append(obj.id) }
            return drop
        }
        for id in expired { removeAnchor(for: id) }

        // Anchor sync: create/update a world anchor per object at its OBB center.
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

        // Parts → sub-regions of the primary object.
        if let pi = primaryObjectIdx, !parts.isEmpty {
            var parent = updated[pi]
            parent.parts = parts.map { part in
                let region: OrientedBox?
                if let box = part.box, let raw = resolver(box) {
                    region = RegistryFusion.clamp(region: raw, into: parent.obb)
                } else if part.box == nil,
                          Self.topSliceLabels.contains(part.label.lowercased()) {
                    region = RegistryFusion.topSlice(of: parent.obb,
                                                     fraction: Self.topSliceFraction)
                } else {
                    region = nil
                }
                return SpatailPart(label: part.label, box: part.box,
                                   region: region, confidence: part.confidence)
            }
            updated[pi] = parent
        }

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
