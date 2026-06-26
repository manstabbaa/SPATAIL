// PerceptionDebugModel.swift
//
// Observable snapshot of the live pipeline for the debug HUD. The session
// coordinator writes to it once per detection tick (and updates tracking
// quality every frame); the SwiftUI overlay reads it. Keeping debug state
// in its own object means the pipeline itself carries no UI concerns.

import Foundation
import Combine

@MainActor
final class PerceptionDebugModel: ObservableObject {
    /// Master toggle for the on-screen debug overlay.
    @Published var enabled: Bool = true

    /// Latest perception facts.
    @Published var sourceName: String = "Mock"
    @Published var intent: String = ""
    @Published var detections: [SpatailDetection] = []
    @Published var sceneContext: SpatailSceneContext = .empty
    @Published var trackingQuality: SpatailTrackingQuality = .unavailable
    @Published var frameId: Int = 0

    /// Latest placement decision (so the HUD can show WHAT and WHY).
    @Published var lastPlan: SpatailPlacementPlan?
    @Published var activeAnchors: Int = 0

    /// Called on each detection tick.
    func update(frame: SpatailPerceptionFrame,
                plan: SpatailPlacementPlan,
                source: String,
                activeAnchors: Int) {
        self.detections = frame.detections
        self.sceneContext = frame.sceneContext
        self.trackingQuality = frame.sceneContext.trackingQuality
        self.frameId = frame.frameId
        self.lastPlan = plan
        self.sourceName = source
        self.activeAnchors = activeAnchors
    }

    /// Called every frame — cheap, just the tracking light.
    func updateTracking(_ quality: SpatailTrackingQuality) {
        if quality != trackingQuality { trackingQuality = quality }
    }

    // Convenience read-outs for the HUD.
    var resolvedCount: Int { detections.filter { $0.isResolved }.count }
    var placementCount: Int { lastPlan?.placements.count ?? 0 }
}
