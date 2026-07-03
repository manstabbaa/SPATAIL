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

        registry.ingest(resolvedList, now: frame.timestamp)

        debug.recordTick(sourceName: service.sourceName,
                         detections: detections,
                         outcomes: outcomes,
                         inferenceMillis: inferenceMillis,
                         frameTimestamp: frame.timestamp,
                         error: errorText)
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
