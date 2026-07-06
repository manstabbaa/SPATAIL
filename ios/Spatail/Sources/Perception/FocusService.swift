// FocusService.swift — question-driven + round-robin deep identity (v3 §5/§7).
//
// The ambient identify loop describes ONE primary per ~1 Hz frame from a 960 px
// stream — that is why the keyboard across the room stayed "machine". This
// service closes the gap with FOCUS PASSES: hi-res crops (1920-px keyframes)
// of one object at a time, sent as `vision.focus` with what's wanted from them
// (attributes and/or a question), answered requester-only as
// `vision.focus.result` and folded into the entity dossier.
//
// Two lanes, one in flight total (law 3 — no queues):
//   • ASK lane (priority): AskPlanner calls requestFocus(objectId:question:…);
//     it preempts any pending ambient request (latest-wins) and reports
//     completion so the ask flow can proceed with an enriched registry.
//   • AMBIENT lane: every ~5 s, the stalest display-worthy parent (never
//     identified, or dossier older than ~45 s) gets a default-attributes pass —
//     round-robin deep identity for every established entity, not just the
//     frame's primary.
//
// Honesty: if no recent keyframe shows the target ≥ 96 px, the pass does not
// fire — and the ask lane reports WHY ("too small / not in view"), which the
// UI surfaces as guidance instead of a silent shrug.

import Foundation
import CoreGraphics
import UIKit
import simd

@MainActor
final class FocusService: ObservableObject {

    // MARK: Tunables

    static let ambientInterval: TimeInterval = 5
    /// Identity/dossier older than this is stale enough to re-focus.
    static let staleAfter: TimeInterval = 45
    static let requestTimeout: TimeInterval = 8
    /// The target must project at least this many pixels (min side) in some
    /// recent keyframe for a crop to be worth sending.
    static let minCropSidePx: CGFloat = 96
    /// How many recent keyframes are searched for the best crop source.
    static let keyframeSearchDepth = 12
    static let defaultWanted = ["colors", "materials", "textContent", "brand", "state"]

    // MARK: Failure reasons (ask-lane guidance)

    enum FocusFailure {
        case notStreaming
        case objectGone
        case tooSmallInView     // → "move closer to the <label>"
        case timedOut
    }

    // MARK: State

    @Published private(set) var pendingObjectId: UUID?

    private struct Pending {
        let requestId: String
        let objectId: UUID
        let sentAt: Date
        let keyframeTimestamp: TimeInterval
        /// Normalized UPRIGHT rect the crop covers (box back-mapping space).
        let cropUprightRect: CGRect
        let orientation: CGImagePropertyOrientation
        let isAsk: Bool
        let completion: ((FocusFailure?) -> Void)?
    }
    private var pending: Pending?
    private var streaming = false
    private var loopTask: Task<Void, Never>?

    private weak var pipeline: LivePerceptionPipeline?
    private weak var registry: ObjectRegistry?
    private weak var uplink: VisionUplink?
    /// Crop encode/decode lane — never on main.
    private let cropQueue = DispatchQueue(label: "dev.spatail.perception.focus",
                                          qos: .userInitiated)

    init(pipeline: LivePerceptionPipeline, registry: ObjectRegistry,
         uplink: VisionUplink) {
        self.pipeline = pipeline
        self.registry = registry
        self.uplink = uplink
    }

    // MARK: Lifecycle

    func setStreaming(_ on: Bool) {
        streaming = on
        if on, loopTask == nil { startLoop() }
        if !on { resolvePending(as: .notStreaming) }
    }

    private func startLoop() {
        loopTask = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 1_000_000_000)
                guard let self else { return }
                self.tick()
            }
        }
    }

    private func tick() {
        // Expire a stuck request (engine died mid-crop, etc.).
        if let p = pending, Date().timeIntervalSince(p.sentAt) > Self.requestTimeout {
            resolvePending(as: .timedOut)
        }
        guard streaming, pending == nil else { return }
        guard let target = stalestTarget() else { return }
        _ = fire(objectId: target.id, question: nil, wanted: Self.defaultWanted,
                 isAsk: false, completion: nil)
    }

    /// The display-worthy PARENT most in need of deep identity: never-identified
    /// first, then the oldest dossier/identity beyond the staleness bar.
    private func stalestTarget() -> SpatailObject? {
        guard let registry else { return nil }
        let now = ProcessInfo.processInfo.systemUptime
        let candidates = registry.objects.filter {
            $0.displayWorthy && $0.parentId == nil && $0.established
        }
        if let never = candidates.first(where: { $0.lastIdentifiedAt == nil }) {
            return never
        }
        return candidates
            .filter {
                let identityAge = now - ($0.lastIdentifiedAt ?? 0)
                let dossierAge = now - ($0.attributesUpdatedAt ?? 0)
                return ($0.attributes == nil && identityAge > Self.staleAfter / 3)
                    || min(identityAge, dossierAge) > Self.staleAfter
            }
            .min { ($0.attributesUpdatedAt ?? 0) < ($1.attributesUpdatedAt ?? 0) }
    }

    // MARK: Ask lane (priority — v3 §7)

    /// Fire a question-conditioned focus pass NOW. Preempts whatever is pending
    /// (latest-wins — a superseded ask resolves as timed out; its late result is
    /// ignored by requestId). `completion(nil)` = result applied;
    /// `completion(f)` = why it couldn't (the ask flow turns .tooSmallInView
    /// into user guidance).
    func requestFocus(objectId: UUID, question: String?, wanted: [String]?,
                      completion: @escaping (FocusFailure?) -> Void) {
        guard streaming else { completion(.notStreaming); return }
        resolvePending(as: .timedOut)
        if !fire(objectId: objectId, question: question,
                 wanted: wanted ?? Self.defaultWanted, isAsk: true,
                 completion: completion) {
            completion(.tooSmallInView)
        }
    }

    // MARK: The pass

    /// Pick the best recent keyframe showing the object big and sharp, crop it
    /// off-main, send. Returns false when nothing usable is in visual memory.
    @discardableResult
    private func fire(objectId: UUID, question: String?, wanted: [String]?,
                      isAsk: Bool, completion: ((FocusFailure?) -> Void)?) -> Bool {
        guard let pipeline, let registry, let uplink,
              let object = registry.object(for: objectId) else {
            completion?(.objectGone)
            return false
        }

        // Best crop source: biggest sharp recent view above the size bar.
        var best: (kf: Keyframe, rect: CGRect, score: CGFloat)?
        for kf in pipeline.keyframes.recent(Self.keyframeSearchDepth) {
            guard let rect = kf.projectRect(obb: object.obb) else { continue }
            let sidePx = min(rect.width * kf.imageResolution.width,
                             rect.height * kf.imageResolution.height)
            guard sidePx >= Self.minCropSidePx else { continue }
            let sharpness = CGFloat(max(0.2, 1.5 - CGFloat(kf.motionScore)))
            let score = sidePx * sharpness
            if score > (best?.score ?? 0) { best = (kf, rect, score) }
        }
        guard let best else { return false }

        let requestId = "f-" + UUID().uuidString.lowercased().prefix(12)
        let canonicalId = registry.canonicalId(objectId)
        let orientation = best.kf.orientation
        let kfTimestamp = best.kf.timestamp
        let keyframe = best.kf
        let rect = best.rect

        cropQueue.async { [weak self] in
            guard let crop = KeyframeStore.cropJPEG(from: keyframe, sensorRect: rect)
            else {
                Task { @MainActor [weak self] in
                    guard let self, let p = self.pending,
                          p.requestId == String(requestId) else { return }
                    self.pending = nil
                    self.pendingObjectId = nil
                    p.completion?(.tooSmallInView)
                }
                return
            }
            Task { @MainActor [weak self] in
                guard let self, let uplink = self.uplink else { return }
                // Still the live request? (an ask may have preempted us mid-crop)
                guard self.pending?.requestId == String(requestId) else { return }
                uplink.sendFocus(requestId: String(requestId),
                                 objectId: canonicalId,
                                 question: question, wanted: wanted,
                                 jpeg: crop.jpeg, frameTimestamp: kfTimestamp)
                // Stamp the true covered rect now that the crop is cut.
                if let p = self.pending, p.requestId == String(requestId) {
                    self.pending = Pending(requestId: p.requestId,
                                           objectId: p.objectId,
                                           sentAt: Date(),
                                           keyframeTimestamp: p.keyframeTimestamp,
                                           cropUprightRect: crop.uprightRect,
                                           orientation: p.orientation,
                                           isAsk: p.isAsk,
                                           completion: p.completion)
                }
            }
        }

        pending = Pending(requestId: String(requestId), objectId: canonicalId,
                          sentAt: Date(), keyframeTimestamp: kfTimestamp,
                          cropUprightRect: .zero, orientation: orientation,
                          isAsk: isAsk, completion: completion)
        pendingObjectId = canonicalId
        return true
    }

    // MARK: Result intake (wired from VisionUplink.$lastFocusResult)

    func handle(result: FocusResultWire) {
        guard let p = pending, result.requestId == p.requestId else { return }
        pending = nil
        pendingObjectId = nil

        guard let registry, let pipeline else { return }
        let now = ProcessInfo.processInfo.systemUptime

        // Crop-relative boxes → full-frame SENSOR rects through the crop rect.
        let cropRect = p.cropUprightRect
        let orientation = p.orientation
        func sensorRect(fromCropBox box: [Double]?) -> CGRect? {
            guard let b = box, b.count == 4, cropRect.width > 0 else { return nil }
            let upright = CGRect(x: cropRect.minX + b[0] * cropRect.width,
                                 y: cropRect.minY + b[1] * cropRect.height,
                                 width: b[2] * cropRect.width,
                                 height: b[3] * cropRect.height)
            // Upright → sensor via two corners (axis-aligned stays axis-aligned).
            let c0 = MaskProvider.sensorPoint(fromUpright: CGPoint(x: upright.minX,
                                                                   y: upright.minY),
                                              orientation: orientation)
            let c1 = MaskProvider.sensorPoint(fromUpright: CGPoint(x: upright.maxX,
                                                                   y: upright.maxY),
                                              orientation: orientation)
            return CGRect(x: min(c0.x, c1.x), y: min(c0.y, c1.y),
                          width: abs(c1.x - c0.x), height: abs(c1.y - c0.y))
        }

        let parts: [(label: String, box: CGRect?, confidence: Float)] =
            (result.parts ?? []).map {
                ($0.label, sensorRect(fromCropBox: $0.box), Float($0.confidence))
            }

        registry.applyFocusResult(
            objectId: p.objectId,
            label: result.label,
            confidence: Float(result.confidence ?? 0.6),
            attributes: result.attributes,
            parts: parts,
            now: now,
            resolver: { box in
                pipeline.resolvePartBox(box, frameTimestamp: p.keyframeTimestamp)
            })

        p.completion?(nil)
    }

    private func resolvePending(as failure: FocusFailure) {
        guard let p = pending else { return }
        pending = nil
        pendingObjectId = nil
        p.completion?(failure)
    }
}
