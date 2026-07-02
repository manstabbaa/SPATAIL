// LivePerceptionCoordinator.swift
//
// ════════════════════════════════════════════════════════════════════════
//  THE LIVE DETECTION LOOP — where the whole pipeline runs every tick.
// ════════════════════════════════════════════════════════════════════════
//
//   ARFrame ─▶ DetectionService ─▶ SpatialResolver ─▶ PerceptionFrame
//             (Vision: WHAT)       (ARKit: WHERE)
//                                                         │
//                                   PerceptionPlacementEngine (SPATAIL: WHAT
//                                   SHOULD APPEAR) ─▶ SpatailPlacementPlan
//                                                         │
//                                   PerceptionPlacementRuntime (RealityKit:
//                                   RENDER + ANCHOR)
//
// This is a SECOND, self-contained UIViewRepresentable that lives ALONGSIDE
// the existing ARExperienceCoordinator — that file is untouched. The
// per-frame tick uses RealityKit's SceneEvents.Update (delivered on the
// main thread), so there is no AR-session-delegate threading to manage.
//
// Detection is throttled to ~1 Hz (the "fake detection every 1 second"
// rule); billboards update every frame so labels/panels stay readable.

import SwiftUI
import ARKit
import RealityKit
import Combine

struct LivePerceptionCoordinator: UIViewRepresentable {
    /// Current user intent (drives the engine). Updated from the SwiftUI view.
    var intent: String
    /// Which detector backend to use (mock ↔ real).
    var source: DetectionSource
    /// Shared debug state the HUD reads.
    let debug: PerceptionDebugModel

    func makeCoordinator() -> Coordinator {
        Coordinator(intent: intent, source: source, debug: debug)
    }

    func makeUIView(context: Context) -> ARView {
        let arView = ARView(frame: .zero,
                            cameraMode: .ar,
                            automaticallyConfigureSession: false)
        arView.environment.lighting.intensityExponent = 0.5

        let config = ARWorldTrackingConfiguration()
        config.planeDetection = [.horizontal, .vertical]
        config.environmentTexturing = .automatic
        // LiDAR scene mesh enables object-face hits + the depth upgrade path.
        if ARWorldTrackingConfiguration.supportsSceneReconstruction(.mesh) {
            config.sceneReconstruction = .mesh
        }
        // Provide depth for the resolver's future LiDAR refine (guarded).
        if ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth) {
            config.frameSemantics.insert(.sceneDepth)
        }
        arView.session.run(config)

        context.coordinator.attach(arView: arView)
        return arView
    }

    func updateUIView(_ uiView: ARView, context: Context) {
        // SwiftUI re-invokes this when intent / source change.
        context.coordinator.set(intent: intent)
        context.coordinator.set(source: source)
    }

    static func dismantleUIView(_ uiView: ARView, coordinator: Coordinator) {
        coordinator.teardown()
    }

    // ------------------------------------------------------------------

    @MainActor
    final class Coordinator {
        private var intent: String
        private var source: DetectionSource
        private let debug: PerceptionDebugModel

        // Pipeline stages.
        private var detector: DetectionService
        private let resolver = SpatialResolver()
        private let engine = PerceptionPlacementEngine()
        private let runtime = PerceptionPlacementRuntime()
        /// Real models available to place (scanned from bundle + Documents).
        private let catalog = AssetCatalogBuilder.build()

        private weak var arView: ARView?
        // Qualify to RealityKit: both RealityKit and Combine export a
        // top-level `Cancellable`, so an unqualified annotation is ambiguous.
        private var updateSub: RealityKit.Cancellable?

        // Detection cadence.
        private let detectionInterval: TimeInterval = 1.0   // ← "every 1 second"
        private var lastDetectionTime: TimeInterval = -.greatestFiniteMagnitude
        private var isDetecting = false
        private var frameCounter = 0

        init(intent: String, source: DetectionSource, debug: PerceptionDebugModel) {
            self.intent = intent
            self.source = source
            self.debug = debug
            self.detector = source.makeService()
            self.debug.sourceName = detector.sourceName
        }

        func attach(arView: ARView) {
            self.arView = arView
            runtime.attach(to: arView)
            // Per-frame tick on the main thread.
            updateSub = arView.scene.subscribe(to: SceneEvents.Update.self) { [weak self] _ in
                self?.onFrame()
            }
        }

        func teardown() {
            updateSub?.cancel()
            updateSub = nil
            runtime.clear()
        }

        // MARK: - Intent / source updates from SwiftUI

        func set(intent: String) { self.intent = intent }

        func set(source: DetectionSource) {
            guard source != self.source else { return }
            self.source = source
            self.detector = source.makeService()
            self.debug.sourceName = detector.sourceName
            // New detector → drop the old anchors and re-detect promptly.
            runtime.clear()
            lastDetectionTime = -.greatestFiniteMagnitude
        }

        // MARK: - Per-frame tick

        private func onFrame() {
            guard let arView = arView, let frame = arView.session.currentFrame else { return }
            let cameraPosition = frame.camera.transform.columns.3.xyz

            // Every frame: keep labels/panels facing the user + tracking light.
            runtime.updateBillboards(cameraPosition: cameraPosition)
            debug.updateTracking(SpatailTrackingQuality(frame.camera.trackingState))

            // Throttled: run detection at ~1 Hz, never overlapping.
            guard !isDetecting,
                  frame.timestamp - lastDetectionTime >= detectionInterval else { return }
            runDetectionCycle(frame: frame, in: arView, cameraPosition: cameraPosition)
        }

        /// One full pass: detect → resolve → decide → render.
        private func runDetectionCycle(frame: ARFrame, in arView: ARView, cameraPosition: SIMD3<Float>) {
            isDetecting = true
            lastDetectionTime = frame.timestamp
            frameCounter += 1
            let frameId = frameCounter

            let input = DetectionInput(
                pixelBuffer: frame.capturedImage,
                orientation: Self.cgOrientation(for: arView),
                viewportSize: arView.bounds.size,
                frameId: frameId,
                timestamp: frame.timestamp,
            )

            // ── STAGE 1: VISION — detect WHAT is in the frame (may be async).
            detector.detect(input) { [weak self] result in
                // Hop to main before touching the AR scene / RealityKit.
                DispatchQueue.main.async {
                    guard let self = self,
                          let arView = self.arView,
                          let liveFrame = arView.session.currentFrame else {
                        self?.isDetecting = false
                        return
                    }
                    let raw = (try? result.get()) ?? []
                    let camNow = liveFrame.camera.transform.columns.3.xyz

                    // ── STAGE 2: ARKIT — resolve WHERE each detection lives.
                    //    (2D normalized box → world hit). The bridge.
                    let resolved = self.resolver.resolveAll(raw, in: arView, frame: liveFrame)
                    let sceneContext = SceneContextBuilder.build(from: liveFrame, arView: arView)

                    // Assemble the perception packet.
                    let perception = SpatailPerceptionFrame(
                        frameId: frameId,
                        timestamp: liveFrame.timestamp,
                        cameraTransform: liveFrame.camera.transform,
                        detections: resolved,
                        sceneContext: sceneContext,
                    )

                    // ── STAGE 3: SPATAIL — decide WHAT SHOULD APPEAR there.
                    let plan = self.engine.makePlan(intent: self.intent, frame: perception, catalog: self.catalog)

                    // ── STAGE 4: REALITYKIT — render + anchor the result.
                    self.runtime.apply(plan, cameraPosition: camNow)

                    self.debug.update(
                        frame: perception, plan: plan,
                        source: self.detector.sourceName,
                        activeAnchors: self.runtime.activePlacementCount,
                    )
                    self.isDetecting = false
                }
            }
        }

        // MARK: - Orientation

        /// Orientation to feed Vision so the captured (camera-native) image is
        /// upright for the current interface orientation. Irrelevant to the
        /// mock; correct for real Core ML detection.
        private static func cgOrientation(for arView: ARView) -> CGImagePropertyOrientation {
            let interface = arView.window?.windowScene?.interfaceOrientation ?? .portrait
            switch interface {
            case .portrait:           return .right
            case .portraitUpsideDown: return .left
            case .landscapeLeft:      return .down
            case .landscapeRight:     return .up
            default:                  return .right
            }
        }
    }
}
