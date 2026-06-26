// PerceptionNarrator.swift
//
// Turns a detected label into short explanatory copy for the panel overlay.
// This is the OFFLINE stand-in for the eventual LLM / knowledge service —
// it keeps the experience meaningful with no network, and exposes the exact
// seam where a smarter narrator plugs in.
//
// Pure Foundation. No ARKit / RealityKit.

import Foundation

struct PerceptionNarrator {

    /// Body lines for an explanation panel about `label`, given what
    /// perception knows (anchor, distance, how it was located).
    ///
    /// TODO(server/LLM): replace the offline category copy below with a call
    /// to the knowledge / LLM service. The signature is the stable seam —
    /// swap the body, keep the callers (PerceptionPlacementEngine).
    func explain(label: String,
                 anchorType: SpatailAnchorType,
                 distanceMeters: Float,
                 method: SpatailWorldHit.ResolveMethod) -> [String] {
        var lines: [String] = []
        if let fact = Self.fact(for: label) {
            lines.append(fact)
        } else {
            lines.append("Identified in view and anchored to the \(anchorType.displayName.lowercased()).")
        }
        lines.append("Located by \(Self.methodLabel(method)) · \(String(format: "%.2f", distanceMeters)) m away.")
        return lines
    }

    /// A short headline for the panel / label.
    func headline(label: String) -> String { label }

    // MARK: - Offline knowledge (small, illustrative — not exhaustive)

    private static func fact(for label: String) -> String? {
        let l = label.lowercased()
        for (keys, copy) in knowledge {
            if keys.contains(where: { l.contains($0) }) { return copy }
        }
        return nil
    }

    /// Keyword buckets → one-line explanation. Intentionally small; the real
    /// copy comes from the LLM/knowledge service (see TODO above).
    private static let knowledge: [([String], String)] = [
        (["person", "human"],            "A person detected in the scene — tracked as a moving subject."),
        (["cat", "dog", "animal"],       "A live animal — its pose and position update as it moves."),
        (["chair", "seat", "sofa", "couch"], "Seating — a place to sit; useful as a real-world reference for scale."),
        (["table", "desk"],              "A flat working surface — a natural anchor for tabletop content."),
        (["cup", "mug", "bottle", "glass"], "A vessel for liquid — a small, graspable everyday object."),
        (["laptop", "computer", "keyboard", "screen", "monitor", "tv"], "A computing device — a screen-based information surface."),
        (["book", "notebook"],           "A bound document — pages of written information."),
        (["plant", "flower", "potted"],  "A plant — a living decorative element."),
        (["phone", "cell"],              "A handheld device — personal screen and camera."),
        (["car", "vehicle", "engine"],   "A vehicle or mechanical system — explodes into inspectable parts."),
    ]

    private static func methodLabel(_ method: SpatailWorldHit.ResolveMethod) -> String {
        switch method {
        case .existingPlaneGeometry: return "plane geometry"
        case .existingPlane:         return "a detected plane"
        case .estimatedPlane:        return "an estimated plane"
        case .sceneDepth:            return "LiDAR depth"
        case .cameraRayFallback:     return "a camera ray"
        }
    }
}
