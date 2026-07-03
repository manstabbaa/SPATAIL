// PerceptionDebugModel.swift — the observable snapshot the Truth Overlay (W6) reads.
//
// Ported from SPATAILMobileAR's PerceptionDebugModel, trimmed to exactly what the
// overlay needs to tell the truth about the live loop (spec §0 law 5 — if you can't
// see what it saw, it doesn't ship):
//   • the last detections (what Vision said, in captured-image space),
//   • per-detection resolve outcomes (did the depth grid survive? what OBB came out?),
//   • per-placement rationale strings (why each element landed where it did — fed by
//     the runtime/placement side via `recordPlacementRationales`).
//
// The pipeline writes once per detection tick; views read. No UI concerns in here,
// and only Core + Foundation types so the overlay renders it however it wants.

import Foundation
import CoreGraphics

@MainActor
final class PerceptionDebugModel: ObservableObject {

    /// One detection's journey through the resolver, flattened for display.
    struct ResolveOutcome: Identifiable {
        let id: UUID
        let label: String?
        let confidence: Float
        /// Normalized captured-image-space box (top-left origin).
        let box: CGRect
        let isResolved: Bool
        /// Grid taps that survived foreground clustering (0 when unresolved).
        let tapCoverage: Float
        /// "depth-grid" | "raycast-grid" | "unresolved".
        let method: String
        /// "0.31×0.24×0.18 m @ (1.2, 0.8, -0.4)" — nil when unresolved.
        let obbSummary: String?
        let supportSurfaceId: String?
        /// One human-readable line for the overlay row.
        let note: String

        init(detection: Detection2D, resolved: ResolvedDetection?, method: String) {
            self.id = detection.id
            self.label = detection.label
            self.confidence = detection.confidence
            self.box = detection.box
            self.method = method
            if let r = resolved {
                self.isResolved = true
                self.tapCoverage = r.tapCoverage
                let e = r.obb.extents, c = r.obb.center
                self.obbSummary = String(
                    format: "%.2f×%.2f×%.2f m @ (%.2f, %.2f, %.2f)",
                    e.x, e.y, e.z, c.x, c.y, c.z)
                self.supportSurfaceId = r.supportSurfaceId
                let support = r.supportSurfaceId.map { " on \($0)" } ?? ""
                self.note = String(format: "%@ via %@ (%.0f%% taps)%@",
                                   detection.label ?? "object", method,
                                   r.tapCoverage * 100, support)
            } else {
                self.isResolved = false
                self.tapCoverage = 0
                self.obbSummary = nil
                self.supportSurfaceId = nil
                self.note = "\(detection.label ?? "object") — \(method)"
            }
        }
    }

    // MARK: Published truth

    @Published private(set) var sourceName = ""
    /// What the active detector said on the last tick (captured-image space).
    @Published private(set) var lastDetections: [Detection2D] = []
    /// How each of those detections resolved (or didn't).
    @Published private(set) var lastOutcomes: [ResolveOutcome] = []
    /// Wall-clock cost of the last inference, milliseconds.
    @Published private(set) var lastInferenceMillis: Double = 0
    /// ARFrame timestamp of the last completed tick (device-uptime clock).
    @Published private(set) var lastTickFrameTimestamp: TimeInterval = 0
    /// Last detector error, nil when the last tick succeeded.
    @Published private(set) var lastError: String?
    /// Why each placed element landed where it did — pushed by the placement side.
    @Published private(set) var placementRationales: [String] = []

    // Convenience read-outs for the overlay header.
    var resolvedCount: Int { lastOutcomes.filter(\.isResolved).count }
    var detectionCount: Int { lastDetections.count }

    // MARK: Writers

    /// Called by the pipeline whenever the active service (name) changes.
    func noteSource(_ name: String) {
        if name != sourceName { sourceName = name }
    }

    /// Called once per completed detection tick.
    func recordTick(sourceName: String,
                    detections: [Detection2D],
                    outcomes: [ResolveOutcome],
                    inferenceMillis: Double,
                    frameTimestamp: TimeInterval,
                    error: String?) {
        self.sourceName = sourceName
        self.lastDetections = detections
        self.lastOutcomes = outcomes
        self.lastInferenceMillis = inferenceMillis
        self.lastTickFrameTimestamp = frameTimestamp
        self.lastError = error
    }

    /// Called by the placement side (ExperienceRuntime / AppModel) with the
    /// per-placement `reason` strings from the latest applied plan.
    func recordPlacementRationales(_ lines: [String]) {
        placementRationales = lines
    }
}
