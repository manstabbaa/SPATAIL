// SpatailPlacementContract.swift
//
// THE PLACEMENT PLAN — the typed contract that flows OUT of the SPATAIL
// placement engine and INTO the RealityKit runtime.
//
// SPATAIL principle (Master File, "MATTER → why it appears" +
// "INTERACTION → Behavior Contract", p.42):
//   "Each object receives a behavior contract: intent, anchor, scale,
//    position, interaction, state, and comfort rules."
//
// Every placement here carries its REASON — the engine must justify each
// thing it puts in the world from: user intent, detected target, spatial
// anchor, scale mode, interaction behavior. Nothing appears by default.
//
// Pure value types: Foundation + simd only. No ARKit, no RealityKit.

import Foundation
import simd

// ---------------------------------------------------------------------------
// What kind of thing the engine decided to place
// ---------------------------------------------------------------------------

/// The visual form an overlay takes. Master File "WHAT APPEARS" taxonomy,
/// reduced to the renderable primitives the runtime ships today.
enum SpatailOverlayKind: String, Codable, Hashable {
    case marker          // a small 3D point marker on the hit
    case highlight       // translucent-blue holographic volume/plane on the target
    case label           // a floating text plate
    case arrow           // a directional pointer toward the target
    case panel           // a 2D UI panel (explanation / data)
    case model           // a 3D model (USDZ / placeholder)
    case explodedModel   // a 3D model shown exploded

    var displayName: String {
        switch self {
        case .marker:        return "Marker"
        case .highlight:     return "Highlight"
        case .label:         return "Label"
        case .arrow:         return "Arrow"
        case .panel:         return "Panel"
        case .model:         return "3D Model"
        case .explodedModel: return "Exploded Model"
        }
    }
}

/// How the placed content is scaled. Master File "Behavior Contract → scale".
enum SpatailScaleMode: String, Codable, Hashable {
    case realScale       // 1:1 with the world
    case tabletop        // shrunk to sit on a surface
    case enlargedDetail  // blown up so a small part is legible
    case fixedMeters     // an explicit metric size (see placement.sizeMeters)
    case compactPanel    // panel sized for arm's-length reading

    var displayName: String { rawValue }
}

/// How the content orients itself. Master File "Locus of Control" (p.29)
/// expressed as an orientation rule.
enum SpatailAlignment: String, Codable, Hashable {
    case billboard          // always face the camera (labels, panels)
    case surfaceAligned     // lie on / against the hit surface (highlights)
    case uprightFacingUser  // stand upright but yaw toward the user
    case worldFixed         // keep the world orientation it was placed with

    var displayName: String { rawValue }
}

/// What the user can DO with the placed object. Master File p.35:
/// "Objects Should Carry Interaction Affordances ... an interaction
/// contract: what it can do, what the user can do to it."
enum SpatailInteractionBehavior: String, Codable, Hashable {
    case none
    case tapToExpand     // panels: tap to enlarge / reveal detail
    case tapToExplode    // models: tap to explode/collapse
    case highlightPulse  // highlights: gentle attention pulse
    case tapToSelect     // markers/targets: tap to focus
    case dwellReveal     // reveal more when gazed at / approached

    var displayName: String { rawValue }
}

// ---------------------------------------------------------------------------
// The four sub-instructions named by the spec
// ---------------------------------------------------------------------------

/// WHICH detection this placement attaches to, resolved into the world.
/// Master File p.26: "Placement is the system deciding WHERE the answer
/// becomes meaningful" — anchored to a real, recognized reference.
struct SpatailPlacementTarget: Codable, Hashable {
    /// The `SpatailDetection.id` this placement is bound to (so the runtime
    /// can re-anchor it when the detection moves between frames).
    var detectionId: String
    var label: String
    var anchorType: SpatailAnchorType
    var worldPosition: SIMD3<Float>
    var surfaceNormal: SIMD3<Float>?
    var confidence: Float
}

/// WHAT to instantiate. Carries enough to render a placeholder now and to
/// swap in a real asset later without touching the engine.
struct SpatailAssetInstruction: Codable, Hashable {
    var overlayKind: SpatailOverlayKind
    /// Text payload for label / panel kinds (title + body lines).
    var title: String?
    var bodyLines: [String]?
    /// Reference to a real asset to load when available. nil → placeholder.
    /// TODO(asset): resolve to a bundled / downloaded USDZ via the asset
    /// pipeline; until then the runtime draws a primitive placeholder.
    var assetRef: String?
    /// Brand tint as hex ("#6EA8FF"). nil → runtime brand default.
    var tintHex: String?
    /// Free-form style tag the runtime can branch on (e.g. "holographic",
    /// "pastel"). Master File "IDENTITY → render style guides".
    var styleTag: String?
}

/// HOW to place it: scale, offset, alignment, anchoring. Master File
/// "Behavior Contract → scale, position".
struct SpatailPlacementInstruction: Codable, Hashable {
    var scaleMode: SpatailScaleMode
    /// Explicit size in meters for `fixedMeters` / sizing a panel or
    /// highlight. Interpreted per overlay kind (panel: width×height;
    /// highlight/model: bounding box).
    var sizeMeters: SIMD3<Float>?
    /// Offset from the target's world position, in the target's local
    /// frame (x = right, y = up, z = toward camera). "Place it NEXT TO" =
    /// a nonzero x; "above" = a nonzero y.
    var offsetMeters: SIMD3<Float>
    var alignment: SpatailAlignment
    var anchorType: SpatailAnchorType
}

/// HOW it behaves once placed. Master File "Behavior Contract →
/// interaction, state, comfort rules" + p.43 "Adaptive Runtime".
struct SpatailInteractionInstruction: Codable, Hashable {
    var behaviors: [SpatailInteractionBehavior]
    /// Persistent context stays up; non-persistent can be dismissed/auto-hide.
    var isPersistent: Bool
    /// Comfort: don't render closer than this / fade beyond that (meters).
    var minDistanceMeters: Float?
    var maxDistanceMeters: Float?

    static let inert = SpatailInteractionInstruction(
        behaviors: [.none], isPersistent: true,
        minDistanceMeters: nil, maxDistanceMeters: nil,
    )
}

// ---------------------------------------------------------------------------
// The reason every placement must carry
// ---------------------------------------------------------------------------

/// The justification for a single placement. The SPATAIL rule: every placed
/// object needs a reason — user intent, detected target, spatial anchor,
/// scale mode, interaction behavior. This struct makes that rule a type.
struct SpatailPlacementReason: Codable, Hashable {
    var intent: String            // the user intent that motivated this
    var detectedLabel: String     // what was detected
    var anchorRationale: String   // why this anchor / locus of control
    var scaleRationale: String    // why this scale
    var interactionRationale: String

    /// One-line human summary for the debug HUD / logs.
    var summary: String {
        "\(intent) → \(detectedLabel): \(anchorRationale)"
    }
}

// ---------------------------------------------------------------------------
// One placement = the four instructions + its reason
// ---------------------------------------------------------------------------

/// A single decided placement. Bundles the four spec sub-instructions plus
/// the reason. The runtime turns one of these into one anchored entity.
struct SpatailPlacement: Codable, Hashable, Identifiable {
    var id: String
    var target: SpatailPlacementTarget
    var asset: SpatailAssetInstruction
    var placement: SpatailPlacementInstruction
    var interaction: SpatailInteractionInstruction
    var reason: SpatailPlacementReason

    init(id: String = UUID().uuidString,
         target: SpatailPlacementTarget,
         asset: SpatailAssetInstruction,
         placement: SpatailPlacementInstruction,
         interaction: SpatailInteractionInstruction,
         reason: SpatailPlacementReason) {
        self.id = id
        self.target = target
        self.asset = asset
        self.placement = placement
        self.interaction = interaction
        self.reason = reason
    }
}

// ---------------------------------------------------------------------------
// The plan — everything to render for one perception frame + intent
// ---------------------------------------------------------------------------

/// The complete output of the placement engine for one (intent, frame) pair.
/// The runtime diffs successive plans by placement id to add/update/remove
/// anchors.
struct SpatailPlacementPlan: Codable, Hashable, Identifiable {
    var planId: String
    /// The perception frame this plan was computed from (for traceability).
    var sourceFrameId: Int
    var timestamp: TimeInterval
    /// The raw user intent string that drove the plan.
    var intent: String
    var placements: [SpatailPlacement]
    /// A short narrative of how the engine reasoned, for the HUD / logs.
    var reasoningSummary: String

    var id: String { planId }

    static func empty(intent: String, frameId: Int, timestamp: TimeInterval) -> SpatailPlacementPlan {
        SpatailPlacementPlan(
            planId: UUID().uuidString,
            sourceFrameId: frameId,
            timestamp: timestamp,
            intent: intent,
            placements: [],
            reasoningSummary: "No resolved detections to place against yet.",
        )
    }
}
