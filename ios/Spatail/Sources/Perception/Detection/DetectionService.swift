// DetectionService.swift — the vision-layer seam. "Vision detects WHAT is in the frame."
//
// Ported from SPATAILMobileAR/Perception/Detection WITH the threading fix mandated by
// LIVE_BRAIN_SPEC §0 law 2: every implementation runs inference on a DEDICATED SERIAL
// background queue (qos .userInitiated), sets a SINGLE-FLIGHT latch BEFORE dispatching,
// and hops its completion back to the MAIN queue. Callers never see a completion on a
// background thread and can never stack up inference work — a busy detector DROPS the
// frame (latest-wins, spec §0 law 3) and `detect` returns false.
//
// Output currency is Core's `Detection2D` (SpatailCore.swift): a normalized [0–1],
// top-left-origin box in the CAPTURED IMAGE's space (sensor-native orientation). That is
// the one canonical 2D space of the whole pipeline — the depth map, the camera
// intrinsics, ARCamera.projectPoint, and the JPEG frames streamed to the PC brain all
// live there, so depth-grid resolution and identity-attach IoU need no per-orientation
// conversions downstream. Detectors that run Vision with an upright orientation convert
// their boxes back into sensor space before returning (see `SensorSpace`).

import Foundation
import CoreVideo
import CoreGraphics
import ImageIO

// MARK: - Input

/// A frame of pixels handed to a detector, plus the metadata it needs to produce
/// correctly-oriented, sensor-space normalized boxes.
struct DetectionInput {
    /// The ARFrame's captured image (YCbCr pixel buffer, sensor-native orientation).
    let pixelBuffer: CVPixelBuffer
    /// Orientation that makes the pixel buffer upright for the current UI orientation.
    /// Detectors feed this to Vision so models see upright content, then map the
    /// resulting boxes BACK to sensor space.
    let orientation: CGImagePropertyOrientation
    /// Size, in points, of the ARView (informational; some detectors want aspect).
    let viewportSize: CGSize
    /// Monotonic detection-cycle counter from the pipeline.
    let frameId: Int
    /// ARFrame.timestamp (device-uptime clock — same clock as `vision.identification`'s
    /// `frameTimestamp` on the wire).
    let timestamp: TimeInterval
}

// MARK: - Protocol

/// Anything that can find objects in a camera frame.
///
/// Implementations MUST:
///   • run inference on their own dedicated serial background queue (never the caller's
///     thread, never main),
///   • latch single-flight BEFORE dispatching (return false = frame dropped while busy),
///   • call `completion` on the MAIN queue,
///   • return `Detection2D` boxes in normalized, top-left, sensor-image space.
protocol DetectionService: AnyObject {
    /// Human-readable name for the Truth Overlay ("Mock", "Apple Vision", "Core ML").
    var sourceName: String { get }
    /// The Core wire tag stamped on emitted detections.
    var sourceKind: DetectionSourceKind { get }

    /// Run detection. Returns false when a previous inference is still in flight —
    /// the input is dropped and `completion` is NOT called (single-flight, spec law 2/3).
    @discardableResult
    func detect(_ input: DetectionInput,
                completion: @escaping (Result<[Detection2D], Error>) -> Void) -> Bool
}

// MARK: - The threading fix, in one reusable piece

/// Owns the dedicated serial inference queue and the single-flight latch.
/// `submit` latches BEFORE dispatch and releases when the work item returns; the
/// completion the work item produces is hopped to main by the work item itself
/// (see the services). Thread-safe; callable from any thread.
final class InferenceExecutor {
    private let queue: DispatchQueue
    private let lock = NSLock()
    private var inFlight = false

    init(label: String) {
        // Dedicated SERIAL background queue, qos userInitiated — spec §0 law 2.
        queue = DispatchQueue(label: label, qos: .userInitiated)
    }

    /// Latch, then dispatch. Returns false (and runs nothing) when already in flight.
    @discardableResult
    func submit(_ work: @escaping () -> Void) -> Bool {
        lock.lock()
        if inFlight {
            lock.unlock()
            return false        // drop-on-busy: the caller skips this frame
        }
        inFlight = true         // ← latched BEFORE dispatch, never after
        lock.unlock()

        queue.async { [weak self] in
            work()
            guard let self else { return }
            self.lock.lock()
            self.inFlight = false
            self.lock.unlock()
        }
        return true
    }
}

// MARK: - Sensor-space conversion

/// Maps Vision's results back into SPATAIL's canonical 2D space: the CAPTURED image,
/// normalized [0–1], origin TOP-LEFT, sensor-native orientation.
///
/// Vision, when handed an orientation, reports `boundingBox` normalized with a
/// BOTTOM-LEFT origin in the UPRIGHT (rotated) image. Two steps undo that:
///   1. flip Y → top-left origin in the upright image,
///   2. inverse-rotate the rect by the orientation → sensor space.
enum SensorSpace {
    /// Vision bounding box (bottom-left, upright space) → sensor-space top-left rect.
    static func rect(fromVisionBox box: CGRect,
                     orientation: CGImagePropertyOrientation) -> CGRect {
        // 1. Bottom-left → top-left inside the upright image.
        let upright = CGRect(x: box.origin.x,
                             y: 1.0 - box.origin.y - box.height,
                             width: box.width,
                             height: box.height)
        return rect(fromUpright: upright, orientation: orientation)
    }

    /// Top-left-origin rect in the upright image → sensor space.
    ///
    /// Derivations (normalized coords, top-left origin everywhere):
    ///   .right — upright = sensor rotated 90° CW:  sensor(x,y) = (uy, 1-ux)
    ///   .left  — upright = sensor rotated 90° CCW: sensor(x,y) = (1-uy, ux)
    ///   .down  — upright = sensor rotated 180°:    sensor(x,y) = (1-ux, 1-uy)
    static func rect(fromUpright r: CGRect,
                     orientation: CGImagePropertyOrientation) -> CGRect {
        switch orientation {
        case .right:
            return CGRect(x: r.minY, y: 1.0 - r.maxX, width: r.height, height: r.width)
        case .left:
            return CGRect(x: 1.0 - r.maxY, y: r.minX, width: r.height, height: r.width)
        case .down:
            return CGRect(x: 1.0 - r.maxX, y: 1.0 - r.maxY, width: r.width, height: r.height)
        default:
            return r
        }
    }
}

// MARK: - Shared geometry helper

/// Intersection-over-union of two normalized top-left rects (dedup + identity attach).
func detectionIoU(_ a: CGRect, _ b: CGRect) -> Double {
    let inter = a.intersection(b)
    guard !inter.isNull, inter.width > 0, inter.height > 0 else { return 0 }
    let interArea = inter.width * inter.height
    let union = a.width * a.height + b.width * b.height - interArea
    return union > 0 ? Double(interArea / union) : 0
}

// MARK: - Source switch

/// Selectable detector backends — the Truth Overlay binds a picker to this so mock ↔
/// real detection can be flipped at runtime.
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

    /// Build the concrete service. `appleVision` works with no model; `coreML`
    /// degrades gracefully until a model ships — neither crashes.
    func makeService() -> DetectionService {
        switch self {
        case .mock:        return MockDetectionService()
        case .appleVision: return VisionDetectionService()
        case .coreML:      return CoreMLVisionDetectionService()
        }
    }
}
