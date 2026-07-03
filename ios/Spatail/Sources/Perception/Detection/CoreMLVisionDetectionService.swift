// CoreMLVisionDetectionService.swift — Apple Vision + Core ML object detection over the
// live ARFrame. Wired end-to-end EXCEPT for the model itself: no `.mlmodel` ships in the
// project yet, so the request builder returns nil and `detect` answers with an empty
// result (and a one-time log) instead of crashing. Drop a model named "ObjectDetector"
// into the bundle and flip the source switch to `.coreML` — no code change needed.
//
// Threading (the mandated fix): model load AND inference happen on the dedicated serial
// inference queue via InferenceExecutor (Core ML model compilation is exactly the kind
// of heavy work spec §0 law 2 bans from main); latch before dispatch; completion on MAIN.

import Foundation
import Vision
import CoreML
import CoreVideo
import CoreGraphics
import ImageIO

final class CoreMLVisionDetectionService: DetectionService {
    let sourceName = "Core ML"
    let sourceKind: DetectionSourceKind = .coreML

    /// Only report detections at/above this confidence.
    var minimumConfidence: VNConfidence = 0.4
    /// Cap how many detections we emit per frame (highest confidence first).
    var maxDetections = 8

    private let executor = InferenceExecutor(label: "dev.spatail.perception.coreml")

    // Built lazily ON THE INFERENCE QUEUE (first detect), never on main.
    private var request: VNCoreMLRequest?
    private var attemptedModelLoad = false
    private var warnedMissingModel = false

    @discardableResult
    func detect(_ input: DetectionInput,
                completion: @escaping (Result<[Detection2D], Error>) -> Void) -> Bool {
        let minimumConfidence = self.minimumConfidence
        let maxDetections = self.maxDetections
        let sourceKind = self.sourceKind

        return executor.submit { [weak self] in
            guard let self else {
                // Service deallocated mid-flight (source switched). Still complete —
                // callers key their own single-flight latch off the completion.
                DispatchQueue.main.async { completion(.success([])) }
                return
            }

            // ── Model (loaded once, on this serial queue — safe without extra locks
            //    because the executor guarantees one work item at a time) ──
            if !self.attemptedModelLoad {
                self.attemptedModelLoad = true
                self.request = Self.makeRequest()
            }
            guard let request = self.request else {
                // No model bundled yet — degrade gracefully so the live loop still
                // runs; it just finds nothing until a model ships.
                if !self.warnedMissingModel {
                    self.warnedMissingModel = true
                    print("[CoreMLVisionDetectionService] No Core ML model bundled — "
                        + "returning no detections. Add ObjectDetector.mlmodel(c) to the bundle.")
                }
                DispatchQueue.main.async { completion(.success([])) }
                return
            }

            // ── Inference (dedicated serial background queue) ──
            let handler = VNImageRequestHandler(cvPixelBuffer: input.pixelBuffer,
                                                orientation: input.orientation,
                                                options: [:])
            do {
                try handler.perform([request])
                let observations = (request.results as? [VNRecognizedObjectObservation]) ?? []
                var out: [Detection2D] = []
                for obs in observations {
                    guard let top = obs.labels.first, top.confidence >= minimumConfidence
                    else { continue }
                    out.append(Detection2D(
                        label: top.identifier,
                        confidence: top.confidence,
                        // Vision box (bottom-left, upright space) → sensor space.
                        box: SensorSpace.rect(fromVisionBox: obs.boundingBox,
                                              orientation: input.orientation),
                        source: sourceKind,
                        frameTimestamp: input.timestamp))
                }
                let topN = Array(out.sorted { $0.confidence > $1.confidence }
                    .prefix(maxDetections))
                // ── Hop back to main (the fix) ──
                DispatchQueue.main.async { completion(.success(topN)) }
            } catch {
                DispatchQueue.main.async { completion(.failure(error)) }
            }
        }
    }

    // MARK: - Model

    /// Looks for a compiled object-detection model named "ObjectDetector" in the app
    /// bundle (a Create ML object detector or a YOLO Core ML export). The result
    /// mapping above already handles VNRecognizedObjectObservation.
    private static func makeRequest() -> VNCoreMLRequest? {
        guard let url = Bundle.main.url(forResource: "ObjectDetector", withExtension: "mlmodelc")
                ?? Bundle.main.url(forResource: "ObjectDetector", withExtension: "mlmodel"),
              let model = try? MLModel(contentsOf: url),
              let vnModel = try? VNCoreMLModel(for: model) else {
            return nil
        }
        let req = VNCoreMLRequest(model: vnModel)
        req.imageCropAndScaleOption = .scaleFill
        return req
    }
}
