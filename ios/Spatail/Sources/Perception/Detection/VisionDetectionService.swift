// VisionDetectionService.swift — REAL on-device detection with NO model file required.
//
// Uses Apple Vision's BUILT-IN requests so the pipeline finds real objects out of the box:
//   • VNRecognizeAnimalsRequest      → real labelled boxes (cat / dog)
//   • VNDetectHumanRectanglesRequest → real person boxes
//   • VNGenerateAttentionBasedSaliencyImageRequest → "the thing the user is looking at"
//     as a box, labelled by …
//   • VNClassifyImageRequest         → a whole-image class name for that box
//
// Point the phone at a mug, a chair, a pet, a person — you get a labelled box with a
// real confidence and a normalized location, all without shipping a Core ML model.
// For a custom domain model use CoreMLVisionDetectionService instead.
//
// Threading (the mandated fix): `handler.perform` runs on the dedicated serial
// inference queue via InferenceExecutor; the latch is set before dispatch; the
// completion is delivered on MAIN. A busy detector drops the frame (returns false).

import Foundation
import Vision
import CoreVideo
import CoreGraphics
import ImageIO

final class VisionDetectionService: DetectionService {
    let sourceName = "Apple Vision"
    let sourceKind: DetectionSourceKind = .appleVision

    /// Drop detections below this confidence.
    var minimumConfidence: Float = 0.25
    /// Cap how many detections we emit per frame (highest confidence first).
    var maxDetections = 5

    private let executor = InferenceExecutor(label: "dev.spatail.perception.vision")

    @discardableResult
    func detect(_ input: DetectionInput,
                completion: @escaping (Result<[Detection2D], Error>) -> Void) -> Bool {
        let minimumConfidence = self.minimumConfidence
        let maxDetections = self.maxDetections
        let sourceKind = self.sourceKind

        return executor.submit {
            // ── Inference (dedicated serial background queue) ──
            let handler = VNImageRequestHandler(cvPixelBuffer: input.pixelBuffer,
                                                orientation: input.orientation,
                                                options: [:])
            let animals = VNRecognizeAnimalsRequest()
            let humans = VNDetectHumanRectanglesRequest()
            let saliency = VNGenerateAttentionBasedSaliencyImageRequest()
            let classify = VNClassifyImageRequest()

            do {
                try handler.perform([animals, humans, saliency, classify])
            } catch {
                DispatchQueue.main.async { completion(.failure(error)) }
                return
            }

            func make(label: String, confidence: Float, visionBox: CGRect) -> Detection2D {
                Detection2D(label: label,
                            confidence: confidence,
                            box: SensorSpace.rect(fromVisionBox: visionBox,
                                                  orientation: input.orientation),
                            source: sourceKind,
                            frameTimestamp: input.timestamp)
            }

            var detections: [Detection2D] = []

            // ── Real semantic boxes: animals ──
            for obs in animals.results ?? [] {
                guard let top = obs.labels.first, top.confidence >= minimumConfidence
                else { continue }
                detections.append(make(label: Self.prettify(top.identifier),
                                       confidence: top.confidence,
                                       visionBox: obs.boundingBox))
            }

            // ── Real semantic boxes: humans ──
            for obs in humans.results ?? [] {
                guard obs.confidence >= minimumConfidence else { continue }
                detections.append(make(label: "Person",
                                       confidence: obs.confidence,
                                       visionBox: obs.boundingBox))
            }

            // ── Saliency box labelled by whole-image classification ──
            // "What is the user looking at?" (Master File p.26).
            if let salient = (saliency.results?.first)?.salientObjects?.first {
                let label = Self.topClassification(classify.results, minConfidence: 0.10)
                    ?? "Object"
                let candidate = make(label: label,
                                     confidence: max(minimumConfidence, salient.confidence),
                                     visionBox: salient.boundingBox)
                // Only add if it isn't already covered by a semantic box.
                if !detections.contains(where: { detectionIoU($0.box, candidate.box) > 0.5 }) {
                    detections.append(candidate)
                }
            }

            let top = Array(detections
                .sorted { $0.confidence > $1.confidence }
                .prefix(maxDetections))

            // ── Hop back to main (the fix) ──
            DispatchQueue.main.async { completion(.success(top)) }
        }
    }

    // MARK: - Helpers

    private static func topClassification(_ results: [VNClassificationObservation]?,
                                          minConfidence: Float) -> String? {
        guard let best = results?
            .filter({ $0.confidence >= minConfidence })
            .max(by: { $0.confidence < $1.confidence }) else { return nil }
        return prettify(best.identifier)
    }

    /// "coffee_mug" / "Egyptian cat, Felis catus" → "Coffee Mug" / "Egyptian Cat".
    private static func prettify(_ identifier: String) -> String {
        let firstClause = identifier.split(separator: ",").first.map(String.init) ?? identifier
        return firstClause
            .replacingOccurrences(of: "_", with: " ")
            .split(separator: " ")
            .map { $0.prefix(1).uppercased() + $0.dropFirst() }
            .joined(separator: " ")
    }
}
