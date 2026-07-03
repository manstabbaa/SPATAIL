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

    public init(id: UUID = UUID(), label: String, box: CGRect?, region: OrientedBox?,
                confidence: Float) {
        self.id = id
        self.label = label
        self.box = box
        self.region = region
        self.confidence = confidence
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
    /// Debounce state — a candidate label must win twice (or conf ≥ 0.8) to be adopted.
    /// Property defaults required: these are excluded from CodingKeys (not wire fields),
    /// so Decodable synthesis needs them defaulted when decoding wire payloads.
    public var pendingLabel: String? = nil
    public var pendingCount: Int = 0
    public var lastMeasuredAt: TimeInterval
    public var lastIdentifiedAt: TimeInterval?

    public init(id: UUID = UUID(), label: String? = nil, confidence: Float = 0,
                obb: OrientedBox, supportSurfaceId: String? = nil,
                parts: [SpatailPart] = [], pendingLabel: String? = nil,
                pendingCount: Int = 0, lastMeasuredAt: TimeInterval,
                lastIdentifiedAt: TimeInterval? = nil) {
        self.id = id
        self.label = label
        self.confidence = confidence
        self.obb = obb
        self.supportSurfaceId = supportSurfaceId
        self.parts = parts
        self.pendingLabel = pendingLabel
        self.pendingCount = pendingCount
        self.lastMeasuredAt = lastMeasuredAt
        self.lastIdentifiedAt = lastIdentifiedAt
    }

    /// Seconds since identity last confirmed — shown by the Truth Overlay.
    public func identificationAge(now: TimeInterval) -> TimeInterval? {
        lastIdentifiedAt.map { now - $0 }
    }

    private enum CodingKeys: String, CodingKey {
        // pendingLabel/pendingCount are debounce internals — not wire fields.
        case id, label, confidence, obb, supportSurfaceId, parts
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
