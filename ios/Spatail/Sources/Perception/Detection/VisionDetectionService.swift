// VisionDetectionService.swift — REAL on-device detection with NO model file required.
//
// Uses Apple Vision's BUILT-IN requests so the pipeline finds real objects out of the box:
//   • VNRecognizeAnimalsRequest      → real labelled boxes (cat / dog)
//   • VNDetectHumanRectanglesRequest → real person boxes
//   • VNGenerateObjectnessBasedSaliencyImageRequest → up to ~3 "this is an object" boxes
//   • VNGenerateAttentionBasedSaliencyImageRequest → "the thing the user is looking at"
//   • VNClassifyImageRequest with a PER-BOX regionOfInterest → each salient box gets
//     its OWN class name (a bottle crop classifies as "water bottle", not whatever
//     dominates the whole frame)
//
// Point the phone at a mug, a bottle, a chair, a pet, a person — you get labelled boxes
// with real confidences and normalized locations, all without shipping a Core ML model.
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
    var maxDetections = 8

    /// A per-box classification must clear this to name the box (else "Object").
    private static let roiClassifyMinConfidence: Float = 0.15
    /// Salient boxes smaller than this (normalized) are noise, not objects.
    private static let minCandidateSide: CGFloat = 0.03
    /// Whole-frame or near-whole-frame saliency boxes are "the scene", not an object.
    private static let maxCandidateArea: CGFloat = 0.85

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
            let objectness = VNGenerateObjectnessBasedSaliencyImageRequest()
            let attention = VNGenerateAttentionBasedSaliencyImageRequest()

            do {
                try handler.perform([animals, humans, objectness, attention])
            } catch {
                DispatchQueue.main.async { completion(.failure(error)) }
                return
            }

            func make(label: String, confidence: Float, visionBox: CGRect,
                      kind: DetectionLabelKind? = nil) -> Detection2D {
                Detection2D(label: label,
                            confidence: confidence,
                            box: SensorSpace.rect(fromVisionBox: visionBox,
                                                  orientation: input.orientation),
                            source: sourceKind,
                            frameTimestamp: input.timestamp,
                            labelKind: kind)
            }

            var detections: [Detection2D] = []
            var semanticVisionBoxes: [CGRect] = []

            // ── Real semantic boxes: animals ──
            for obs in animals.results ?? [] {
                guard let top = obs.labels.first, top.confidence >= minimumConfidence
                else { continue }
                detections.append(make(label: Self.prettify(top.identifier),
                                       confidence: top.confidence,
                                       visionBox: obs.boundingBox))
                semanticVisionBoxes.append(obs.boundingBox)
            }

            // ── Real semantic boxes: humans ──
            for obs in humans.results ?? [] {
                guard obs.confidence >= minimumConfidence else { continue }
                detections.append(make(label: "Person",
                                       confidence: obs.confidence,
                                       visionBox: obs.boundingBox))
                semanticVisionBoxes.append(obs.boundingBox)
            }

            // ── Candidate object boxes: objectness (multi-object) + attention ──
            // "What things are in the frame?" + "What is the user looking at?"
            // (Master File p.26). Dedup among themselves and against semantic boxes.
            var candidates: [(box: CGRect, saliency: Float)] = []
            func addCandidate(_ obs: VNRectangleObservation) {
                let box = obs.boundingBox.intersection(CGRect(x: 0, y: 0, width: 1, height: 1))
                guard box.width >= Self.minCandidateSide,
                      box.height >= Self.minCandidateSide,
                      box.width * box.height <= Self.maxCandidateArea else { return }
                guard !candidates.contains(where: { detectionIoU($0.box, box) > 0.5 }),
                      !semanticVisionBoxes.contains(where: { detectionIoU($0, box) > 0.5 })
                else { return }
                candidates.append((box, obs.confidence))
            }
            for obs in (objectness.results?.first)?.salientObjects ?? [] { addCandidate(obs) }
            for obs in (attention.results?.first)?.salientObjects ?? [] { addCandidate(obs) }

            // ── Name each candidate with a PER-BOX classification ──
            // regionOfInterest is in the same upright, bottom-left-origin space the
            // observations report — the boxes pass straight through.
            var classifiers: [(box: CGRect, saliency: Float, request: VNClassifyImageRequest)] = []
            for candidate in candidates {
                let request = VNClassifyImageRequest()
                request.regionOfInterest = candidate.box
                classifiers.append((candidate.box, candidate.saliency, request))
            }
            if !classifiers.isEmpty {
                try? handler.perform(classifiers.map(\.request))
            }
            for entry in classifiers {
                if let top = Self.topClassification(entry.request.results,
                                                    minConfidence: Self.roiClassifyMinConfidence) {
                    // Labeled box: the LABEL's confidence is the honest currency
                    // (it feeds identity debounce downstream), floored for emission.
                    // v3 §5: only display-allowlisted taxonomy words at real
                    // confidence are SEMANTIC ("couch" 0.95); the rest are HINTS
                    // ("machine", "textile") — they condition, never display.
                    detections.append(make(label: top.label,
                                           confidence: max(minimumConfidence, top.confidence),
                                           visionBox: entry.box,
                                           kind: Self.labelKind(identifier: top.identifier,
                                                                confidence: top.confidence)))
                } else {
                    // Unnamed but real geometry — still worth measuring and fitting.
                    detections.append(make(label: "Object",
                                           confidence: minimumConfidence,
                                           visionBox: entry.box,
                                           kind: .hint))
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

    /// Taxonomy identifiers that describe the scene/image, not a thing — never
    /// worth attaching to an object box.
    private static let abstractIdentifiers: Set<String> = [
        "indoor", "outdoor", "structure", "material", "pattern", "texture",
        "abstract", "blur", "dark", "light", "art", "design", "document",
        "machine_readable_media", "screenshot",
    ]

    private static func topClassification(_ results: [VNClassificationObservation]?,
                                          minConfidence: Float)
        -> (label: String, identifier: String, confidence: Float)? {
        guard let best = results?
            .filter({ $0.confidence >= minConfidence
                       && !abstractIdentifiers.contains($0.identifier.lowercased()) })
            .max(by: { $0.confidence < $1.confidence }) else { return nil }
        return (prettify(best.identifier), best.identifier, best.confidence)
    }

    // MARK: - Label worth (Perception v3 §5)

    /// A taxonomy label must clear this to count as SEMANTIC even when
    /// allowlisted — a 0.2 "couch" is a hunch, not an identity.
    private static let semanticDisplayConfidence: Float = 0.4

    /// Concrete thing-nouns the taxonomy names well enough to DISPLAY. Anything
    /// else the classifier says ("machine", "textile", "bedding",
    /// "wood_processed"…) is a HINT: it conditions priors/gates/templates and
    /// never surfaces as identity. Matched token-wise against the identifier.
    private static let displayableIdentifierTokens: Set<String> = [
        // furniture / room
        "couch", "sofa", "loveseat", "armchair", "chair", "stool", "bench",
        "table", "desk", "bed", "mattress", "dresser", "cabinet", "shelf",
        "bookshelf", "wardrobe", "ottoman", "rug", "lamp", "mirror", "clock",
        "painting", "curtain", "radiator", "fireplace",
        // electronics
        "keyboard", "laptop", "monitor", "television", "tv", "phone",
        "smartphone", "telephone", "tablet", "remote", "mouse", "headphones",
        "headphone", "earphone", "speaker", "printer", "camera", "console",
        "controller", "microphone", "router", "drone",
        // kitchen / bath
        "bottle", "cup", "mug", "glass", "jar", "vase", "candle", "bowl",
        "plate", "pan", "pot", "kettle", "toaster", "microwave", "oven",
        "refrigerator", "fridge", "sink", "toilet", "bathtub", "towel",
        // things
        "book", "plant", "flower", "pillow", "cushion", "blanket", "shoe",
        "sneaker", "boot", "backpack", "bag", "handbag", "suitcase", "guitar",
        "piano", "drum", "ball", "bicycle", "skateboard", "helmet", "toy",
        "doll", "watch", "umbrella", "fan", "heater",
    ]

    /// HINT unless the identifier names a displayable thing at real confidence.
    private static func labelKind(identifier: String,
                                  confidence: Float) -> DetectionLabelKind {
        guard confidence >= semanticDisplayConfidence else { return .hint }
        let tokens = identifier.lowercased()
            .split(whereSeparator: { !$0.isLetter && $0 != "_" })
            .flatMap { $0.split(separator: "_") }
            .map(String.init)
        return tokens.contains(where: displayableIdentifierTokens.contains)
            ? .semantic : .hint
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
