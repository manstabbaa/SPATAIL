// ExperienceContract.swift
// Codable shell for experience.json (v0.5 SpatialExperienceContract).
//
// The JS source of truth is `pipeline/spatail/experience_contract.js`.
// Closed vocab values live in `Vocab.swift` (generated from JS).
//
// v1 deliberately keeps loose typing for fields the renderer doesn't need
// yet — `relationships`, `reasoningSummary`, etc. ride as raw JSON. Tighten
// them as iOS renderers grow.

import Foundation

public struct ExperienceContract: Codable, Sendable {
    public let schemaVersion: String
    public let createdAt: String?
    public let experienceId: String
    public let title: String?
    public let sourcePrompt: String?
    public let detectedDomain: String?

    public let spatialElements: [SpatialElement]
    public let animations: [Animation]?
    public let interactions: [Interaction]?
    public let sequences: [Sequence]?
    public let defaultSequenceId: String?

    public let explanation: Explanation?
    public let mechanics: [Mechanic]?
    public let presentation: Presentation?

    public let roomContract: AnyCodable?

    // MARK: nested types

    public struct SpatialElement: Codable, Sendable, Identifiable {
        public let id: String
        public let title: String?
        public let contentType: ContentType?
        public let representationMode: RepresentationMode
        public let placement: Placement?
        public let anchorStrategy: AnchorStrategy?
        public let scaleMode: ScaleMode?
        public let priority: Int?
        public let fidelity: Fidelity?
        public let attentionBehavior: AttentionBehavior?
        public let whyThisRepresentation: String?
        public let whyThisPlacement: String?

        // Loose-typed payload bag for content the renderer reads opaquely.
        public let sourceContent: AnyCodable?

        public struct Placement: Codable, Sendable {
            public let kind: SpatailPlayer.Placement?
            public let offset: [Float]?
            public let targetId: String?
        }
    }

    public struct Animation: Codable, Sendable, Identifiable {
        public let id: String
        public let primitive: AnimationPrimitive
        public let target: String?
        public let params: AnyCodable?
    }

    public struct Interaction: Codable, Sendable, Identifiable {
        public let id: String
        public let trigger: InteractionTrigger
        public let target: String?
        public let actions: [Action]

        public struct Action: Codable, Sendable {
            public let kind: InteractionAction
            public let ref: String?
            public let params: AnyCodable?

            enum CodingKeys: String, CodingKey {
                case kind = "type"
                case ref
                case params
            }
        }
    }

    public struct Sequence: Codable, Sendable, Identifiable {
        public let id: String
        public let steps: [Step]

        public struct Step: Codable, Sendable, Identifiable {
            public let id: String
            public let label: String?
            public let actions: [Interaction.Action]?
            public let durationMs: Int?
        }
    }

    public struct Explanation: Codable, Sendable {
        public let written: String?
        public let intentSummary: String?
    }

    public struct Mechanic: Codable, Sendable, Identifiable {
        public let id: String
        public let kind: MechanicKind
        public let target: String?
        public let params: AnyCodable?
        public let why: String?
        public let anchorsOn: String?
    }

    public struct Presentation: Codable, Sendable {
        public let layout: PresentationLayout?
        public let ordering: [String]?
    }
}

// MARK: - AnyCodable
//
// Untyped JSON passthrough for fields whose shape varies per mechanic /
// element. When a specific shape stabilises, replace with a real Codable
// struct and remove the AnyCodable wrapper for that field.

public struct AnyCodable: Codable, Sendable, @unchecked Sendable {
    public let value: Any

    public init(_ value: Any) { self.value = value }

    public init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() {                  self.value = NSNull()
        } else if let b = try? c.decode(Bool.self) {    self.value = b
        } else if let i = try? c.decode(Int.self) {     self.value = i
        } else if let d = try? c.decode(Double.self) {  self.value = d
        } else if let s = try? c.decode(String.self) {  self.value = s
        } else if let a = try? c.decode([AnyCodable].self) { self.value = a.map(\.value)
        } else if let o = try? c.decode([String: AnyCodable].self) {
            self.value = o.mapValues(\.value)
        } else {
            throw DecodingError.typeMismatch(
                AnyCodable.self,
                .init(codingPath: decoder.codingPath,
                      debugDescription: "Unsupported JSON value"))
        }
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch value {
        case is NSNull:           try c.encodeNil()
        case let b as Bool:       try c.encode(b)
        case let i as Int:        try c.encode(i)
        case let d as Double:     try c.encode(d)
        case let s as String:     try c.encode(s)
        case let a as [Any]:      try c.encode(a.map(AnyCodable.init))
        case let o as [String: Any]: try c.encode(o.mapValues(AnyCodable.init))
        default:
            throw EncodingError.invalidValue(
                value,
                .init(codingPath: encoder.codingPath,
                      debugDescription: "Unsupported value"))
        }
    }
}
