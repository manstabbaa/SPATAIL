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
    /// Debounce state — a candidate label must win twice (or conf ≥ 0.8) to be adopted.
    /// Property defaults required: these are excluded from CodingKeys (not wire fields),
    /// so Decodable synthesis needs them defaulted when decoding wire payloads.
    public var pendingLabel: String? = nil
    public var pendingCount: Int = 0
    /// When the form was last fitted (device uptime clock) — freshness gate for the
    /// registry's smoothing rules. Device-local, NOT a wire field.
    public var formUpdatedAt: TimeInterval? = nil
    public var lastMeasuredAt: TimeInterval
    public var lastIdentifiedAt: TimeInterval?

    public init(id: UUID = UUID(), label: String? = nil, confidence: Float = 0,
                obb: OrientedBox, supportSurfaceId: String? = nil,
                parts: [SpatailPart] = [], form: ObjectForm? = nil,
                pendingLabel: String? = nil,
                pendingCount: Int = 0, formUpdatedAt: TimeInterval? = nil,
                lastMeasuredAt: TimeInterval,
                lastIdentifiedAt: TimeInterval? = nil) {
        self.id = id
        self.label = label
        self.confidence = confidence
        self.obb = obb
        self.supportSurfaceId = supportSurfaceId
        self.parts = parts
        self.form = form
        self.pendingLabel = pendingLabel
        self.pendingCount = pendingCount
        self.formUpdatedAt = formUpdatedAt
        self.lastMeasuredAt = lastMeasuredAt
        self.lastIdentifiedAt = lastIdentifiedAt
    }

    /// Seconds since identity last confirmed — shown by the Truth Overlay.
    public func identificationAge(now: TimeInterval) -> TimeInterval? {
        lastIdentifiedAt.map { now - $0 }
    }

    private enum CodingKeys: String, CodingKey {
        // pendingLabel/pendingCount/formUpdatedAt are device internals — not wire fields.
        case id, label, confidence, obb, supportSurfaceId, parts, form
        case lastMeasuredAt = "lastSeenAt"
        case lastIdentifiedAt
    }
}

// MARK: - Detections (perception layer currency)

public enum DetectionSourceKind: String, Codable, Sendable {
    case appleVision, coreML, vlm
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

    public init(id: UUID = UUID(), label: String?, confidence: Float, box: CGRect,
                source: DetectionSourceKind, frameTimestamp: TimeInterval) {
        self.id = id
        self.label = label
        self.confidence = confidence
        self.box = box
        self.source = source
        self.frameTimestamp = frameTimestamp
    }
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
