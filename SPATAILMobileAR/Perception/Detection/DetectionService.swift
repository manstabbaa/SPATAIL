// DetectionService.swift
//
// THE VISION LAYER SEAM. "Vision detects WHAT the object is."
//
// A clean protocol so the rest of the pipeline never knows whether a
// detection came from a mock, an Apple Vision request, a Core ML / YOLO
// model, or a server-side VLM. Swap implementations freely; downstream
// (resolver → engine → runtime) is untouched.
//
// This layer is deliberately AR-FREE in spirit: it is handed a raw camera
// image and returns 2D detections with `worldHit == nil`. The SPATIAL
// layer (SpatialResolver) is the only place that turns 2D into 3D.

import Foundation
import CoreVideo
import ImageIO
import CoreGraphics

/// A frame of pixels handed to a detector, plus the metadata it needs to
/// produce correctly-oriented normalized boxes.
struct DetectionInput {
    /// The ARFrame's captured image (YCbCr pixel buffer from the camera).
    let pixelBuffer: CVPixelBuffer
    /// Orientation to apply so the model sees an upright image.
    let orientation: CGImagePropertyOrientation
    /// The size, in points, of the ARView the boxes will be drawn over.
    /// Detectors return NORMALIZED boxes, so this is informational / for
    /// detectors that need an aspect ratio.
    let viewportSize: CGSize
    /// Monotonic frame counter from the session loop.
    let frameId: Int
    /// ARFrame timestamp.
    let timestamp: TimeInterval
}

/// Anything that can find objects in a camera frame.
///
/// Implementations MUST return detections whose `screenBoundingBox` is in
/// SpatailScreenRect convention (top-left origin, normalized [0,1]) and
/// MUST leave `worldHit == nil` — resolution is not their job.
protocol DetectionService: AnyObject {
    /// Human-readable name for the debug HUD ("Mock", "Core ML", "VLM").
    var sourceName: String { get }

    /// Run detection. May be async internally; calls `completion` on an
    /// unspecified queue — callers hop to main before touching the AR scene.
    func detect(_ input: DetectionInput,
                completion: @escaping (Result<[SpatailDetection], Error>) -> Void)
}

// ---------------------------------------------------------------------------
// Source switch — the single place that picks an implementation.
// ---------------------------------------------------------------------------

/// Selectable detector backends. The live view binds a picker to this so a
/// developer can flip mock ↔ real detection at runtime for debugging
/// (follow-up requirement: "Add a simple switch between mock and real").
enum DetectionSource: String, CaseIterable, Identifiable, Hashable {
    case mock
    case appleVision   // REAL, model-free — Apple Vision built-ins (works today)
    case coreML        // REAL, custom model — see CoreMLVisionDetectionService

    var id: String { rawValue }

    var displayName: String {
        switch self {
        case .mock:        return "Mock"
        case .appleVision: return "Vision"
        case .coreML:      return "Core ML"
        }
    }

    /// Build the concrete service. Kept here so callers depend only on the
    /// protocol. `appleVision` works with no model; `coreML` falls back to a
    /// stub that logs a TODO until a custom model ships — neither crashes.
    @MainActor
    func makeService() -> DetectionService {
        switch self {
        case .mock:        return MockDetectionService()
        case .appleVision: return VisionDetectionService()
        case .coreML:      return CoreMLVisionDetectionService()
        }
    }
}
