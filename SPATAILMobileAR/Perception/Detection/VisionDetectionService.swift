// VisionDetectionService.swift
//
// REAL on-device detection with NO model file required. Uses Apple Vision's
// BUILT-IN requests so the pipeline finds real objects out of the box:
//
//   • VNRecognizeAnimalsRequest      → real labelled boxes (cat / dog)
//   • VNDetectHumanRectanglesRequest → real person boxes
//   • VNGenerateAttentionBasedSaliencyImageRequest → "the thing the user is
//     looking at" as a box, labelled by …
//   • VNClassifyImageRequest         → a whole-image class name for that box
//
// This is the difference between "stub" and "works today": point the phone
// at a mug, a chair, a pet, a person — you get a labelled box with a real
// confidence and a normalized location, all without shipping a Core ML model.
// For a custom domain model, use CoreMLVisionDetectionService instead.
//
// Conforms to the SAME DetectionService protocol — resolver / engine /
// runtime are unchanged.

import Foundation
import Vision
import CoreVideo
import ImageIO
import CoreGraphics

final class VisionDetectionService: DetectionService {
    let sourceName = "Apple Vision"

    /// Drop detections below this confidence.
    var minimumConfidence: Float = 0.25
    /// Cap how many detections we emit per frame (highest confidence first).
    var maxDetections = 5

    func detect(_ input: DetectionInput,
                completion: @escaping (Result<[SpatailDetection], Error>) -> Void) {
        let handler = VNImageRequestHandler(
            cvPixelBuffer: input.pixelBuffer,
            orientation: input.orientation,
            options: [:],
        )

        let animals = VNRecognizeAnimalsRequest()
        let humans = VNDetectHumanRectanglesRequest()
        let saliency = VNGenerateAttentionBasedSaliencyImageRequest()
        let classify = VNClassifyImageRequest()

        do {
            try handler.perform([animals, humans, saliency, classify])
        } catch {
            completion(.failure(error))
            return
        }

        var detections: [SpatailDetection] = []

        // ── Real semantic boxes: animals ──
        if let animalObs = animals.results {
            for (i, obs) in animalObs.enumerated() {
                guard let top = obs.labels.first, top.confidence >= minimumConfidence else { continue }
                detections.append(make(id: "vision-animal-\(i)",
                                       label: Self.prettify(top.identifier),
                                       confidence: top.confidence,
                                       visionBox: obs.boundingBox))
            }
        }

        // ── Real semantic boxes: humans ──
        if let humanObs = humans.results {
            for (i, obs) in humanObs.enumerated() {
                guard obs.confidence >= minimumConfidence else { continue }
                detections.append(make(id: "vision-human-\(i)",
                                       label: "Person",
                                       confidence: obs.confidence,
                                       visionBox: obs.boundingBox))
            }
        }

        // ── Saliency box labelled by whole-image classification ──
        // "What is the user looking at?" (Master File p.26).
        if let salient = (saliency.results?.first)?.salientObjects?.first {
            let label = Self.topClassification(classify.results, minConfidence: 0.10) ?? "Object"
            let conf = max(minimumConfidence, salient.confidence)
            let candidate = make(id: "vision-salient",
                                 label: label,
                                 confidence: conf,
                                 visionBox: salient.boundingBox)
            // Only add if it isn't already covered by a semantic box.
            if !detections.contains(where: { iou($0.screenBoundingBox, candidate.screenBoundingBox) > 0.5 }) {
                detections.append(candidate)
            }
        }

        let top = detections
            .sorted { $0.confidence > $1.confidence }
            .prefix(maxDetections)
        completion(.success(Array(top)))
    }

    // MARK: - Helpers

    /// Build a SpatailDetection, converting Vision's BOTTOM-LEFT normalized
    /// box to SPATAIL's TOP-LEFT convention. ← coordinate-system boundary.
    private func make(id: String, label: String, confidence: Float, visionBox: CGRect) -> SpatailDetection {
        let rect = SpatailScreenRect(
            x: Double(visionBox.origin.x),
            y: Double(1.0 - visionBox.origin.y - visionBox.height),
            width: Double(visionBox.width),
            height: Double(visionBox.height),
        )
        return SpatailDetection(id: id, label: label, confidence: confidence,
                                screenBoundingBox: rect, worldHit: nil)
    }

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

    /// Intersection-over-union of two normalized top-left rects (for dedup).
    private func iou(_ a: SpatailScreenRect, _ b: SpatailScreenRect) -> Double {
        let ax2 = a.x + a.width, ay2 = a.y + a.height
        let bx2 = b.x + b.width, by2 = b.y + b.height
        let ix = max(0, min(ax2, bx2) - max(a.x, b.x))
        let iy = max(0, min(ay2, by2) - max(a.y, b.y))
        let inter = ix * iy
        let union = a.width * a.height + b.width * b.height - inter
        return union > 0 ? inter / union : 0
    }
}
