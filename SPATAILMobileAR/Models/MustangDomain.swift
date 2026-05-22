// MustangDomain.swift
//
// Domain-specific value types named in the architecture spec. For v1
// these are mostly view-model conveniences — the iOS app reads the
// generic SourceContent struct directly from the contract — but they
// give us a typed Swift API for the next iteration where the iPhone
// authors a card on-device (without round-tripping through the backend).

import Foundation

struct ContentCard: Codable, Identifiable, Hashable {
    let id: String
    var title: String
    var prompt: String
    var domain: String?
    var blocks: [ContentBlock]
}

enum ContentBlock: Codable, Hashable {
    case fact(key: String, value: String, group: String?)
    case summary(title: String, body: String)
    case list(title: String, items: [String])
    case steps(title: String, items: [String], targetRef: String?)
    case timeline(title: String, events: [TimelineEvent])
    case decisions(title: String, options: [DecisionOption])
    case object3d(id: String, name: String, role: String, assetGroupRef: String?, targetRef: String?, components: [ComponentEntry])
    case diagnostic(title: String, targetRef: String?, finding: String)

    // Custom Codable so we can switch on a `kind` string the same way
    // the backend card JSONs do — kept for parity if a future iteration
    // ingests these on-device.
    enum CodingKeys: String, CodingKey {
        case kind, key, value, group, title, body, items, steps, events,
             options, id, name, role, assetGroupRef, targetRef, components,
             finding
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let kind = try c.decode(String.self, forKey: .kind)
        switch kind {
        case "fact":
            self = .fact(
                key: try c.decode(String.self, forKey: .key),
                value: try c.decode(String.self, forKey: .value),
                group: try c.decodeIfPresent(String.self, forKey: .group),
            )
        case "summary":
            self = .summary(
                title: try c.decode(String.self, forKey: .title),
                body: try c.decode(String.self, forKey: .body),
            )
        case "list":
            self = .list(
                title: try c.decode(String.self, forKey: .title),
                items: try c.decode([String].self, forKey: .items),
            )
        case "steps":
            self = .steps(
                title: try c.decode(String.self, forKey: .title),
                items: try c.decode([String].self, forKey: .steps),
                targetRef: try c.decodeIfPresent(String.self, forKey: .targetRef),
            )
        case "timeline":
            self = .timeline(
                title: try c.decode(String.self, forKey: .title),
                events: try c.decode([TimelineEvent].self, forKey: .events),
            )
        case "decisions":
            self = .decisions(
                title: try c.decode(String.self, forKey: .title),
                options: try c.decode([DecisionOption].self, forKey: .options),
            )
        case "object3d":
            self = .object3d(
                id: try c.decode(String.self, forKey: .id),
                name: try c.decode(String.self, forKey: .name),
                role: try c.decode(String.self, forKey: .role),
                assetGroupRef: try c.decodeIfPresent(String.self, forKey: .assetGroupRef),
                targetRef: try c.decodeIfPresent(String.self, forKey: .targetRef),
                components: (try? c.decode([ComponentEntry].self, forKey: .components)) ?? [],
            )
        case "diagnostic":
            self = .diagnostic(
                title: try c.decode(String.self, forKey: .title),
                targetRef: try c.decodeIfPresent(String.self, forKey: .targetRef),
                finding: try c.decode(String.self, forKey: .finding),
            )
        default:
            throw DecodingError.dataCorruptedError(
                forKey: .kind, in: c,
                debugDescription: "Unknown ContentBlock kind: \(kind)",
            )
        }
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        switch self {
        case .fact(let key, let value, let group):
            try c.encode("fact", forKey: .kind)
            try c.encode(key, forKey: .key)
            try c.encode(value, forKey: .value)
            try c.encodeIfPresent(group, forKey: .group)
        case .summary(let title, let body):
            try c.encode("summary", forKey: .kind)
            try c.encode(title, forKey: .title)
            try c.encode(body, forKey: .body)
        case .list(let title, let items):
            try c.encode("list", forKey: .kind)
            try c.encode(title, forKey: .title)
            try c.encode(items, forKey: .items)
        case .steps(let title, let items, let targetRef):
            try c.encode("steps", forKey: .kind)
            try c.encode(title, forKey: .title)
            try c.encode(items, forKey: .steps)
            try c.encodeIfPresent(targetRef, forKey: .targetRef)
        case .timeline(let title, let events):
            try c.encode("timeline", forKey: .kind)
            try c.encode(title, forKey: .title)
            try c.encode(events, forKey: .events)
        case .decisions(let title, let options):
            try c.encode("decisions", forKey: .kind)
            try c.encode(title, forKey: .title)
            try c.encode(options, forKey: .options)
        case .object3d(let id, let name, let role, let assetGroupRef, let targetRef, let components):
            try c.encode("object3d", forKey: .kind)
            try c.encode(id, forKey: .id)
            try c.encode(name, forKey: .name)
            try c.encode(role, forKey: .role)
            try c.encodeIfPresent(assetGroupRef, forKey: .assetGroupRef)
            try c.encodeIfPresent(targetRef, forKey: .targetRef)
            try c.encode(components, forKey: .components)
        case .diagnostic(let title, let targetRef, let finding):
            try c.encode("diagnostic", forKey: .kind)
            try c.encode(title, forKey: .title)
            try c.encodeIfPresent(targetRef, forKey: .targetRef)
            try c.encode(finding, forKey: .finding)
        }
    }
}

// ---------------------------------------------------------------------------
// Mustang-specific value types (mirrors the spec list)
// ---------------------------------------------------------------------------

struct VehicleProfile: Hashable {
    var year: Int
    var make: String
    var model: String
    var trim: String?
    var vin: String?
    var mileage: Int
    var lastServiced: String?
}

struct MaintenanceItem: Hashable {
    var title: String
    var interval: String?
    var dueState: String  // e.g. "overdue", "due", "ok"
    var notes: String?
}

struct InsuranceInfo: Hashable {
    var carrier: String
    var policyNumber: String?
    var validThrough: String?
    var deductibleCollision: String?
    var deductibleComprehensive: String?
    var roadsideAssistance: String?
}

struct ServiceHistoryItem: Hashable {
    var date: String
    var summary: String
}

struct RepairStep: Hashable, Identifiable {
    var index: Int
    var instruction: String
    var id: Int { index }
}

struct ToolItem: Hashable, Identifiable {
    var name: String
    var note: String?
    var id: String { name }
}

struct DiagnosticFinding: Hashable {
    var title: String
    var detail: String
    var severity: String  // "info" | "warn" | "fix_now"
    var anchorObjectId: String?
}

// ---------------------------------------------------------------------------
// SpatialReasoningDecision — captured per element as a presentational tuple
// for the SpatialPlanPreviewView.
// ---------------------------------------------------------------------------

struct SpatialReasoningDecision: Hashable {
    var elementId: String
    var elementTitle: String
    var representationMode: RepresentationMode
    var placementKind: String
    var anchorStrategy: String
    var scaleMode: String
    var attentionBehavior: String
    var whyRepresentation: String
    var whyPlacement: String
}
