// ARSessionHub.swift — the ONE ARSession/ARView owner for the whole app.
//
// Everything that needs camera frames or anchor callbacks goes through here:
//   • frame consumers: addFrameConsumer(id:hz:) — invoked on MAIN at ~hz with the
//     live ARFrame; consumers hop off-main themselves (spec §0 law 2). The hub
//     never buffers frames — latest-wins by construction, nothing is queued.
//   • delegate multiplexer: addSessionDelegate(_:) — didAdd/didUpdate/didRemove
//     anchors (plus session health callbacks) fan out to every registered
//     listener (RoomScannerService, object-anchor trackers, …).
//
// ARKit configuration per the interface contract: world tracking with
// .meshWithClassification scene reconstruction when the device supports it,
// horizontal + vertical planes, sceneDepth when supported. ARSession's
// delegateQueue is left nil, so every callback below arrives on the main queue —
// which is exactly the actor this class lives on.

import Foundation
import ARKit
import RealityKit

@MainActor
final class ARSessionHub: NSObject, ObservableObject {

    // MARK: The single AR surface

    let arView: ARView

    /// The live frame, straight from the session (always the newest — never cached).
    var currentFrame: ARFrame? { arView.session.currentFrame }

    // MARK: Frame consumers (throttled fan-out, main thread)

    private struct FrameConsumer {
        let hz: Double
        var lastFired: TimeInterval
        let handler: (ARFrame) -> Void
    }

    private var consumers: [String: FrameConsumer] = [:]

    /// Register (or replace) a frame consumer. `handler` runs on MAIN at ~`hz`;
    /// anything heavy must hop to a background queue inside the handler.
    func addFrameConsumer(id: String, hz: Double, _ handler: @escaping (ARFrame) -> Void) {
        consumers[id] = FrameConsumer(hz: hz, lastFired: 0, handler: handler)
    }

    func removeFrameConsumer(id: String) {
        consumers[id] = nil
    }

    // MARK: Session delegate multiplexer

    private final class WeakDelegate {
        weak var value: ARSessionDelegate?
        init(_ value: ARSessionDelegate) { self.value = value }
    }

    private var subDelegates: [WeakDelegate] = []

    /// Add a listener for anchor + session callbacks. Held weakly — owners keep
    /// their services alive (AppModel does), the hub never retains them.
    func addSessionDelegate(_ d: ARSessionDelegate) {
        subDelegates.removeAll { $0.value == nil || $0.value === d }
        subDelegates.append(WeakDelegate(d))
    }

    // MARK: Init + configuration

    override init() {
        arView = ARView(frame: .zero, cameraMode: .ar, automaticallyConfigureSession: false)
        super.init()
        // Cheap wins for a phone screen full of live geometry.
        arView.renderOptions.insert([.disableMotionBlur, .disableDepthOfField])
        arView.session.delegate = self          // delegateQueue nil → main queue
        arView.session.run(Self.makeConfiguration())
    }

    /// World tracking + the richest scene understanding the device offers.
    static func makeConfiguration() -> ARWorldTrackingConfiguration {
        let config = ARWorldTrackingConfiguration()
        config.planeDetection = [.horizontal, .vertical]
        config.environmentTexturing = .automatic
        if ARWorldTrackingConfiguration.supportsSceneReconstruction(.meshWithClassification) {
            config.sceneReconstruction = .meshWithClassification
        } else if ARWorldTrackingConfiguration.supportsSceneReconstruction(.mesh) {
            config.sceneReconstruction = .mesh
        }
        if ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth) {
            config.frameSemantics.insert(.sceneDepth)
        }
        if ARWorldTrackingConfiguration.supportsFrameSemantics(.smoothedSceneDepth) {
            config.frameSemantics.insert(.smoothedSceneDepth)
        }
        return config
    }
}

// MARK: - ARSessionDelegate (fan-out)

extension ARSessionHub: ARSessionDelegate {

    func session(_ session: ARSession, didUpdate frame: ARFrame) {
        // Throttled consumer fan-out (per-consumer hz), then the delegate chain.
        let now = frame.timestamp
        for (id, consumer) in consumers {
            if now - consumer.lastFired >= 1.0 / max(consumer.hz, 0.1) {
                consumers[id]?.lastFired = now
                consumer.handler(frame)
            }
        }
        for d in subDelegates { d.value?.session?(session, didUpdate: frame) }
    }

    func session(_ session: ARSession, didAdd anchors: [ARAnchor]) {
        for d in subDelegates { d.value?.session?(session, didAdd: anchors) }
    }

    func session(_ session: ARSession, didUpdate anchors: [ARAnchor]) {
        for d in subDelegates { d.value?.session?(session, didUpdate: anchors) }
    }

    func session(_ session: ARSession, didRemove anchors: [ARAnchor]) {
        for d in subDelegates { d.value?.session?(session, didRemove: anchors) }
    }

    // Session health — forwarded so listeners can react honestly (spec §0 law 3).

    func session(_ session: ARSession, didFailWithError error: Error) {
        for d in subDelegates { d.value?.session?(session, didFailWithError: error) }
    }

    func sessionWasInterrupted(_ session: ARSession) {
        for d in subDelegates { d.value?.sessionWasInterrupted?(session) }
    }

    func sessionInterruptionEnded(_ session: ARSession) {
        for d in subDelegates { d.value?.sessionInterruptionEnded?(session) }
    }

    func sessionShouldAttemptRelocalization(_ session: ARSession) -> Bool {
        true    // keep the room's coordinate frame after interruptions
    }

    func session(_ session: ARSession, cameraDidChangeTrackingState camera: ARCamera) {
        for d in subDelegates { d.value?.session?(session, cameraDidChangeTrackingState: camera) }
    }
}
