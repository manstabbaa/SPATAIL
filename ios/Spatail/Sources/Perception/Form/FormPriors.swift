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

    // MARK: - Furniture scale table (Perception v3 §1)

    /// How a class's form is decomposed (v3 §3). `.seating` → seat/backrest/
    /// armrests; `.slabTop` → top + base; `.slab`/`.box` → one primitive.
    enum AssemblyTemplate: String {
        case seating, slabTop, slab, box
    }

    /// Canonical footprint of a furniture-scale class + its association gate.
    /// The GATE is the heart of the couch fix: two observations of the same
    /// class within `gate` metres (compatible heights) are one physical thing —
    /// bottle-scale defaults fragment anything bigger than ~0.3 m.
    struct FurniturePrior {
        let length: Float      // longest horizontal extent (m)
        let depth: Float
        let height: Float
        /// Same-class center-distance association gate (m).
        let gate: Float
        let template: AssemblyTemplate
    }

    /// Guidance values — tune against the replay fixtures, keep the shape.
    static let furniture: [String: FurniturePrior] = [
        "couch":     .init(length: 2.0, depth: 0.9, height: 0.8, gate: 1.2, template: .seating),
        "sofa":      .init(length: 2.0, depth: 0.9, height: 0.8, gate: 1.2, template: .seating),
        "loveseat":  .init(length: 1.5, depth: 0.9, height: 0.8, gate: 1.0, template: .seating),
        "sectional": .init(length: 2.8, depth: 1.0, height: 0.8, gate: 1.5, template: .seating),
        "armchair":  .init(length: 0.9, depth: 0.9, height: 0.8, gate: 0.6, template: .seating),
        "chair":     .init(length: 0.5, depth: 0.5, height: 0.9, gate: 0.45, template: .seating),
        "stool":     .init(length: 0.4, depth: 0.4, height: 0.6, gate: 0.4, template: .box),
        "bench":     .init(length: 1.2, depth: 0.4, height: 0.5, gate: 0.8, template: .slabTop),
        "bed":       .init(length: 2.0, depth: 1.6, height: 0.6, gate: 1.4, template: .slab),
        "mattress":  .init(length: 2.0, depth: 1.6, height: 0.3, gate: 1.4, template: .slab),
        "table":     .init(length: 1.4, depth: 0.8, height: 0.75, gate: 0.9, template: .slabTop),
        "desk":      .init(length: 1.4, depth: 0.7, height: 0.75, gate: 0.9, template: .slabTop),
        "dresser":   .init(length: 1.0, depth: 0.5, height: 1.0, gate: 0.7, template: .box),
        "cabinet":   .init(length: 1.0, depth: 0.5, height: 1.2, gate: 0.7, template: .box),
        "shelf":     .init(length: 0.9, depth: 0.35, height: 1.6, gate: 0.6, template: .box),
        "bookshelf": .init(length: 0.9, depth: 0.35, height: 1.8, gate: 0.6, template: .box),
        "wardrobe":  .init(length: 1.2, depth: 0.6, height: 2.0, gate: 0.8, template: .box),
        "tv":         .init(length: 1.1, depth: 0.1, height: 0.65, gate: 0.6, template: .box),
        "television": .init(length: 1.1, depth: 0.1, height: 0.65, gate: 0.6, template: .box),
        "monitor":    .init(length: 0.62, depth: 0.1, height: 0.4, gate: 0.4, template: .box),
        "keyboard":   .init(length: 0.36, depth: 0.13, height: 0.035, gate: 0.25, template: .box),
        "laptop":     .init(length: 0.32, depth: 0.22, height: 0.02, gate: 0.25, template: .box),
        "rug":        .init(length: 2.0, depth: 1.5, height: 0.02, gate: 1.2, template: .slab),
        "carpet":     .init(length: 2.0, depth: 1.5, height: 0.02, gate: 1.2, template: .slab),
        "ottoman":    .init(length: 0.7, depth: 0.7, height: 0.45, gate: 0.5, template: .box),
        "refrigerator": .init(length: 0.9, depth: 0.7, height: 1.7, gate: 0.6, template: .box),
        "fridge":       .init(length: 0.9, depth: 0.7, height: 1.7, gate: 0.6, template: .box),
    ]

    /// The furniture prior a label (or class hint) names, by word-token match.
    static func furniturePrior(for label: String?) -> FurniturePrior? {
        for token in tokens(of: label) {
            if let p = furniture[token] { return p }
        }
        return nil
    }

    /// The furniture class TOKEN a label names — the same-class merge currency
    /// ("brown couch" and "couch" both name "couch"; "sofa" ≠ "couch" though,
    /// so equality is by token, after this normalization).
    static func furnitureToken(for label: String?) -> String? {
        for token in tokens(of: label) where furniture[token] != nil {
            return furnitureAlias[token] ?? token
        }
        return nil
    }

    /// Tokens that name the SAME physical class (merge currency equivalence).
    private static let furnitureAlias: [String: String] = [
        "sofa": "couch", "loveseat": "couch", "sectional": "couch",
        "television": "tv", "mattress": "bed", "carpet": "rug",
        "fridge": "refrigerator", "bookshelf": "shelf", "desk": "table",
    ]

    /// Scale tier — picks the voxel tier and the fitting path (v3 §3).
    enum ScaleClass {
        case small, furniture
    }

    static func scaleClass(for label: String?) -> ScaleClass {
        furniturePrior(for: label) != nil ? .furniture : .small
    }

    private static func tokens(of label: String?) -> [String] {
        guard let label else { return [] }
        return label.lowercased()
            .split(whereSeparator: { !$0.isLetter })
            .map(String.init)
    }
}
