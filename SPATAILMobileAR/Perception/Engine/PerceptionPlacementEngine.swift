// PerceptionPlacementEngine.swift
//
// ════════════════════════════════════════════════════════════════════════
//  "SPATAIL decides what should appear there."
// ════════════════════════════════════════════════════════════════════════
//
// Input : a user intent string + a resolved SpatailPerceptionFrame +
//         the available assets / visual styles.
// Output: a SpatailPlacementPlan — for every thing it places, WHY (intent,
//         target, anchor, scale, interaction).
//
// This is the brain. It is pure (Foundation + simd) so it can be unit-
// tested off-device and reused by the visionOS / Android-XR players the
// Master File anticipates. It imports NOTHING from ARKit or RealityKit.
//
// It encodes the Master File "SPACE" doctrine directly:
//   • p.26  Placement is spatial REFERENCE, not coordinates — bind to the
//           detected target, not a default position.
//   • p.27  Placement follows the JOB — a table panel sits beside the
//           object on the table, not floating in the center.
//   • p.28  Recognition + reconstruction — highlight lands ON the thing.
//   • p.29  Locus of control — object-local vs world-local placement.
//   • p.42  Behavior contract — intent, anchor, scale, position,
//           interaction, comfort.

import Foundation
import simd

// ---------------------------------------------------------------------------
// Brand tints the engine can stamp onto placements (Master File "IDENTITY").
// Mirrors AppRoot's Color.spatailAccent / spatailAccent2.
// ---------------------------------------------------------------------------

enum SpatailBrand {
    static let accentHex  = "#6EA8FF"   // primary blue   (0.43, 0.66, 1.00)
    static let accent2Hex = "#B56BFF"   // secondary purple(0.71, 0.42, 1.00)
}

// ---------------------------------------------------------------------------
// What the engine has to work with: the asset / style catalog.
// ---------------------------------------------------------------------------

/// The "available assets / visual styles" input. For v1 this is a
/// placeholder; later it is populated from the resolved asset pipeline.
struct SpatailAssetCatalog {
    /// Real models the runtime could load, keyed by a semantic name.
    /// TODO(asset): populate from the bundle / asset pipeline; when an
    /// entry matches a detection label, the engine emits a `.model`
    /// overlay with this `assetRef` instead of a placeholder.
    var modelRefsByLabel: [String: String] = [:]
    /// Render style tag (Master File "render style guides"). 3D-pastel for
    /// explanatory scenes, holographic for highlights.
    var styleTag: String = "holographic"

    static let placeholder = SpatailAssetCatalog()

    /// Best asset ref for a detection label: exact key first, then a
    /// substring match either direction ("coffee mug" ↔ "mug").
    func ref(forLabel label: String) -> String? {
        let key = label.lowercased()
        if let exact = modelRefsByLabel[key] { return exact }
        for (k, v) in modelRefsByLabel where key.contains(k) || k.contains(key) { return v }
        return nil
    }
}

// ---------------------------------------------------------------------------
// Intent classification (Master File p.41).
// ---------------------------------------------------------------------------

/// The verbs SPATAIL recognizes in a prompt. Keyword-based for v1.
struct PerceptionIntent {
    enum Verb: String { case highlight, explain, label, place, compare, simulate, guide, inspect }

    var verbs: Set<Verb>
    var raw: String

    var wantsHighlight: Bool { verbs.contains(.highlight) || verbs.contains(.inspect) }
    var wantsExplanation: Bool { verbs.contains(.explain) }
    var wantsLabel: Bool { verbs.contains(.label) || verbs.contains(.explain) || verbs.isEmpty }
    /// "next to / beside / compare" → place the panel off to the side.
    var sideBySide: Bool

    /// Default behavior when the prompt says nothing specific: identify the
    /// thing (marker + label).
    var isBare: Bool { verbs.isEmpty }
}

enum IntentClassifier {
    // TODO(LLM): replace this keyword matcher with the on-device / server
    // intent model. The contract (PerceptionIntent) does not change.
    static func classify(_ text: String) -> PerceptionIntent {
        let t = text.lowercased()
        var verbs: Set<PerceptionIntent.Verb> = []
        func hit(_ words: [String]) -> Bool { words.contains { t.contains($0) } }

        if hit(["highlight", "outline", "show me the", "point out"]) { verbs.insert(.highlight) }
        if hit(["explain", "explanation", "describe", "what is", "tell me"]) { verbs.insert(.explain) }
        if hit(["label", "name", "tag"]) { verbs.insert(.label) }
        if hit(["place", "put", "add"]) { verbs.insert(.place) }
        if hit(["compare", "versus", " vs "]) { verbs.insert(.compare) }
        if hit(["simulate", "animate", "how it works", "physics"]) { verbs.insert(.simulate) }
        if hit(["guide", "step", "how do i", "how to"]) { verbs.insert(.guide) }
        if hit(["inspect", "look at", "examine"]) { verbs.insert(.inspect) }

        let sideBySide = hit(["next to", "beside", "side by side", "compare", "alongside"])
        return PerceptionIntent(verbs: verbs, raw: text, sideBySide: sideBySide)
    }
}

// ---------------------------------------------------------------------------
// The engine.
// ---------------------------------------------------------------------------

struct PerceptionPlacementEngine {

    private let narrator = PerceptionNarrator()

    func makePlan(intent rawIntent: String,
                  frame: SpatailPerceptionFrame,
                  catalog: SpatailAssetCatalog = .placeholder) -> SpatailPlacementPlan {

        let intent = IntentClassifier.classify(rawIntent)
        let resolved = frame.resolvedDetections

        guard !resolved.isEmpty else {
            return .empty(intent: rawIntent, frameId: frame.frameId, timestamp: frame.timestamp)
        }

        var placements: [SpatailPlacement] = []
        for det in resolved {
            placements.append(contentsOf: decide(for: det, intent: intent, catalog: catalog))
        }

        let summary = reasoning(intent: intent, resolved: resolved, placements: placements)
        return SpatailPlacementPlan(
            planId: UUID().uuidString,
            sourceFrameId: frame.frameId,
            timestamp: frame.timestamp,
            intent: rawIntent,
            placements: placements,
            reasoningSummary: summary,
        )
    }

    // MARK: - Per-detection decision

    /// Turn one resolved detection into 0..n placements per the intent.
    private func decide(for det: SpatailDetection,
                        intent: PerceptionIntent,
                        catalog: SpatailAssetCatalog) -> [SpatailPlacement] {
        guard let hit = det.worldHit else { return [] }
        let target = SpatailPlacementTarget(
            detectionId: det.id,
            label: det.label,
            anchorType: hit.anchorType,
            worldPosition: hit.worldPosition,
            surfaceNormal: hit.surfaceNormal,
            confidence: hit.confidence,
        )

        var out: [SpatailPlacement] = []

        // ── 1. HIGHLIGHT — recognition + reconstruction (p.28): land the
        //       holographic volume directly ON the detected target. Shown
        //       for an explicit highlight verb OR any explain intent
        //       (wantsExplanation already implies a non-bare prompt).
        if intent.wantsHighlight || intent.wantsExplanation {
            out.append(highlight(target: target, det: det, intent: intent))
        }

        // ── 2. A real / placeholder MODEL if the prompt asks to place one
        //       and we have (or can stand in for) an asset.
        if intent.verbs.contains(.place) {
            out.append(model(target: target, det: det, intent: intent, catalog: catalog))
        }

        // ── 3. LABEL — a floating name above the target (object-local UI,
        //       p.29). Shown for explain / label / bare-identify intents.
        if intent.wantsLabel {
            out.append(label(target: target, det: det, intent: intent))
        }

        // ── 4. EXPLANATION PANEL — placed NEXT TO the target, following the
        //       job (p.27): on the same surface for tables/floors/walls,
        //       floating to the side otherwise.
        if intent.wantsExplanation {
            out.append(panel(target: target, det: det, intent: intent))
        }

        // ── 5. If the prompt was bare and produced nothing yet, drop a
        //       marker so the user at least sees the resolved point.
        if out.isEmpty {
            out.append(marker(target: target, det: det, intent: intent))
        }

        return out
    }

    // MARK: - Placement builders (each justifies itself)

    private func highlight(target: SpatailPlacementTarget,
                           det: SpatailDetection,
                           intent: PerceptionIntent) -> SpatailPlacement {
        let size = estimatedPhysicalSize(det: det)
        return SpatailPlacement(
            id: "\(target.detectionId)#highlight",
            target: target,
            asset: SpatailAssetInstruction(
                overlayKind: .highlight,
                title: nil, bodyLines: nil, assetRef: nil,
                tintHex: SpatailBrand.accentHex,
                styleTag: "holographic",
            ),
            placement: SpatailPlacementInstruction(
                scaleMode: .fixedMeters,
                sizeMeters: size,
                offsetMeters: .zero,                 // ON the target
                alignment: .surfaceAligned,
                anchorType: target.anchorType,
            ),
            interaction: SpatailInteractionInstruction(
                behaviors: [.highlightPulse, .tapToSelect],
                isPersistent: true,
                minDistanceMeters: 0.15, maxDistanceMeters: 6.0,
            ),
            reason: SpatailPlacementReason(
                intent: intent.raw,
                detectedLabel: det.label,
                anchorRationale: "Highlight bound directly to the detected \(det.label) on the \(target.anchorType.displayName.lowercased()) — recognition + reconstruction, not a floating preview.",
                scaleRationale: "Sized to the detection's apparent footprint so the glow wraps the real thing.",
                interactionRationale: "Gentle pulse draws attention; tap to focus.",
            ),
        )
    }

    private func label(target: SpatailPlacementTarget,
                       det: SpatailDetection,
                       intent: PerceptionIntent) -> SpatailPlacement {
        let lift = labelLift(for: target.anchorType, det: det)
        return SpatailPlacement(
            id: "\(target.detectionId)#label",
            target: target,
            asset: SpatailAssetInstruction(
                overlayKind: .label,
                title: det.label,
                bodyLines: ["\(Int(det.confidence * 100))% confidence"],
                assetRef: nil,
                tintHex: SpatailBrand.accentHex,
                styleTag: "pastel",
            ),
            placement: SpatailPlacementInstruction(
                scaleMode: .compactPanel,
                sizeMeters: SIMD3(0.30, 0.10, 0.01),
                offsetMeters: SIMD3(0, lift, 0),     // floats above the target
                alignment: .billboard,               // always readable
                anchorType: target.anchorType,
            ),
            interaction: SpatailInteractionInstruction(
                behaviors: [.dwellReveal], isPersistent: true,
                minDistanceMeters: 0.2, maxDistanceMeters: 5.0,
            ),
            reason: SpatailPlacementReason(
                intent: intent.raw,
                detectedLabel: det.label,
                anchorRationale: "Name floats above the object (object-local UI) so it reads as belonging to the thing.",
                scaleRationale: "Compact panel scale — legible at arm's length without dominating.",
                interactionRationale: "Billboards to the user; reveals more on dwell.",
            ),
        )
    }

    private func panel(target: SpatailPlacementTarget,
                       det: SpatailDetection,
                       intent: PerceptionIntent) -> SpatailPlacement {
        let offset = sidePanelOffset(for: target.anchorType, det: det)
        return SpatailPlacement(
            id: "\(target.detectionId)#panel",
            target: target,
            asset: SpatailAssetInstruction(
                overlayKind: .panel,
                title: det.label,
                bodyLines: explanationBody(for: det),
                assetRef: nil,
                tintHex: SpatailBrand.accentHex,
                styleTag: "pastel",
            ),
            placement: SpatailPlacementInstruction(
                scaleMode: .compactPanel,
                sizeMeters: SIMD3(0.42, 0.30, 0.01),
                offsetMeters: offset,                // NEXT TO the target
                alignment: .billboard,
                anchorType: target.anchorType,
            ),
            interaction: SpatailInteractionInstruction(
                behaviors: [.tapToExpand], isPersistent: true,
                minDistanceMeters: 0.25, maxDistanceMeters: 5.0,
            ),
            reason: SpatailPlacementReason(
                intent: intent.raw,
                detectedLabel: det.label,
                anchorRationale: "Explanation placed beside the \(det.label) on its \(target.anchorType.displayName.lowercased()) — content follows the job (Master File p.27), not the screen center.",
                scaleRationale: "Reading-sized panel at the surface so eyes travel object → text naturally.",
                interactionRationale: "Tap to expand into full detail.",
            ),
        )
    }

    private func model(target: SpatailPlacementTarget,
                       det: SpatailDetection,
                       intent: PerceptionIntent,
                       catalog: SpatailAssetCatalog) -> SpatailPlacement {
        let ref = catalog.ref(forLabel: det.label)
        let offset = sidePanelOffset(for: target.anchorType, det: det)
        return SpatailPlacement(
            id: "\(target.detectionId)#model",
            target: target,
            asset: SpatailAssetInstruction(
                overlayKind: .model,
                title: det.label,
                bodyLines: nil,
                assetRef: ref,                       // nil → placeholder box
                tintHex: SpatailBrand.accent2Hex,
                styleTag: catalog.styleTag,
            ),
            placement: SpatailPlacementInstruction(
                scaleMode: .tabletop,
                sizeMeters: SIMD3(0.2, 0.2, 0.2),
                offsetMeters: offset,
                alignment: .uprightFacingUser,
                anchorType: target.anchorType,
            ),
            interaction: SpatailInteractionInstruction(
                behaviors: [.tapToExplode, .tapToSelect], isPersistent: true,
                minDistanceMeters: 0.3, maxDistanceMeters: 5.0,
            ),
            reason: SpatailPlacementReason(
                intent: intent.raw,
                detectedLabel: det.label,
                anchorRationale: ref == nil
                    ? "No matching asset yet — placeholder stands in beside the target."
                    : "Model '\(ref!)' placed beside the detected \(det.label).",
                scaleRationale: "Tabletop scale so it sits on the surface next to the real object.",
                interactionRationale: "Tap to explode/inspect parts.",
            ),
        )
    }

    private func marker(target: SpatailPlacementTarget,
                        det: SpatailDetection,
                        intent: PerceptionIntent) -> SpatailPlacement {
        SpatailPlacement(
            id: "\(target.detectionId)#marker",
            target: target,
            asset: SpatailAssetInstruction(
                overlayKind: .marker, title: det.label, bodyLines: nil,
                assetRef: nil, tintHex: SpatailBrand.accentHex, styleTag: "holographic",
            ),
            placement: SpatailPlacementInstruction(
                scaleMode: .fixedMeters,
                sizeMeters: SIMD3(0.05, 0.05, 0.05),
                offsetMeters: .zero,
                alignment: .billboard,
                anchorType: target.anchorType,
            ),
            interaction: SpatailInteractionInstruction(
                behaviors: [.tapToSelect], isPersistent: true,
                minDistanceMeters: 0.15, maxDistanceMeters: 6.0,
            ),
            reason: SpatailPlacementReason(
                intent: intent.raw,
                detectedLabel: det.label,
                anchorRationale: "Marker on the resolved hit so the user sees exactly where SPATAIL located the \(det.label).",
                scaleRationale: "Small fixed marker — a reference point, not content.",
                interactionRationale: "Tap to select.",
            ),
        )
    }

    // MARK: - Spatial heuristics (placement follows the job / locus of control)

    /// Rough physical size of the detected thing from its apparent size and
    /// distance. Heuristic (no intrinsics in the pure engine): at distance d,
    /// a box spanning fraction f of the view is ≈ d·f meters wide for a
    /// ~60° FoV. Clamped to a believable range.
    private func estimatedPhysicalSize(det: SpatailDetection) -> SIMD3<Float> {
        let d = det.worldHit?.distanceMeters ?? 1.0
        let fw = Float(det.screenBoundingBox.width)
        let fh = Float(det.screenBoundingBox.height)
        let w = min(0.6, max(0.08, d * fw))
        let h = min(0.6, max(0.08, d * fh))
        let depth = min(w, h) * 0.6
        return SIMD3(w, h, depth)
    }

    /// How far above the target the floating label should sit — clears the
    /// highlight volume.
    private func labelLift(for anchor: SpatailAnchorType, det: SpatailDetection) -> Float {
        let size = estimatedPhysicalSize(det: det)
        switch anchor {
        case .wall:   return 0.0          // walls: label sits centered, no lift
        default:      return size.y + 0.12
        }
    }

    /// Where a side panel / model goes relative to the target, per surface.
    /// Master File p.27 (follow the job) + p.29 (locus of control).
    private func sidePanelOffset(for anchor: SpatailAnchorType, det: SpatailDetection) -> SIMD3<Float> {
        let size = estimatedPhysicalSize(det: det)
        let gap: Float = 0.06
        let toRight = size.x / 2 + 0.21 + gap     // half target + half panel(0.42) + gap
        switch anchor {
        case .table, .seat:
            // Beside on the table, slightly up so it doesn't clip the surface.
            return SIMD3(toRight, 0.16, 0)
        case .floor:
            // Raised toward eye level so the user isn't reading the floor.
            return SIMD3(toRight, 0.9, 0)
        case .wall, .ceiling:
            // On the wall plane, to the right.
            return SIMD3(toRight, 0, 0)
        case .object, .unknown:
            // Floating to the right at the object's height.
            return SIMD3(toRight, 0, 0)
        }
    }

    private func explanationBody(for det: SpatailDetection) -> [String] {
        // Copy comes from the (offline) narrator — the LLM/knowledge seam.
        guard let hit = det.worldHit else { return ["Detected in view; resolving position…"] }
        return narrator.explain(label: det.label, anchorType: hit.anchorType,
                                distanceMeters: hit.distanceMeters, method: hit.method)
    }

    // MARK: - Narrative

    private func reasoning(intent: PerceptionIntent,
                           resolved: [SpatailDetection],
                           placements: [SpatailPlacement]) -> String {
        let verbList = intent.verbs.isEmpty ? "identify" : intent.verbs.map(\.rawValue).joined(separator: " + ")
        let kinds = Set(placements.map { $0.asset.overlayKind.displayName }).sorted().joined(separator: ", ")
        return "Intent ‘\(verbList)’ over \(resolved.count) resolved detection(s) → \(placements.count) placement(s): \(kinds.isEmpty ? "none" : kinds)."
    }
}
