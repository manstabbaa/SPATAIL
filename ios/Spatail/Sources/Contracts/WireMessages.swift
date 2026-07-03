// WireMessages.swift — the vision-socket wire shapes (LIVE_BRAIN_SPEC §1,
// REALTIME_PROTOCOL §10). Text messages on ws://<pc>:8798/v1/vision are JSON
// envelopes {type, payload}; binary messages are JPEG camera frames.
//
// Downlink (PC → phone): vision.identification, experience.delta.
// Uplink   (phone → PC): room.update, pose.update.
//
// Decoding is TOLERANT (missing/mistyped keys → defaults) so a creative VLM or
// an older engine can never fail the whole decode. Field names verified against
// pipeline/server/spatail_vision_engine.py (IdentifyResult.to_dict, _handle_control)
// and pipeline/spatail/room_contract_adapter.js (toBrainRoom/toBrainPose).

import Foundation
import simd

// MARK: - Envelope

/// Peek at a text message's type before committing to a payload shape.
struct WireEnvelopeHeader: Decodable {
    let type: String
}

/// A fully-typed downlink envelope, decoded once the type is known.
struct WireEnvelope<Payload: Decodable>: Decodable {
    let type: String
    let payload: Payload
}

// MARK: - vision.identification (PC → phone, ~1 Hz)

/// The VLM's answer for one uplinked frame (spec §1.3). Engine payload:
/// { detections: [{label, confidence, box}], primary, rawText, latencyMs,
///   model, parts: [{label, box?, confidence?}], frameTimestamp }
struct IdentificationWire: Decodable, Equatable {

    /// One detected thing. Identity is POSITIONAL (index in this tick's list),
    /// never label-keyed — two "cup"s in frame must not collapse into one row
    /// (the diagnosed SPATAILMobileAR bug: `var id: String { label }`).
    struct Detection: Identifiable, Equatable {
        let index: Int
        let label: String
        let confidence: Double
        /// Normalized [x, y, w, h], origin top-left, or nil when the VLM only
        /// returned a label (boxes are hints, not ground truth).
        let box: [Double]?
        var id: Int { index }
    }

    /// A sub-part of the PRIMARY object (spec §1.3): box in the same image
    /// space as detections[].box; may be absent (label-only fallback).
    struct Part: Decodable, Equatable {
        let label: String
        let box: [Double]?
        /// The engine omits the number when the VLM gave none — 0.5 means
        /// "asserted without a stated confidence", not zero belief.
        let confidence: Double

        enum K: String, CodingKey { case label, box, confidence }
        init(from d: Decoder) throws {
            let c = try d.container(keyedBy: K.self)
            label = (try? c.decode(String.self, forKey: .label)) ?? ""
            box = try? c.decode([Double].self, forKey: .box)
            confidence = (try? c.decode(Double.self, forKey: .confidence)) ?? 0.5
        }
    }

    let primary: String?
    /// Primary-label confidence. The engine carries it inside detections[0]
    /// rather than top-level, so decoding falls back to the detection that
    /// matches `primary` (then the first detection) when no top-level key.
    let confidence: Double?
    let detections: [Detection]
    let parts: [Part]?
    /// Capture/ingest time (epoch seconds, PC clock) of the identified frame.
    let frameTimestamp: Double?
    let latencyMs: Int?
    let model: String?
    let rawText: String?

    enum K: String, CodingKey {
        case primary, confidence, detections, parts, frameTimestamp
        case latencyMs, latency, model, rawText
    }

    private struct RawDetection: Decodable {
        var label = "object"
        var confidence = 0.0
        var box: [Double]?
        enum K: String, CodingKey { case label, confidence, box }
        init(from d: Decoder) throws {
            let c = try d.container(keyedBy: K.self)
            label = (try? c.decode(String.self, forKey: .label)) ?? "object"
            confidence = (try? c.decode(Double.self, forKey: .confidence)) ?? 0
            box = try? c.decode([Double].self, forKey: .box)
        }
    }

    init(from d: Decoder) throws {
        let c = try d.container(keyedBy: K.self)
        primary = try? c.decode(String.self, forKey: .primary)
        let raws = (try? c.decode([RawDetection].self, forKey: .detections)) ?? []
        detections = raws.enumerated().map { i, r in
            Detection(index: i, label: r.label, confidence: r.confidence, box: r.box)
        }
        if let top = try? c.decode(Double.self, forKey: .confidence) {
            confidence = top
        } else if let p = primary,
                  let match = detections.first(where: { $0.label == p }) {
            confidence = match.confidence
        } else {
            confidence = detections.first?.confidence
        }
        parts = (try? c.decode([Part].self, forKey: .parts))?
            .filter { !$0.label.isEmpty }
        frameTimestamp = try? c.decode(Double.self, forKey: .frameTimestamp)
        latencyMs = (try? c.decode(Int.self, forKey: .latencyMs))
                 ?? (try? c.decode(Int.self, forKey: .latency))
        model = try? c.decode(String.self, forKey: .model)
        rawText = try? c.decode(String.self, forKey: .rawText)
    }
}

// MARK: - experience.delta (PC → phone)

/// The fusion brain's placement decision. `kind == "full"` carries a complete
/// SpatialExperienceContract; versions are monotonic (REALTIME_PROTOCOL §4).
/// The runtime applies these DIFF-BASED by element id — never rebuild.
struct ExperienceDeltaWire: Decodable {
    let version: Int
    let kind: String                              // "full" | "patch"
    let experience: SpatialExperienceContract?
    /// The brain's binding decision (plan_from_room.js `fused`): which surface
    /// or tracked object the identity landed on and WHY. Additive — absent on
    /// payloads that don't forward it; the Truth Overlay just hides the card.
    let fused: FusedBindingWire?
    /// Part-addressed placement (spatail_vision_engine delta assembly): the brain
    /// asks the runtime to land the plan ON this registry object (optionally on a
    /// named part of it). Additive — absent on non-part-addressed plans; the
    /// runtime routes it into the PlacementTarget .object/.part solve path.
    let target: TargetRefWire?

    enum K: String, CodingKey { case version, kind, experience, fused, target }
    init(from d: Decoder) throws {
        let c = try d.container(keyedBy: K.self)
        version = (try? c.decode(Int.self, forKey: .version)) ?? 0
        kind = (try? c.decode(String.self, forKey: .kind)) ?? "full"
        experience = try? c.decode(SpatialExperienceContract.self, forKey: .experience)
        fused = try? c.decode(FusedBindingWire.self, forKey: .fused)
        target = try? c.decode(TargetRefWire.self, forKey: .target)
    }
}

/// The brain's part-addressed anchor request: `{objectId, part}` — objectId is
/// our registry UUID echoed back (or, defensively, the noun the VLM used);
/// `part` names a sub-region ("cap"). Every field optional — tolerant by design.
struct TargetRefWire: Decodable, Equatable {
    let objectId: String?
    let part: String?

    enum K: String, CodingKey { case objectId, part }
    init(from d: Decoder) throws {
        let c = try d.container(keyedBy: K.self)
        objectId = try? c.decode(String.self, forKey: .objectId)
        part = try? c.decode(String.self, forKey: .part)
    }
}

/// The fusion brain's binding provenance (surface_fusion.js labeledSurface →
/// plan_from_room.js `fused`). Every field optional — tolerant by design.
struct FusedBindingWire: Decodable, Equatable {
    let label: String?
    let kind: String?                             // surface kind | "object"
    let surfaceId: String?
    let supportSurfaceId: String?
    let objectId: String?
    let confidence: Double?
    let matchReason: String?
}

// MARK: - pose.update (phone → PC, ≤ 2 Hz — spec §1.1)

/// Keeps the brain's gaze ray live between room sends. Never per-frame.
struct PoseWire: Codable, Equatable {
    var position: [Float]
    var forward: [Float]
    var up: [Float]
    var timestamp: Double        // epoch seconds

    init(position: [Float], forward: [Float], up: [Float], timestamp: Double) {
        self.position = position
        self.forward = forward
        self.up = up
        self.timestamp = timestamp
    }

    /// Build from an ARKit camera transform: position = translation column,
    /// forward = -Z column, up = +Y column (camera looks down its -Z).
    init(cameraTransform t: simd_float4x4, timestamp: Double) {
        position = [t.columns.3.x, t.columns.3.y, t.columns.3.z]
        forward = [-t.columns.2.x, -t.columns.2.y, -t.columns.2.z]
        up = [t.columns.1.x, t.columns.1.y, t.columns.1.z]
        self.timestamp = timestamp
    }
}

struct PoseUpdateEnvelope: Encodable {
    var type = "pose.update"
    var payload: PoseWire
}

// MARK: - room.update (phone → PC, throttled — spec §1.2)

/// The room payload the engine's room_contract_adapter normalizes: the proven
/// SPATAILMobileAR RoomContract shape ("0.4.0-spatail-room") EXTENDED per spec
/// §1.2 with objects[] (tracked-object OBBs) and per-surface confidence +
/// concave boundary. `polygon` stays the adapter's required field; `boundary`
/// carries the same vertices under the spec's additive name.
struct RoomContractWire: Codable {
    var schemaVersion = "0.4.0-spatail-room"
    var roomId: String
    var capturedAt: String            // ISO-8601
    var device: String
    var lidar: Bool
    var boundingBox: BoundingBoxWire
    var surfaces: [RoomSurfaceWire]
    var anchorablePoints: [AnchorablePointWire] = []
    var preferredWallId: String?
    var preferredTableId: String?
    /// Spec §1.2 — SpatailObject is wire-aligned Codable (obb center/extents
    /// encode as [x,y,z] arrays; lastMeasuredAt encodes as "lastSeenAt").
    var objects: [SpatailObject]

    /// Snapshot the live scanner + registry state into the wire shape.
    init(surfaces: [RoomSurface], objects: [SpatailObject], roomId: String,
         device: String, lidar: Bool, capturedAt: Date = Date()) {
        self.roomId = roomId
        self.capturedAt = Self.iso8601.string(from: capturedAt)
        self.device = device
        self.lidar = lidar
        self.surfaces = surfaces.map { RoomSurfaceWire(core: $0, lidar: lidar) }
        self.objects = objects
        self.boundingBox = BoundingBoxWire(containing: surfaces)
        self.preferredWallId = Self.largestSurfaceId(of: .wall, in: surfaces)
        self.preferredTableId = Self.largestSurfaceId(of: .table, in: surfaces)
    }

    private static let iso8601 = ISO8601DateFormatter()

    private static func largestSurfaceId(of kind: SurfaceKind,
                                         in surfaces: [RoomSurface]) -> String? {
        surfaces.filter { $0.kind == kind }.max(by: { $0.areaM2 < $1.areaM2 })?.id
    }
}

struct BoundingBoxWire: Codable {
    var min: [Float]
    var max: [Float]

    init(containing surfaces: [RoomSurface]) {
        var lo = SIMD3<Float>(repeating: .greatestFiniteMagnitude)
        var hi = SIMD3<Float>(repeating: -.greatestFiniteMagnitude)
        for s in surfaces {
            for v in s.boundary {
                lo = simd_min(lo, v)
                hi = simd_max(hi, v)
            }
        }
        if lo.x > hi.x { lo = .zero; hi = .zero }   // no geometry yet
        min = [lo.x, lo.y, lo.z]
        max = [hi.x, hi.y, hi.z]
    }
}

struct RoomSurfaceWire: Codable {
    var id: String
    var kind: SurfaceKind
    /// World-space vertices [[x,y,z]] — the adapter's required field (≥ 3).
    var polygon: [[Float]]
    var normal: [Float]
    var area: Float
    /// Dominant plane height (world Y). The adapter reads it as heightMeters.
    var height: Float?
    var source: String                // "lidar" | "plane_heuristic"
    var confidence: Float?            // spec §1.2 additive
    var boundary: [[Float]]?          // spec §1.2 additive (concave allowed)

    init(core s: RoomSurface, lidar: Bool) {
        id = s.id
        kind = s.kind
        polygon = s.boundary.map { [$0.x, $0.y, $0.z] }
        boundary = polygon
        normal = Self.normal(for: s)
        area = s.areaM2
        height = s.y
        source = lidar ? "lidar" : "plane_heuristic"
        confidence = s.confidence
    }

    /// Outward-ish normal for the brain's planner. Horizontal kinds snap to
    /// ±Y; vertical kinds use the boundary's Newell normal flattened to the
    /// horizontal plane (walls are gravity-aligned by construction).
    static func normal(for s: RoomSurface) -> [Float] {
        switch s.kind {
        case .floor, .table, .seat:
            return [0, 1, 0]
        case .ceiling:
            return [0, -1, 0]
        case .wall, .door, .window:
            var n = newellNormal(of: s.boundary)
            n.y = 0
            let len = simd_length(n)
            guard len > 1e-5 else { return [0, 0, 1] }
            n /= len
            return [n.x, n.y, n.z]
        case .unknown:
            let n = newellNormal(of: s.boundary)
            let len = simd_length(n)
            guard len > 1e-5 else { return [0, 1, 0] }
            return [n.x / len, n.y / len, n.z / len]
        }
    }

    /// Newell's method — robust polygon normal, concave-safe.
    static func newellNormal(of points: [SIMD3<Float>]) -> SIMD3<Float> {
        guard points.count >= 3 else { return .zero }
        var n = SIMD3<Float>(0, 0, 0)
        for i in 0..<points.count {
            let a = points[i]
            let b = points[(i + 1) % points.count]
            n.x += (a.y - b.y) * (a.z + b.z)
            n.y += (a.z - b.z) * (a.x + b.x)
            n.z += (a.x - b.x) * (a.y + b.y)
        }
        return n
    }
}

/// Tap-to-place anchor points (RoomContract lineage). Kept for wire fidelity;
/// the live app sends an empty list until a user-tagged anchor flow exists.
struct AnchorablePointWire: Codable {
    var id: String
    var position: [Float]
    var surfaceId: String?
    var label: String?
    var createdAt: String
}

struct RoomUpdateEnvelope: Encodable {
    struct Payload: Encodable {
        var room: RoomContractWire
        var pose: PoseWire?
        /// The user's active ask (spec §1.2) — scopes live replans to a question
        /// so the brain can emit part-addressed targets. Absent (nil) clears any
        /// held concept server-side: stale questions must not steer later plans.
        var concept: String?
    }
    var type = "room.update"
    var payload: Payload
}
