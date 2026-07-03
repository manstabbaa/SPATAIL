// LivePerceptionPipeline.swift — THE LIVE MEASUREMENT LOOP (LIVE_BRAIN_SPEC §0 + §3).
//
//   hub frames (~1.5 Hz, main) ─▶ DetectionService (dedicated serial bg queue)
//                                        │  completion hopped to main
//                                        ▼
//                        SpatialResolver (depth-grid / raycast fallback)
//                                        │
//                                        ▼
//                        ObjectRegistry.ingest (main) → published SpatailObjects
//
// Threading shape (spec §0 law 2): the ONLY heavy work is inference, and it runs on the
// service's dedicated serial queue behind a single-flight latch. Everything else — the
// 25 depth taps per box, the OBB fit, the ingest — is microseconds and stays on main,
// where ARView/ARFrame access is legal. A whole-cycle guard on top of the service latch
// means a slow inference can never stack resolution work either (drop-on-busy).
//
// The pipeline also owns the two identity-attach helpers the registry consumes
// (spec §3 identity attach — wired by AppModel):
//   • `projector(for:)` — world OBB → normalized rect in the CURRENT frame's captured-
//     image space via ARCamera.projectPoint (current-frame projection is the spec's
//     intent: objects don't move; a tiny ring buffer of recent (timestamp, camera)
//     pairs picks the nearest pose when the identified frame is slightly stale).
//   • `resolvePartBox(_:)` — depth-grid a VLM part box against the current frame.

import Foundation
import ARKit
import RealityKit
import Combine
import CoreGraphics
import UIKit
import simd

@MainActor
final class LivePerceptionPipeline: ObservableObject {

    // MARK: Configuration

    /// Detection cadence (spec: ~1–2 Hz).
    static let detectionHz: Double = 1.5
    /// Ring depth for (timestamp, camera) pairs — a few seconds of poses.
    private static let cameraRingCapacity = 24
    /// A ring pose older than this relative to the requested timestamp is stale;
    /// fall through to the live camera.
    private static let maxPoseAge: TimeInterval = 0.75

    // MARK: Observable state

    /// Active detector backend; switch at runtime from the Truth Overlay.
    @Published var source: DetectionSource {
        didSet {
            guard source != oldValue else { return }
            service = source.makeService()
            debug.noteSource(service.sourceName)
            // Invalidate any in-flight cycle from the OLD service: bump the generation
            // so its late completion is ignored, and release the latch so the new
            // service starts detecting on the next tick.
            cycleGeneration += 1
            cycleInFlight = false
        }
    }
    /// Trimmed observable snapshot for the Truth Overlay (W6).
    let debug = PerceptionDebugModel()

    // MARK: Wiring

    private let hub: ARSessionHub
    private let registry: ObjectRegistry
    private let surfacesProvider: () -> [RoomSurface]
    private var service: DetectionService
    /// Perception v2 — the Form Engine. Mask-based sampling, multi-view fusion,
    /// parametric fitting, transparency fallback. All off-main on its own
    /// single-flight serial queue; results come back as immutable values.
    private let formEngine = FormEngine()

    private static let consumerId = "perception.live-pipeline"
    private var running = false
    /// The frame consumer is registered once (the hub replaces by id anyway);
    /// `running` gates ticks so stop()/start() cycles are free.
    private var consumerRegistered = false
    /// Whole-cycle single-flight (detect → resolve → ingest). The DetectionService has
    /// its own latch; this one additionally covers the main-thread tail of the cycle.
    private var cycleInFlight = false
    /// Bumped whenever an in-flight cycle is invalidated (detector swapped). A stale
    /// completion carrying an old generation must not unlatch the current cycle.
    private var cycleGeneration = 0
    private var cycleCounter = 0
    private var cameraRing: [(timestamp: TimeInterval, camera: ARCamera)] = []

    init(hub: ARSessionHub,
         registry: ObjectRegistry,
         surfacesProvider: @escaping () -> [RoomSurface]) {
        self.hub = hub
        self.registry = registry
        self.surfacesProvider = surfacesProvider
        let initial: DetectionSource = .appleVision   // real + model-free by default
        self.source = initial
        self.service = initial.makeService()
        debug.noteSource(service.sourceName)

        // Form Engine results land on MAIN as immutable values → registry + debug.
        formEngine.onResults = { [weak self] publication in
            guard let self else { return }
            self.registry.applyForms(publication.results, now: publication.timestamp)
            self.debug.recordFormPass(maskMillis: publication.maskMillis,
                                      degraded: publication.degraded,
                                      resultCount: publication.results.count,
                                      notes: publication.notes)
        }
    }

    // MARK: Lifecycle

    func start() {
        guard !running else { return }
        running = true
        // World-anchor management for measured objects (spec §3) — the registry mints
        // ARAnchors; give it the session they belong to.
        registry.attachSession(hub.arView.session)
        // Register the frame consumer once; `running` gates the ticks so stop()/start()
        // cycles don't need a hub-side remove API.
        if !consumerRegistered {
            consumerRegistered = true
            hub.addFrameConsumer(id: Self.consumerId,
                                 hz: Self.detectionHz) { [weak self] frame in
                self?.tick(frame)
            }
        }
    }

    func stop() {
        running = false
    }

    // MARK: Tick (main, ~1.5 Hz)

    private func tick(_ frame: ARFrame) {
        guard running else { return }

        // Always feed the pose ring — projections want poses even on dropped ticks.
        recordCamera(frame)

        guard !cycleInFlight else { return }   // previous cycle still running → drop

        let interfaceOrientation = currentInterfaceOrientation()
        cycleCounter += 1
        let input = DetectionInput(pixelBuffer: frame.capturedImage,
                                   orientation: Self.cgOrientation(for: interfaceOrientation),
                                   viewportSize: hub.arView.bounds.size,
                                   frameId: cycleCounter,
                                   timestamp: frame.timestamp)

        cycleInFlight = true
        let generation = cycleGeneration
        let startedAt = Date()
        // The service latches BEFORE dispatching to its serial queue and calls the
        // completion on MAIN (the ported fix). `accepted == false` = dropped-on-busy.
        let accepted = service.detect(input) { [weak self] result in
            // Resolve against the SAME frame the boxes were detected in — its depth
            // map and camera pose are the ones the boxes are aligned with. The frame
            // is held for only one inference (~100–300 ms at 1.5 Hz).
            self?.finishCycle(result: result,
                              frame: frame,
                              interfaceOrientation: interfaceOrientation,
                              inferenceMillis: -startedAt.timeIntervalSinceNow * 1000,
                              generation: generation)
        }
        if !accepted { cycleInFlight = false }
    }

    /// Main-thread tail of the cycle: resolve → ingest → debug snapshot.
    private func finishCycle(result: Result<[Detection2D], Error>,
                             frame: ARFrame,
                             interfaceOrientation: UIInterfaceOrientation,
                             inferenceMillis: Double,
                             generation: Int) {
        // A stale completion (detector swapped mid-inference) must neither unlatch
        // the current cycle nor ingest against the new detector's stream.
        guard generation == cycleGeneration else { return }
        defer { cycleInFlight = false }
        guard running else { return }

        let detections: [Detection2D]
        var errorText: String?
        switch result {
        case .success(let d): detections = d
        case .failure(let e):
            detections = []
            errorText = String(describing: e)
        }

        let surfaces = surfacesProvider()
        let resolver = SpatialResolver()
        var resolvedList: [ResolvedDetection] = []
        var outcomes: [PerceptionDebugModel.ResolveOutcome] = []

        for detection in detections {
            if let outcome = resolver.resolve(detection,
                                              frame: frame,
                                              arView: hub.arView,
                                              interfaceOrientation: interfaceOrientation,
                                              surfaces: surfaces) {
                resolvedList.append(outcome.resolved)
                outcomes.append(.init(detection: detection,
                                      resolved: outcome.resolved,
                                      method: outcome.method.rawValue))
            } else {
                outcomes.append(.init(detection: detection, resolved: nil,
                                      method: "unresolved"))
            }
        }

        // Ingest with the rescue projector (prior-form objects match by image IoU
        // when their world OBB is seated but the single-frame depth is garbage).
        let liveCamera = frame.camera
        let matches = registry.ingest(resolvedList, now: frame.timestamp,
                                      projector: { Self.project(obb: $0, camera: liveCamera) })

        // Perception v2: hand the frame + matched detections to the Form Engine
        // (single-flight, off-main; drop-on-busy keeps the 1–2 Hz clock honest).
        submitFormPass(resolved: resolvedList, matches: matches, frame: frame,
                       interfaceOrientation: interfaceOrientation)

        debug.recordTick(sourceName: service.sourceName,
                         detections: detections,
                         outcomes: outcomes,
                         inferenceMillis: inferenceMillis,
                         frameTimestamp: frame.timestamp,
                         error: errorText)
    }

    // MARK: Form Engine feed (main-side capture, background everything else)

    private func submitFormPass(resolved: [ResolvedDetection],
                                matches: [UUID: UUID],
                                frame: ARFrame,
                                interfaceOrientation: UIInterfaceOrientation) {
        guard !resolved.isEmpty else { return }
        // Stage 1 samples the SMOOTHED scene depth (temporal filtering suits
        // accumulation); plain sceneDepth is the fallback carrier of the same maps.
        guard let sceneDepth = frame.smoothedSceneDepth ?? frame.sceneDepth
        else { return }   // no LiDAR → no form engine (raycast grid already ran)

        let orientation = Self.cgOrientation(for: interfaceOrientation)
        let camPos = frame.camera.transform.columns.3

        var candidates: [FormEngine.Candidate] = []
        for r in resolved {
            guard let objectId = matches[r.detection.id] else { continue }
            // Class label: adopted registry identity first, detector label second.
            let label = registry.objects.first(where: { $0.id == objectId })?.label
                ?? r.detection.label
            let support = supportObservation(underBox: r.detection.box, frame: frame,
                                             orientation: orientation,
                                             interfaceOrientation: interfaceOrientation)
            candidates.append(FormEngine.Candidate(objectId: objectId,
                                                   sensorBox: r.detection.box,
                                                   classLabel: label,
                                                   measurementCenter: r.obb.center,
                                                   support: support))
        }
        guard !candidates.isEmpty else { return }

        let pass = FormEngine.Pass(capturedImage: frame.capturedImage,
                                   depthMap: sceneDepth.depthMap,
                                   confidenceMap: sceneDepth.confidenceMap,
                                   intrinsics: frame.camera.intrinsics,
                                   imageResolution: frame.camera.imageResolution,
                                   cameraTransform: frame.camera.transform,
                                   cameraPosition: SIMD3(camPos.x, camPos.y, camPos.z),
                                   orientation: orientation,
                                   timestamp: frame.timestamp,
                                   candidates: candidates,
                                   liveObjectIds: Set(registry.objects.map(\.id)))
        formEngine.submit(pass)
    }

    /// Raycast the horizontal surface just under a detection's silhouette (upright-
    /// space bottom-center) — the seat + range the transparency fallback scales
    /// priors with. Main-actor (ARView raycast); one cheap cast per detection.
    private func supportObservation(underBox box: CGRect, frame: ARFrame,
                                    orientation: CGImagePropertyOrientation,
                                    interfaceOrientation: UIInterfaceOrientation)
        -> FormEngine.SupportObservation? {
        let viewSize = hub.arView.bounds.size
        guard viewSize.width > 1, viewSize.height > 1 else { return nil }

        let upright = MaskProvider.uprightRect(fromSensor: box, orientation: orientation)
        let uprightBottom = CGPoint(x: upright.midX,
                                    y: min(upright.maxY + 0.02, 0.995))
        let sensorPoint = MaskProvider.sensorPoint(fromUpright: uprightBottom,
                                                   orientation: orientation)
        let display = frame.displayTransform(for: interfaceOrientation,
                                             viewportSize: viewSize)
        let viewNorm = sensorPoint.applying(display)
        guard (0...1).contains(viewNorm.x), (0...1).contains(viewNorm.y) else { return nil }
        let screenPoint = CGPoint(x: viewNorm.x * viewSize.width,
                                  y: viewNorm.y * viewSize.height)

        let hit = hub.arView.raycast(from: screenPoint,
                                     allowing: .existingPlaneGeometry,
                                     alignment: .horizontal).first
            ?? hub.arView.raycast(from: screenPoint,
                                  allowing: .estimatedPlane,
                                  alignment: .horizontal).first
        guard let hit else { return nil }
        let c = hit.worldTransform.columns.3
        let world = SIMD3<Float>(c.x, c.y, c.z)
        // Pinhole z-depth of the seat (silhouette scaling wants Z, not ray range).
        let camSpace = frame.camera.transform.inverse * SIMD4<Float>(c.x, c.y, c.z, 1)
        guard camSpace.z < -0.01 else { return nil }
        return FormEngine.SupportObservation(point: world, zDepth: -camSpace.z)
    }

    // MARK: Identity-attach helpers (consumed by ObjectRegistry.applyIdentity)

    /// A closure projecting a world OBB into the identified frame's normalized
    /// captured-image space (top-left origin) — the space `vision.identification`
    /// boxes live in. Uses the ring pose nearest `frameTimestamp`, else the live
    /// camera (current-frame projection: objects don't move, poses barely matter).
    func projector(for frameTimestamp: TimeInterval) -> (OrientedBox) -> CGRect? {
        var camera: ARCamera?
        if let nearest = cameraRing.min(by: {
            abs($0.timestamp - frameTimestamp) < abs($1.timestamp - frameTimestamp)
        }), abs(nearest.timestamp - frameTimestamp) <= Self.maxPoseAge {
            camera = nearest.camera
        }
        camera = camera ?? hub.currentFrame?.camera

        guard let cam = camera else { return { _ in nil } }
        return { obb in Self.project(obb: obb, camera: cam) }
    }

    /// Depth-resolve a VLM part box (normalized captured-image space) against the
    /// CURRENT frame into a world OBB. Returns nil when nothing usable is in view.
    func resolvePartBox(_ box: CGRect) -> OrientedBox? {
        guard let frame = hub.currentFrame else { return nil }
        return SpatialResolver().resolveBox(box,
                                            frame: frame,
                                            arView: hub.arView,
                                            interfaceOrientation: currentInterfaceOrientation())
    }

    // MARK: Projection math

    /// Project the 8 OBB corners with ARCamera.projectPoint into captured-image pixel
    /// space (.landscapeRight = sensor-native), normalize, and take the bounding rect.
    /// nil when the OBB is behind the camera or entirely off-frame.
    private static func project(obb: OrientedBox, camera: ARCamera) -> CGRect? {
        let res = camera.imageResolution
        guard res.width > 0, res.height > 0 else { return nil }

        let hx = obb.extents.x / 2, hy = obb.extents.y / 2, hz = obb.extents.z / 2
        let c = cos(obb.yaw), s = sin(obb.yaw)
        let inverseView = camera.transform.inverse

        var minX = CGFloat.greatestFiniteMagnitude
        var minY = CGFloat.greatestFiniteMagnitude
        var maxX = -CGFloat.greatestFiniteMagnitude
        var maxY = -CGFloat.greatestFiniteMagnitude
        var inFront = 0

        for sx in [Float(-1), 1] {
            for sy in [Float(-1), 1] {
                for sz in [Float(-1), 1] {
                    let lx = sx * hx, ly = sy * hy, lz = sz * hz
                    // R_y(yaw) about the box center.
                    let world = SIMD3<Float>(obb.center.x + c * lx + s * lz,
                                             obb.center.y + ly,
                                             obb.center.z - s * lx + c * lz)
                    // Camera looks down -Z in its own space; require in-front.
                    let camSpace = inverseView * SIMD4<Float>(world.x, world.y, world.z, 1)
                    guard camSpace.z < -0.01 else { continue }
                    inFront += 1
                    let px = camera.projectPoint(world,
                                                 orientation: .landscapeRight,
                                                 viewportSize: res)
                    minX = min(minX, px.x); maxX = max(maxX, px.x)
                    minY = min(minY, px.y); maxY = max(maxY, px.y)
                }
            }
        }
        // Need most of the box in front of the camera for an honest rect.
        guard inFront >= 4 else { return nil }

        let rect = CGRect(x: minX / res.width,
                          y: minY / res.height,
                          width: (maxX - minX) / res.width,
                          height: (maxY - minY) / res.height)
            .intersection(CGRect(x: 0, y: 0, width: 1, height: 1))
        guard !rect.isNull, rect.width > 0.005, rect.height > 0.005 else { return nil }
        return rect
    }

    // MARK: Orientation

    private func currentInterfaceOrientation() -> UIInterfaceOrientation {
        hub.arView.window?.windowScene?.interfaceOrientation ?? .portrait
    }

    /// Orientation that makes the captured (camera-native) image upright for the
    /// current interface orientation — fed to Vision so models see upright content.
    private static func cgOrientation(for interface: UIInterfaceOrientation) -> CGImagePropertyOrientation {
        switch interface {
        case .portrait:           return .right
        case .portraitUpsideDown: return .left
        case .landscapeLeft:      return .down
        case .landscapeRight:     return .up
        default:                  return .right
        }
    }

    private func recordCamera(_ frame: ARFrame) {
        cameraRing.append((frame.timestamp, frame.camera))
        if cameraRing.count > Self.cameraRingCapacity {
            cameraRing.removeFirst(cameraRing.count - Self.cameraRingCapacity)
        }
    }
}
