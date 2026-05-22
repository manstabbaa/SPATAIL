// SpatialContract.swift
//
// Codable mirror of the SpatialExperienceContract emitted by
// /pipeline/spatail/experience_contract.js. The shapes here intentionally
// match the JSON field-for-field. When the backend schema evolves, the
// changes land here first.
//
// `SourceContent` is the tricky one: the JSON's `sourceContent` is
// polymorphic across content types. Modeled as a struct of optionals so
// every variant decodes cleanly without a custom init.

import Foundation
import simd

// ---------------------------------------------------------------------------
// Top-level contract
// ---------------------------------------------------------------------------

struct SpatialExperienceContract: Codable, Identifiable {
    let schemaVersion: String
    let createdAt: String?

    let experienceId: String
    let title: String
    let sourcePrompt: String
    let sourceInputs: [SourceInputRef]
    let sourceFiles: [String]
    let detectedDomain: DetectedDomain
    let environmentAssumptions: EnvironmentAssumptions

    let spatialElements: [SpatialElement]
    let relationships: [SpatialRelationship]
    let interactionPlan: InteractionPlan
    let attentionPlan: [AttentionStep]
    let assetRequirements: [AssetRequirement]

    let reasoningSummary: String

    let vocabularies: Vocabularies?

    var id: String { experienceId }
}

struct SourceInputRef: Codable, Hashable {
    let kind: String
    let key: String?
    let title: String?
}

struct DetectedDomain: Codable, Hashable {
    let name: String
    let confidence: String
    let source: String?
}

struct EnvironmentAssumptions: Codable, Hashable {
    let kind: String?
    let surfaces: [String]?
    let anchorObject: String?
    let roomDimensionsMeters: RoomDimensions?
    let source: String?
}

struct RoomDimensions: Codable, Hashable {
    let widthX: Double?
    let depthZ: Double?
    let wallY: Double?
    let tableHeight: Double?
}

struct Vocabularies: Codable, Hashable {
    let contentTypes: [String]?
    let representationModes: [String]?
    let placements: [String]?
    let scaleModes: [String]?
    let anchorStrategies: [String]?
    let attentionBehaviors: [String]?
}

// ---------------------------------------------------------------------------
// Spatial element
// ---------------------------------------------------------------------------

struct SpatialElement: Codable, Identifiable, Hashable {
    let id: String
    let title: String
    // Raw strings, parsed lazily into typed enums so unknown values
    // don't break decoding — the renderer falls back gracefully.
    let contentType: String
    let representationMode: String
    let placement: SpatialPlacement
    let anchorStrategy: String?
    let scaleMode: String?
    let priority: Int

    let sourceContent: SourceContent?
    let requiredAssets: [AssetRequirement]
    let fallbackGeometry: String?
    let interactions: [ElementInteraction]
    let attentionBehavior: String?

    let whyThisRepresentation: String
    let whyThisPlacement: String

    // Typed accessors with safe fallbacks.
    var representationModeEnum: RepresentationMode {
        RepresentationMode(rawValue: representationMode) ?? .two_d_panel
    }
    var contentTypeEnum: ContentTypeTag? {
        ContentTypeTag(rawValue: contentType)
    }
    var placementKindEnum: Placement? {
        guard let s = placement.kind else { return nil }
        return Placement(rawValue: s)
    }
    var anchorStrategyEnum: AnchorStrategy? {
        guard let s = anchorStrategy else { return nil }
        return AnchorStrategy(rawValue: s)
    }
    var scaleModeEnum: ScaleMode? {
        guard let s = scaleMode else { return nil }
        return ScaleMode(rawValue: s)
    }
    var attentionBehaviorEnum: AttentionBehavior? {
        guard let s = attentionBehavior else { return nil }
        return AttentionBehavior(rawValue: s)
    }
}

struct SpatialPlacement: Codable, Hashable {
    let kind: String?
    let anchor: String?
    let position: [Double]?
    let rotationDeg: [Double]?
    let sizeMeters: [Double]?
    let orientation: String?
    let layout: String?
    let offsetMeters: [Double]?
    let from: [Double]?
    let to: [Double]?

    // Convenience: position as SIMD3 with sensible defaults.
    var simdPosition: SIMD3<Float> {
        let p = position ?? []
        let x = p.count > 0 ? Float(p[0]) : 0
        let y = p.count > 1 ? Float(p[1]) : 0
        let z = p.count > 2 ? Float(p[2]) : 0
        return SIMD3(x, y, z)
    }
    var simdRotation: SIMD3<Float> {
        let r = rotationDeg ?? []
        let rx = r.count > 0 ? Float(r[0]) : 0
        let ry = r.count > 1 ? Float(r[1]) : 0
        let rz = r.count > 2 ? Float(r[2]) : 0
        return SIMD3(rx * .pi / 180, ry * .pi / 180, rz * .pi / 180)
    }
    var planeSizeMeters: (width: Float, height: Float) {
        let s = sizeMeters ?? []
        let w = s.count > 0 ? Float(s[0]) : 1.0
        let h = s.count > 1 ? Float(s[1]) : 0.8
        return (w, h)
    }
    var boxSizeMeters: (width: Float, height: Float, depth: Float) {
        let s = sizeMeters ?? []
        let w = s.count > 0 ? Float(s[0]) : 0.6
        let h = s.count > 1 ? Float(s[1]) : 0.3
        let d = s.count > 2 ? Float(s[2]) : 0.4
        return (w, h, d)
    }
}

struct ElementInteraction: Codable, Hashable, Identifiable {
    let id: String
    let type: String
    let behavior: String
}

// ---------------------------------------------------------------------------
// SourceContent — polymorphic per content type. Every field is optional;
// the renderer reads the ones it needs for the active representationMode.
// ---------------------------------------------------------------------------

struct SourceContent: Codable, Hashable {
    var title: String?
    var body: String?
    var finding: String?
    var items: [String]?
    var steps: [String]?
    var events: [TimelineEvent]?
    var options: [DecisionOption]?
    var kpis: [KPIEntry]?
    var facts: [FactEntry]?
    var components: [ComponentEntry]?
    var name: String?
    var role: String?
    var assetGroupRef: String?
    var targetRef: String?
    var placementHint: String?
}

struct KPIEntry: Codable, Hashable, Identifiable {
    var label: String
    var value: String
    var delta: String?
    var trend: String?
    var id: String { label }
}

struct FactEntry: Codable, Hashable, Identifiable {
    var key: String
    var value: String
    var id: String { key }
}

struct TimelineEvent: Codable, Hashable, Identifiable {
    var when: String?
    var label: String
    var detail: String?
    var id: String { "\(when ?? "")-\(label)" }
}

struct DecisionOption: Codable, Hashable, Identifiable {
    var id: String?
    var label: String
    var detail: String?

    enum CodingKeys: String, CodingKey { case id, label, detail }

    // Identifiable conformance with a stable fallback.
    var identity: String { id ?? label }
}

struct ComponentEntry: Codable, Hashable, Identifiable {
    var id: String?
    var name: String
    var identity: String { id ?? name }
}

// ---------------------------------------------------------------------------
// Relationships, interactions, attention plan, asset requirements
// ---------------------------------------------------------------------------

struct SpatialRelationship: Codable, Hashable {
    let from: String
    let to: String
    let type: String
    let note: String?
}

struct InteractionPlan: Codable, Hashable {
    let interactions: [GlobalInteraction]
}

struct GlobalInteraction: Codable, Hashable, Identifiable {
    let id: String
    let elementId: String?
    let type: String
    let behavior: String?
    let trigger: String?
}

struct AttentionStep: Codable, Hashable, Identifiable {
    let step: Int
    let focusElementId: String
    let narration: String
    var id: Int { step }
}

struct AssetRequirement: Codable, Hashable, Identifiable {
    let id: String
    let preferredSource: String?
    let hint: String?
    let fallback: String?
    let unused: Bool?
    let note: String?
    let resolvedAssetGroup: ResolvedAssetGroup?
}

struct ResolvedAssetGroup: Codable, Hashable {
    let groupKey: String
    let items: [ResolvedAssetItem]?
}

struct ResolvedAssetItem: Codable, Hashable {
    let fileName: String?
    let relativePath: String?
    let extension_: String?
    let role: String?

    enum CodingKeys: String, CodingKey {
        case fileName, relativePath
        case extension_ = "extension"
        case role
    }
}
