// SpatailCore.swift — shared model types for the Spatail app.
// Wire-aligned with docs/xr/LIVE_BRAIN_SPEC.md §1–§3. Every module imports these;
// no module redefines them. Keep this file dependency-light (no ARKit/RealityKit) so
// the types stay Codable, testable, and serializable straight into wire payloads.

import Foundation
import CoreGraphics
import simd

// MARK: - Surfaces

/// Closed set of room-surface classifications shared with the PC brain.
public enum SurfaceKind: String, Codable, CaseIterable, Sendable {
    case floor, wall, ceiling, table, seat, door, window, unknown
}

/// An oriented bounding box about gravity-aligned +Y (LIVE_BRAIN_SPEC §1.2).
/// Center/extents in metres, world space; `yaw` in radians about +Y.
public struct OrientedBox: Codable, Equatable, Sendable {
    public var center: SIMD3<Float>
    public var extents: SIMD3<Float>
    public var yaw: Float

    public init(center: SIMD3<Float>, extents: SIMD3<Float>, yaw: Float) {
        self.center = center
        self.extents = extents
        self.yaw = yaw
    }

    /// The world-space Y of the bottom face — used for support-surface linking.
    public var bottomY: Float { center.y - extents.y / 2 }
    public var volume: Float { extents.x * extents.y * extents.z }
    public var meanExtent: Float { (extents.x + extents.y + extents.z) / 3 }
}

/// One merged room surface with its true (possibly concave) boundary.
/// Replaces the per-anchor convex slabs: one real table = one RoomSurface.
public struct RoomSurface: Codable, Identifiable, Equatable, Sendable {
    public var id: String
    public var kind: SurfaceKind
    /// World-space boundary polygon (concave allowed), counter-clockwise, metres.
    public var boundary: [SIMD3<Float>]
    /// Dominant plane height (Y for horizontal surfaces; mid-height for vertical).
    public var y: Float
    public var areaM2: Float
    /// Classification confidence 0–1 (face-vote-derived for LiDAR surfaces).
    public var confidence: Float
    /// ARMeshAnchor / ARPlaneAnchor identifiers merged into this surface.
    public var anchorIdentifiers: [UUID]

    public init(id: String, kind: SurfaceKind, boundary: [SIMD3<Float>], y: Float,
                areaM2: Float, confidence: Float, anchorIdentifiers: [UUID]) {
        self.id = id
        self.kind = kind
        self.boundary = boundary
        self.y = y
        self.areaM2 = areaM2
        self.confidence = confidence
        self.anchorIdentifiers = anchorIdentifiers
    }
}

// MARK: - Objects (the registry's currency)

/// A named sub-region of an object ("cap", "handle") — LIVE_BRAIN_SPEC §3 Parts.
public struct SpatailPart: Codable, Identifiable, Equatable, Sendable {
    public var id: UUID
    public var label: String
    /// Normalized [0–1] box in the identified frame's image space (nil for fallbacks).
    public var box: CGRect?
    /// Resolved world region, clamped inside the parent OBB.
    public var region: OrientedBox?
    public var confidence: Float
    /// true when the region was MEASURED by the Form Engine (e.g. the cap found as a
    /// radius step in the fitted revolution profile). A measured part supersedes the
    /// §3 heuristic slice and VLM box resolution. Optional so old payloads decode
    /// (nil reads as false) — additive, wire-safe.
    public var measured: Bool?

    public init(id: UUID = UUID(), label: String, box: CGRect?, region: OrientedBox?,
                confidence: Float, measured: Bool? = nil) {
        self.id = id
        self.label = label
        self.box = box
        self.region = region
        self.confidence = confidence
        self.measured = measured
    }

    /// Convenience: nil-safe measured flag.
    public var isMeasured: Bool { measured == true }
}

// MARK: - Object form (Perception v2 — the Form Engine)

/// The parametric form of a measured object — what the Form Engine fitted (or, when
/// depth collapsed under it, assumed from a class prior). Additive wire field on
/// `room.update.objects[]` (spec §1.2 allows additive); existing consumers ignore it.
public struct ObjectForm: Codable, Equatable, Sendable {
    /// Geometry family the dimensions describe.
    public enum Kind: String, Codable, Sendable {
        /// Surface of revolution about the gravity axis (bottle, can, cup, …).
        case revolution
        /// Oriented box (yaw-only about gravity) — the boxy/unknown path.
        case box
    }

    /// Provenance — HONEST tagging is mandatory: `.measured` means the dimensions
    /// came from fused depth points; `.prior` means depth was unreliable (transparent
    /// object) and the dimensions are class-prior constants, possibly silhouette-scaled.
    public enum Source: String, Codable, Sendable {
        case measured, prior
    }

    public var kind: Kind
    /// Metres, keys as present: revolution → bodyDiameter, height (+ capDiameter,
    /// capHeight when a cap step was found); box → width, depth, height.
    public var dimensions: [String: Float]
    public var source: Source
    /// Fraction (0–1) of the azimuth arc around the object's gravity axis the camera
    /// has observed it from — single-sided vs. surrounded observation.
    public var arcCoverage: Float
    /// Fit residual, metres (RMS radial error for revolution, RMS face distance for
    /// box). 0 for priors — there is nothing measured to have a residual against.
    public var residual: Float

    public init(kind: Kind, dimensions: [String: Float], source: Source,
                arcCoverage: Float, residual: Float) {
        self.kind = kind
        self.dimensions = dimensions
        self.source = source
        self.arcCoverage = arcCoverage
        self.residual = residual
    }

    public var bodyDiameter: Float? { dimensions["bodyDiameter"] }
    public var height: Float? { dimensions["height"] }
    public var capDiameter: Float? { dimensions["capDiameter"] }
    public var capHeight: Float? { dimensions["capHeight"] }
}

// MARK: - Object attributes (Perception v3 §5 — the entity dossier)

/// Accumulated rich identity — what the VLM has LEARNED about this thing over
/// time ("blue keys, English QWERTY"), beyond the label. Additive wire field on
/// `room.update.objects[]` and `vision.identification`/`vision.focus.result`
/// payloads (LIVE_BRAIN_SPEC §5.1/§5.3). Every field optional by design.
public struct ObjectAttributes: Codable, Equatable, Sendable {
    public var colors: [String]?
    public var materials: [String]?
    public var textContent: [String]?
    public var language: String?
    public var brand: String?
    public var state: String?

    public init(colors: [String]? = nil, materials: [String]? = nil,
                textContent: [String]? = nil, language: String? = nil,
                brand: String? = nil, state: String? = nil) {
        self.colors = colors
        self.materials = materials
        self.textContent = textContent
        self.language = language
        self.brand = brand
        self.state = state
    }

    public var isEmpty: Bool {
        (colors?.isEmpty ?? true) && (materials?.isEmpty ?? true)
            && (textContent?.isEmpty ?? true) && language == nil
            && brand == nil && state == nil
    }

    /// Bound on accumulated list fields — the dossier is a summary, not a log.
    public static let maxListEntries = 6

    /// Fold newer observations in: lists union (newest kept, bounded, case-
    /// insensitive dedup), scalars overwrite when the newcomer says something.
    public mutating func merge(_ newer: ObjectAttributes) {
        func union(_ old: [String]?, _ new: [String]?) -> [String]? {
            guard let new, !new.isEmpty else { return old }
            var seen = Set<String>()
            var out: [String] = []
            for v in new + (old ?? []) {
                let key = v.lowercased().trimmingCharacters(in: .whitespaces)
                guard !key.isEmpty, seen.insert(key).inserted else { continue }
                out.append(v)
                if out.count >= Self.maxListEntries { break }
            }
            return out.isEmpty ? nil : out
        }
        colors = union(colors, newer.colors)
        materials = union(materials, newer.materials)
        textContent = union(textContent, newer.textContent)
        language = newer.language ?? language
        brand = newer.brand ?? brand
        state = newer.state ?? state
    }
}

/// A persistent, world-anchored object instance: measured form (ARKit) fused with
/// identity (VLM). Wire-aligned with `room.update.objects[]` (spec §1.2).
public struct SpatailObject: Codable, Identifiable, Equatable, Sendable {
    public var id: UUID
    /// Adopted (debounced) label; nil until identity attaches.
    public var label: String?
    public var confidence: Float
    public var obb: OrientedBox
    public var supportSurfaceId: String?
    public var parts: [SpatailPart]
    /// Fitted parametric form (Form Engine) — nil until a fit lands. Additive wire
    /// field: `room.update.objects[]` simply gains it (spec §1.2 allows additive).
    public var form: ObjectForm?
    /// Supported-by relation (v3 §0): the object this one rests ON/IN (laundry
    /// pile → couch, mug → table). Children keep their own OBB/identity and are
    /// NEVER merge candidates against their parent. Additive wire field.
    public var parentId: UUID?
    /// The entity dossier (v3 §5) — accumulated rich identity. Additive wire field.
    public var attributes: ObjectAttributes?
    /// Debounce state — a candidate label must win twice (or conf ≥ 0.8) to be adopted.
    /// Property defaults required: these are excluded from CodingKeys (not wire fields),
    /// so Decodable synthesis needs them defaulted when decoding wire payloads.
    public var pendingLabel: String? = nil
    public var pendingCount: Int = 0
    /// When the form was last fitted (device uptime clock) — freshness gate for the
    /// registry's smoothing rules. Device-local, NOT a wire field.
    public var formUpdatedAt: TimeInterval? = nil
    /// Consecutive ingest ticks this object was matched by a measurement (resets on
    /// a missed tick). Device-local mint/display discipline, NOT a wire field.
    public var seenStreak: Int = 0
    /// The streak reached the establish threshold at least once — hysteresis so a
    /// once-established object doesn't flicker out of display on one missed tick.
    public var established: Bool = false
    /// THE one shared definition of "the Lens shows this" (chips + overlay boxes +
    /// ask-matching). Assigned by the registry's coherence pass
    /// (`RegistryCoherence.assignDisplayWorthiness`) on every publish: eligible =
    /// labeled OR fitted form OR seen ≥ 3 consecutive ticks; capped at 12 by
    /// (labeled first, then confidence, then recency). Device-local, NOT a wire
    /// field — non-worthy objects never even ride `room.update`.
    public var displayWorthy: Bool = false
    /// When this object was first minted (device uptime clock) — merge-pass
    /// survivor stability ("older id wins" ties). Device-local, NOT a wire field.
    public var firstSeenAt: TimeInterval? = nil
    /// Taxonomy HINT (v3 §5): a coarse classifier word ("couch", "textile") that
    /// conditions priors/gates/templates but is NEVER displayed, never adopted as
    /// `label`, never rides the wire. Device-local.
    public var classHint: String? = nil
    /// When the dossier last changed (device uptime clock). Device-local.
    public var attributesUpdatedAt: TimeInterval? = nil
    /// Last tick this object's OBB projected into the camera frustum, and how
    /// many consecutive ticks it was visible yet unmatched by any measurement —
    /// the ghost-culling clock (v3 §6: expiry only ticks while visible).
    /// Device-local, NOT wire fields.
    public var lastVisibleAt: TimeInterval? = nil
    public var missedWhileVisible: Int = 0
    public var lastMeasuredAt: TimeInterval
    public var lastIdentifiedAt: TimeInterval?

    public init(id: UUID = UUID(), label: String? = nil, confidence: Float = 0,
                obb: OrientedBox, supportSurfaceId: String? = nil,
                parts: [SpatailPart] = [], form: ObjectForm? = nil,
                parentId: UUID? = nil, attributes: ObjectAttributes? = nil,
                pendingLabel: String? = nil,
                pendingCount: Int = 0, formUpdatedAt: TimeInterval? = nil,
                seenStreak: Int = 0, established: Bool = false,
                displayWorthy: Bool = false, firstSeenAt: TimeInterval? = nil,
                classHint: String? = nil,
                lastMeasuredAt: TimeInterval,
                lastIdentifiedAt: TimeInterval? = nil) {
        self.id = id
        self.label = label
        self.confidence = confidence
        self.obb = obb
        self.supportSurfaceId = supportSurfaceId
        self.parts = parts
        self.form = form
        self.parentId = parentId
        self.attributes = attributes
        self.pendingLabel = pendingLabel
        self.pendingCount = pendingCount
        self.formUpdatedAt = formUpdatedAt
        self.seenStreak = seenStreak
        self.established = established
        self.displayWorthy = displayWorthy
        self.firstSeenAt = firstSeenAt
        self.classHint = classHint
        self.lastMeasuredAt = lastMeasuredAt
        self.lastIdentifiedAt = lastIdentifiedAt
    }

    /// Seconds since identity last confirmed — shown by the Truth Overlay.
    public func identificationAge(now: TimeInterval) -> TimeInterval? {
        lastIdentifiedAt.map { now - $0 }
    }

    private enum CodingKeys: String, CodingKey {
        // pendingLabel/pendingCount/formUpdatedAt/seenStreak/established/
        // displayWorthy/firstSeenAt/classHint/attributesUpdatedAt/lastVisibleAt/
        // missedWhileVisible are device internals — not wire fields.
        case id, label, confidence, obb, supportSurfaceId, parts, form
        case parentId, attributes                       // v3 §5.1 additive
        case lastMeasuredAt = "lastSeenAt"
        case lastIdentifiedAt
    }
}

// MARK: - Detections (perception layer currency)

public enum DetectionSourceKind: String, Codable, Sendable {
    case appleVision, coreML, vlm
}

/// How much a detection's label is worth (v3 §5): `semantic` labels may be
/// displayed/adopted; `hint` labels (coarse taxonomy words like "textile",
/// "machine") only condition priors, gates and templates — never identity.
public enum DetectionLabelKind: String, Codable, Sendable {
    case semantic, hint
}

/// A 2D detection in normalized image space, before spatial resolution.
public struct Detection2D: Codable, Identifiable, Equatable, Sendable {
    public var id: UUID
    public var label: String?
    public var confidence: Float
    /// Normalized [0–1] box, origin top-left, in the source frame's image space.
    public var box: CGRect
    public var source: DetectionSourceKind
    public var frameTimestamp: TimeInterval
    /// nil (legacy) reads as `.semantic`.
    public var labelKind: DetectionLabelKind?

    public init(id: UUID = UUID(), label: String?, confidence: Float, box: CGRect,
                source: DetectionSourceKind, frameTimestamp: TimeInterval,
                labelKind: DetectionLabelKind? = nil) {
        self.id = id
        self.label = label
        self.confidence = confidence
        self.box = box
        self.source = source
        self.frameTimestamp = frameTimestamp
        self.labelKind = labelKind
    }

    public var isSemanticLabel: Bool { (labelKind ?? .semantic) == .semantic }
}

/// A detection after depth-grid resolution: measured 3D form (spec §3).
public struct ResolvedDetection: Equatable, Sendable {
    public var detection: Detection2D
    public var obb: OrientedBox
    public var supportSurfaceId: String?
    /// Fraction of grid taps that survived foreground clustering (quality signal).
    public var tapCoverage: Float

    public init(detection: Detection2D, obb: OrientedBox, supportSurfaceId: String?,
                tapCoverage: Float) {
        self.detection = detection
        self.obb = obb
        self.supportSurfaceId = supportSurfaceId
        self.tapCoverage = tapCoverage
    }
}

// MARK: - Brain endpoints

/// Where the PC brain lives. Persisted by Settings; consumed by Net + Uplink.
public struct BrainEndpoints: Codable, Equatable, Sendable {
    /// e.g. "http://spatail-pc.tail-net.ts.net:8788"
    public var jobServerURL: URL
    /// e.g. "ws://spatail-pc.tail-net.ts.net:8798/v1/vision"
    public var visionSocketURL: URL

    public init(jobServerURL: URL, visionSocketURL: URL) {
        self.jobServerURL = jobServerURL
        self.visionSocketURL = visionSocketURL
    }
}

// MARK: - Connection state (honest — spec §0 law 3)

public enum LinkState: Equatable, Sendable {
    case idle
    case connecting
    case streaming
    /// Carries the consecutive-failure count that tripped it.
    case failed(consecutiveFailures: Int)
}
