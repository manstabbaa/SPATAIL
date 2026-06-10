import Foundation
#if os(iOS)
import ARKit
import RealityKit
import UIKit

// ObjectAnchoringController — the TRACKED stream's runtime layer (iOS 27 object
// tracking, WWDC26 session 283; docs/apple-objecttracking/).
//
// Flow: download the served .referenceobject (post-compile delivery — the same file
// drives visionOS later) → register it on the LIVE ARSession (keeping plane
// detection so the placed-stream fallback and RoomModel keep working) → surface the
// resulting ARObjectAnchor as an AnchorEntity the runtime mounts the experience on.
//
// detectionObjects = mostly-stationary objects (stable pose, low power).
// trackingObjects  = handheld/moving objects (per-frame pose, high power) — iOS 27.
//
// It does NOT own the session delegate: ModularARView.Coordinator stays the
// delegate (it feeds RoomModelBuilder) and FORWARDS ARObjectAnchor events here.

@MainActor
final class ObjectAnchoringController {
    enum Phase: Equatable { case idle, downloading, searching, anchored, paused }

    private(set) var phase: Phase = .idle
    private weak var view: ARView?
    private var anchoring = ModularExperience.Anchoring()
    private var mount: AnchorEntity?
    private var anchorIdentifier: UUID?
    private var fallbackTimer: Timer?

    /// The object anchor is live — present the experience on this mount.
    var onMountReady: ((AnchorEntity) -> Void)?
    /// isTracked flipped (object left/re-entered view). §3.4: pause when lost.
    var onTrackingChanged: ((Bool) -> Void)?
    /// Couldn't go tracked (unsupported / download failed / search timeout) —
    /// degrade to the placed stream (anchoring.fallback == "world").
    var onFallback: ((String) -> Void)?
    var onStatus: ((String) -> Void)?

    /// Object tracking with ML .referenceobject files needs iOS 27.
    static var isSupported: Bool {
        if #available(iOS 27.0, *) { return true }
        return false
    }

    // MARK: - lifecycle

    /// Begin the tracked flow: download the reference object, reconfigure the live
    /// session, and search. Falls back (via onFallback) instead of throwing.
    func begin(anchoring: ModularExperience.Anchoring, view: ARView,
               searchTimeout: TimeInterval = 25) {
        stop()
        self.view = view
        self.anchoring = anchoring
        guard Self.isSupported else {
            onFallback?("Object tracking needs iOS 27 — placing in your space instead.")
            return
        }
        guard !anchoring.referenceObjectUrl.isEmpty else {
            onFallback?("No trackable for this object yet — placing in your space instead.")
            return
        }
        phase = .downloading
        onStatus?("getting the tracker for \(anchoring.objectId.isEmpty ? "your object" : anchoring.objectId)…")
        Task { await self.downloadAndRun(searchTimeout: searchTimeout) }
    }

    func stop() {
        fallbackTimer?.invalidate(); fallbackTimer = nil
        if let mount, let view { view.scene.removeAnchor(mount) }
        mount = nil; anchorIdentifier = nil; phase = .idle
    }

    private func downloadAndRun(searchTimeout: TimeInterval) async {
        guard #available(iOS 27.0, *) else { return }
        do {
            let local = try await Self.download(serverPath: anchoring.referenceObjectUrl)
            let refObj = try ARReferenceObject(archiveURL: local)
            guard let view else { return }
            // Re-run the SAME world-tracking setup with the reference object added —
            // no reset options, so planes/world map (and the RoomModel) survive.
            let cfg = ARWorldTrackingConfiguration()
            cfg.planeDetection = [.horizontal, .vertical]
            cfg.environmentTexturing = .automatic
            if anchoring.tracking == "tracking" {
                cfg.trackingObjects = [refObj]      // handheld: per-frame pose (iOS 27)
            } else {
                cfg.detectionObjects = [refObj]     // stationary: low power
            }
            view.session.run(cfg)
            phase = .searching
            onStatus?("point your camera at the object…")
            fallbackTimer = Timer.scheduledTimer(withTimeInterval: searchTimeout, repeats: false) { [weak self] _ in
                Task { @MainActor in
                    guard let self, self.phase == .searching else { return }
                    self.onFallback?("Couldn't find the object — placing in your space instead.")
                }
            }
        } catch {
            print("SPATAIL tracked: reference object failed: \(error)")
            onFallback?("Tracker unavailable — placing in your space instead.")
        }
    }

    /// Download a server-relative .referenceobject into Documents/SpatailTrackables
    /// (cached by filename — re-prompting the same object is instant).
    static func download(serverPath: String) async throws -> URL {
        let baseStr = GenerativeClient.baseURL.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !baseStr.isEmpty, let base = URL(string: baseStr),
              let url = URL(string: serverPath, relativeTo: base) else {
            throw URLError(.badURL)
        }
        let dir = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("SpatailTrackables", isDirectory: true)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let dst = dir.appendingPathComponent(url.lastPathComponent)
        if FileManager.default.fileExists(atPath: dst.path) { return dst }
        let (tmp, resp) = try await URLSession.shared.download(from: url)
        if let http = resp as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            throw URLError(.badServerResponse)
        }
        try? FileManager.default.removeItem(at: dst)
        try FileManager.default.moveItem(at: tmp, to: dst)
        return dst
    }

    // MARK: - forwarded ARObjectAnchor events (from ModularARView.Coordinator)

    func objectAnchorsAdded(_ anchors: [ARObjectAnchor]) {
        guard phase == .searching, let view, let anchor = anchors.first else { return }
        fallbackTimer?.invalidate(); fallbackTimer = nil
        anchorIdentifier = anchor.identifier
        let m = AnchorEntity(anchor: anchor)
        view.scene.addAnchor(m)
        mount = m
        phase = .anchored
        onStatus?("locked onto the object")
        UIImpactFeedbackGenerator(style: .medium).impactOccurred()
        onMountReady?(m)
    }

    func objectAnchorsUpdated(_ anchors: [ARObjectAnchor]) {
        guard let id = anchorIdentifier,
              let anchor = anchors.first(where: { $0.identifier == id }) else { return }
        let tracked = anchor.isTracked
        let wasPaused = (phase == .paused)
        if !tracked && anchoring.pauseOnTrackingLoss && phase == .anchored {
            phase = .paused
            onStatus?("lost the object — point your camera back at it")
            onTrackingChanged?(false)
        } else if tracked && wasPaused {
            phase = .anchored
            onStatus?("locked onto the object")
            onTrackingChanged?(true)
        }
    }

    func objectAnchorsRemoved(_ anchors: [ARObjectAnchor]) {
        guard let id = anchorIdentifier, anchors.contains(where: { $0.identifier == id }) else { return }
        stop()
        onFallback?("Tracking ended — placing in your space instead.")
    }
}
#endif
