// MockDetectionService.swift
//
// The placeholder VISION layer. Emits a single high-confidence detection
// in the CENTER of the screen so the rest of the pipeline
// (resolver → engine → runtime) can be validated end-to-end before any
// real model exists. This is the deliberate "mock first" step — it keeps
// the build from getting stuck on the model problem.
//
// Cadence (the "every 1 second" rule) is owned by the session loop, not by
// this service — the loop throttles how often it calls `detect`. The mock
// just answers "here is the thing in the middle" each time it is asked.

import Foundation
import CoreGraphics

final class MockDetectionService: DetectionService {
    let sourceName = "Mock"

    /// The label the mock reports. Kept here so the test scenario's
    /// "Detected Object" string has one source of truth.
    var label: String = "Detected Object"

    /// Box size as a fraction of the viewport. A near-square box in the
    /// middle of the frame.
    var boxSize: CGSize = CGSize(width: 0.32, height: 0.32)

    /// Tiny per-call wobble so the marker visibly tracks "live" rather than
    /// looking frozen. Derived from frameId (no wall-clock / RNG needed —
    /// those are unavailable in some contexts and would hurt replayability).
    var wobble: Double = 0.015

    func detect(_ input: DetectionInput,
                completion: @escaping (Result<[SpatailDetection], Error>) -> Void) {
        // A gentle deterministic drift around screen center, driven by the
        // frame counter so successive frames differ slightly.
        let phase = Double(input.frameId) * 0.25
        let dx = sin(phase) * wobble
        let dy = cos(phase) * wobble

        let w = boxSize.width
        let h = boxSize.height
        let box = SpatailScreenRect(
            x: 0.5 - w / 2 + dx,
            y: 0.5 - h / 2 + dy,
            width: w,
            height: h,
        )

        // Stable id ("mock-primary") so the runtime UPDATES the same anchor
        // every tick instead of churning create/destroy. This is what lets
        // the blue marker glide rather than blink.
        let detection = SpatailDetection(
            id: "mock-primary",
            label: label,
            confidence: 0.92,
            screenBoundingBox: box,
            worldHit: nil,   // ← the spatial resolver fills this in
        )

        completion(.success([detection]))
    }
}
