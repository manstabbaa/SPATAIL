// MockDetectionService.swift — the placeholder VISION layer.
//
// Emits a single high-confidence detection near the CENTER of the frame so the rest of
// the pipeline (resolver → registry → runtime) can be validated end-to-end without a
// model or a network. Cadence is owned by the pipeline (it throttles how often `detect`
// is called); the mock just answers "here is the thing in the middle" each time.
//
// Even the mock goes through the InferenceExecutor so its threading contract is
// IDENTICAL to the real services (latch before dispatch, completion on main) — flipping
// mock ↔ real in the Truth Overlay can never change the pipeline's concurrency shape.

import Foundation
import CoreGraphics

final class MockDetectionService: DetectionService {
    let sourceName = "Mock"
    // Dev-only stand-in; Core's DetectionSourceKind is a closed wire set, so the mock
    // masquerades as the on-device Vision source. Mock output never reaches the wire.
    let sourceKind: DetectionSourceKind = .appleVision

    /// The label the mock reports.
    var label = "Detected Object"
    /// Box size as a fraction of the (sensor) image. Near-square, mid-frame.
    var boxSize = CGSize(width: 0.32, height: 0.32)
    /// Tiny per-call wobble so the marker visibly tracks "live" rather than looking
    /// frozen. Derived from frameId — deterministic, replayable.
    var wobble = 0.015

    private let executor = InferenceExecutor(label: "dev.spatail.perception.mock")

    @discardableResult
    func detect(_ input: DetectionInput,
                completion: @escaping (Result<[Detection2D], Error>) -> Void) -> Bool {
        let label = self.label
        let boxSize = self.boxSize
        let wobble = self.wobble
        let sourceKind = self.sourceKind

        return executor.submit {
            let phase = Double(input.frameId) * 0.25
            let dx = sin(phase) * wobble
            let dy = cos(phase) * wobble
            let box = CGRect(x: 0.5 - boxSize.width / 2 + dx,
                             y: 0.5 - boxSize.height / 2 + dy,
                             width: boxSize.width,
                             height: boxSize.height)
            let detection = Detection2D(label: label,
                                        confidence: 0.92,
                                        box: box,
                                        source: sourceKind,
                                        frameTimestamp: input.timestamp)
            DispatchQueue.main.async { completion(.success([detection])) }
        }
    }
}
