// FormPriors.swift — Perception v2 Form Engine, stage 4 constants.
//
// The class-prior dimension table for the transparency / unreliable-depth fallback:
// when LiDAR depth collapses under an object (the founder's clear plastic bottle —
// dToF returns garbage through transparencies), the Form Engine emits a `.prior`
// form whose dimensions come from this table, scaled by the RGB silhouette when a
// support-surface range is available. EDIT THESE FREELY — they are deliberately a
// flat constants file, one line per class.
//
// Also home of the revolution-class set for stage 3 conditioning: labels matching
// these classes get the surface-of-revolution fit; everything else takes the OBB
// path. Pure Foundation — runs in the off-device harness.

import Foundation

enum FormPriors {

    /// Canonical upright dimensions of a class instance, metres.
    struct Prior {
        let diameter: Float
        let height: Float
    }

    /// diameter × height (m). Sources: common retail sizes; tune against calipers.
    static let table: [String: Prior] = [
        "bottle": Prior(diameter: 0.065, height: 0.22),
        "can":    Prior(diameter: 0.066, height: 0.12),
        "cup":    Prior(diameter: 0.085, height: 0.10),
        "mug":    Prior(diameter: 0.085, height: 0.10),
        "glass":  Prior(diameter: 0.075, height: 0.14),
        "jar":    Prior(diameter: 0.085, height: 0.14),
        "vase":   Prior(diameter: 0.100, height: 0.25),
        "candle": Prior(diameter: 0.070, height: 0.10),
    ]

    /// Classes fitted as surfaces of revolution about the gravity axis (stage 3a).
    static let revolutionClasses: Set<String> = [
        "bottle", "can", "cup", "mug", "jar", "glass", "vase", "candle",
    ]

    /// Silhouette scaling of a prior is clamped to this range — a wildly off scale
    /// means the silhouette/range measurement is broken, not that the bottle is 2 m.
    static let scaleClamp: ClosedRange<Float> = 0.5...2.0

    /// The revolution class named by a label, by exact word-token match (so
    /// "water bottle" → "bottle" but "sunglasses" does NOT match "glass").
    static func revolutionClass(for label: String?) -> String? {
        for token in tokens(of: label) where revolutionClasses.contains(token) {
            return token
        }
        return nil
    }

    /// The prior for a label (same token matching), if the table knows the class.
    static func prior(for label: String?) -> Prior? {
        for token in tokens(of: label) {
            if let p = table[token] { return p }
        }
        return nil
    }

    private static func tokens(of label: String?) -> [String] {
        guard let label else { return [] }
        return label.lowercased()
            .split(whereSeparator: { !$0.isLetter })
            .map(String.init)
    }
}
