// CoreMLVisionDetectionService.swift
//
// The REAL VISION layer — Apple Vision + Core ML object detection over the
// live ARFrame. Wired end-to-end EXCEPT for the model itself: no `.mlmodel`
// ships in the project yet, so `loadModel()` returns nil and `detect`
// answers with an empty result (and a one-time TODO log) instead of
// crashing. Drop a model in and flip the source switch to `coreML`.
//
// It conforms to the SAME `DetectionService` protocol as the mock, so the
// resolver / engine / runtime never change. This is the seam the follow-up
// asks for: "Keep the same DetectionService protocol. Add a
// CoreMLVisionDetectionService. Process ARFrame.capturedImage. Return
// normalized bounding boxes, label names, and confidence."

import Foundation
import Vision
import CoreML
import CoreVideo
import ImageIO
import CoreGraphics

final class CoreMLVisionDetectionService: DetectionService {
    let sourceName = "Core ML"

    /// Only report detections at/above this confidence.
    var minimumConfidence: VNConfidence = 0.4

    /// Lazily-built Vision request. nil when no model is available.
    private lazy var request: VNCoreMLRequest? = makeRequest()
    private var warnedMissingModel = false

    // MARK: - DetectionService

    func detect(_ input: DetectionInput,
                completion: @escaping (Result<[SpatailDetection], Error>) -> Void) {
        guard let request = request else {
            // No model bundled yet — degrade gracefully so the live view
            // still runs (it just won't find anything until a model ships).
            if !warnedMissingModel {
                warnedMissingModel = true
                // TODO(model): add an object-detection .mlmodel (e.g. a YOLO
                // export or a Create ML model) to Resources/, then load it in
                // `makeRequest()`. Until then this path returns no detections.
                print("[CoreMLVisionDetectionService] No Core ML model bundled — returning no detections. See TODO(model).")
            }
            completion(.success([]))
            return
        }

        let handler = VNImageRequestHandler(
            cvPixelBuffer: input.pixelBuffer,
            orientation: input.orientation,
            options: [:],
        )
        do {
            try handler.perform([request])
            let detections = mapResults(request.results, frameId: input.frameId)
            completion(.success(detections))
        } catch {
            completion(.failure(error))
        }
    }

    // MARK: - Model

    private func makeRequest() -> VNCoreMLRequest? {
        // Looks for a compiled object-detection model named "ObjectDetector"
        // in the app bundle. Drop one in (a Create ML object detector or a
        // YOLO Core ML export) and this activates with NO code change — the
        // result mapping below already handles VNRecognizedObjectObservation.
        // TODO(model): the only remaining input is the model file itself.
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

    // MARK: - Result mapping  (Vision space → SPATAIL screen space)

    private func mapResults(_ results: [VNObservation]?, frameId: Int) -> [SpatailDetection] {
        guard let observations = results as? [VNRecognizedObjectObservation] else { return [] }
        var out: [SpatailDetection] = []
        for (i, obs) in observations.enumerated() {
            guard let top = obs.labels.first, top.confidence >= minimumConfidence else { continue }

            // Vision boxes are normalized with a BOTTOM-LEFT origin; SPATAIL
            // uses a TOP-LEFT origin. Flip Y. ← coordinate-system boundary.
            let vb = obs.boundingBox
            let rect = SpatailScreenRect(
                x: Double(vb.origin.x),
                y: Double(1.0 - vb.origin.y - vb.height),
                width: Double(vb.width),
                height: Double(vb.height),
            )

            out.append(SpatailDetection(
                // Stable-ish id per slot so anchors persist across frames.
                id: "coreml-\(i)-\(top.identifier)",
                label: top.identifier,
                confidence: top.confidence,
                screenBoundingBox: rect,
                worldHit: nil,   // ← the spatial resolver fills this in
            ))
        }
        return out
    }
}
