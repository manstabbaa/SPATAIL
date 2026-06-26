// SpatailPerceptionContract.swift
//
// THE PERCEPTION PACKET — the typed contract that flows OUT of the
// vision + spatial layers and INTO the SPATAIL placement engine.
//
// SPATAIL principle (Master File, "SPACE → Mapping"):
//   Vision detects WHAT the object is.
//   ARKit resolves WHERE it exists in the world.
//   This file is the frozen handoff between those two facts and the
//   "what should appear there" decision the engine makes next.
//
// Design rules:
//   * Pure value types. Foundation + simd ONLY — no ARKit, no RealityKit.
//     That keeps the contract testable off-device and portable to the
//     visionOS / Android-XR players the Master File anticipates.
//   * Everything is Codable so a frame can be logged, replayed, or sent
//     to a server-side VLM without reshaping.
//
// Conceptual JSON this mirrors:
//   {
//     frameId, timestamp, cameraTransform,
//     detections: [{ label, confidence, screenBoundingBox,
//                    worldPosition, surfaceNormal, anchorType,
//                    trackingQuality }],
//     sceneContext
//   }

import Foundation
import simd

// ---------------------------------------------------------------------------
// Surface / anchor classification
// ---------------------------------------------------------------------------

/// What kind of real-world surface a detection resolved onto. This is the
/// *resolved* surface class (distinct from the contract's `AnchorStrategy`,
/// which is an authoring choice). Drives the engine's "locus of control"
/// decision — Master File p.29: object-local vs world-local placement.
enum SpatailAnchorType: String, Codable, Hashable {
    case floor
    case table
    case wall
    case ceiling
    case seat
    case object      // resolved onto a recognized object / scene-mesh face
    case unknown

    var displayName: String { rawValue.capitalized }

    /// Surfaces a flat panel can rest on / hang from.
    var isPlanarSurface: Bool {
        switch self {
        case .floor, .table, .wall, .ceiling, .seat: return true
        case .object, .unknown:                      return false
        }
    }
}

// ---------------------------------------------------------------------------
// Tracking quality
// ---------------------------------------------------------------------------

/// World-tracking confidence at the moment the frame was captured. Mapped
/// from ARKit's `ARCamera.TrackingState` in the AR layer (see
/// SpatialResolver) so the contract stays ARKit-free.
enum SpatailTrackingQuality: String, Codable, Hashable {
    case unavailable   // tracking not yet established
    case limited       // initializing / excessive motion / low features / low light
    case normal        // full 6-DoF tracking
    case unknown

    var displayName: String { rawValue.capitalized }

    /// Whether placements computed under this quality should be trusted
    /// enough to render. `limited` still renders but the debug HUD warns.
    var isUsable: Bool { self == .normal || self == .limited }
}

// ---------------------------------------------------------------------------
// Normalized screen-space bounding box
// ---------------------------------------------------------------------------

/// A detection's bounding box in NORMALIZED screen space: origin top-left,
/// all components in [0, 1]. Detectors convert their native coordinate
/// system (e.g. Vision's bottom-left normalized space) into this convention
/// so the spatial resolver only ever sees one layout.
struct SpatailScreenRect: Codable, Hashable {
    var x: Double          // left edge,   0 = left,  1 = right
    var y: Double          // top edge,    0 = top,   1 = bottom
    var width: Double
    var height: Double

    var centerX: Double { x + width / 2 }
    var centerY: Double { y + height / 2 }

    /// Normalized center point (top-left origin). The resolver raycasts
    /// from here first.
    var center: SIMD2<Double> { SIMD2(centerX, centerY) }

    static let screenCenter = SpatailScreenRect(x: 0.34, y: 0.34, width: 0.32, height: 0.32)
}

// ---------------------------------------------------------------------------
// World hit — the resolver's output
// ---------------------------------------------------------------------------

/// The 3D resolution of a 2D detection: where, on what, and how sure.
/// Returned by `SpatialResolver` and stitched back onto its detection.
struct SpatailWorldHit: Codable, Hashable {
    /// World-space position of the bounding-box center on the hit surface.
    var worldPosition: SIMD3<Float>
    /// Surface normal at the hit, world space. nil when only a point was
    /// recovered (e.g. estimated plane without orientation).
    var surfaceNormal: SIMD3<Float>?
    /// Which surface class the hit landed on.
    var anchorType: SpatailAnchorType
    /// Straight-line distance from the camera to the hit, meters.
    var distanceMeters: Float
    /// 0..1 — how trustworthy this resolution is (hit type + distance +
    /// tracking). Existing-plane geometry scores higher than an estimated
    /// plane, which scores higher than a camera-ray fallback.
    var confidence: Float
    /// Tracking quality captured alongside the raycast.
    var trackingQuality: SpatailTrackingQuality
    /// How the hit was obtained — useful in the debug HUD and for the
    /// depth-upgrade path (see SpatialResolver TODO).
    var method: ResolveMethod

    enum ResolveMethod: String, Codable, Hashable {
        case existingPlaneGeometry  // raycast against a detected plane's mesh
        case existingPlane          // raycast against a detected plane's extent
        case estimatedPlane         // raycast against an on-the-fly estimated plane
        case sceneDepth             // LiDAR depth at the bbox center (future)
        case cameraRayFallback      // fixed distance along the camera ray (last resort)
    }
}

// ---------------------------------------------------------------------------
// Detection — a single recognized thing in the frame
// ---------------------------------------------------------------------------

/// One detection. The DETECTION layer fills `label`, `confidence`,
/// `screenBoundingBox` and leaves `worldHit == nil`. The SPATIAL layer
/// (SpatialResolver) returns an enriched copy with `worldHit` populated.
/// This single type crossing both layers is intentional — it keeps the
/// detection→resolution boundary obvious while staying one Codable shape.
struct SpatailDetection: Codable, Hashable, Identifiable {
    /// Stable per emitted detection; lets the runtime diff anchors across
    /// frames (same id → update in place, not re-create).
    var id: String
    var label: String
    var confidence: Float
    var screenBoundingBox: SpatailScreenRect

    /// nil until the spatial resolver runs. ← the 2D→3D boundary.
    var worldHit: SpatailWorldHit?

    init(id: String = UUID().uuidString,
         label: String,
         confidence: Float,
         screenBoundingBox: SpatailScreenRect,
         worldHit: SpatailWorldHit? = nil) {
        self.id = id
        self.label = label
        self.confidence = confidence
        self.screenBoundingBox = screenBoundingBox
        self.worldHit = worldHit
    }

    // Convenience accessors so callers can read the resolved facts flatly,
    // matching the conceptual JSON, without unwrapping worldHit everywhere.
    var worldPosition: SIMD3<Float>? { worldHit?.worldPosition }
    var surfaceNormal: SIMD3<Float>? { worldHit?.surfaceNormal }
    var anchorType: SpatailAnchorType { worldHit?.anchorType ?? .unknown }
    var isResolved: Bool { worldHit != nil }
}

// ---------------------------------------------------------------------------
// Scene context — the semantic RoomModel snapshot
// ---------------------------------------------------------------------------

/// A lightweight snapshot of what ARKit knows about the room this frame.
/// Master File p.22: "ARKit/RoomPlan scans surfaces (floor, table, walls)
/// and the LiDAR scene mesh into obstacles, producing a semantic RoomModel
/// (surfaces, obstacles, user pose, light)." This is that, condensed to the
/// fields the placement engine actually reasons over.
struct SpatailSceneContext: Codable, Hashable {
    var trackingQuality: SpatailTrackingQuality
    var detectedPlaneCount: Int
    var hasHorizontalSurface: Bool
    var hasVerticalSurface: Bool
    var hasFloor: Bool
    var hasTable: Bool
    var hasWall: Bool
    /// LiDAR scene reconstruction available + running (enables depth refine
    /// and object-face hits).
    var sceneMeshAvailable: Bool
    /// Ambient intensity in lux if the camera reported it; nil otherwise.
    var ambientIntensityLux: Double?
    /// ARKit world-mapping maturity string, surfaced for the HUD.
    var worldMappingStatus: String?

    static let empty = SpatailSceneContext(
        trackingQuality: .unavailable,
        detectedPlaneCount: 0,
        hasHorizontalSurface: false,
        hasVerticalSurface: false,
        hasFloor: false,
        hasTable: false,
        hasWall: false,
        sceneMeshAvailable: false,
        ambientIntensityLux: nil,
        worldMappingStatus: nil,
    )
}

// ---------------------------------------------------------------------------
// Perception frame — the whole packet
// ---------------------------------------------------------------------------

/// One tick of perception: the camera pose, everything detected, and the
/// room context — the complete input the placement engine consumes.
///
/// Codable is provided explicitly (see extension below) because the camera
/// transform is a `simd_float4x4`, whose Codable conformance is not relied
/// upon — it is encoded as 16 column-major floats so the packet round-trips
/// on any toolchain (and to a server-side VLM).
struct SpatailPerceptionFrame: Identifiable {
    /// Monotonic frame counter from the session loop.
    var frameId: Int
    /// ARFrame timestamp (seconds, device clock — NOT wall clock).
    var timestamp: TimeInterval
    /// Camera→world transform at capture (column-major, ARKit convention).
    var cameraTransform: simd_float4x4
    var detections: [SpatailDetection]
    var sceneContext: SpatailSceneContext

    var id: Int { frameId }

    /// Camera world position, pulled from the transform's translation column.
    var cameraPosition: SIMD3<Float> {
        SIMD3(cameraTransform.columns.3.x,
              cameraTransform.columns.3.y,
              cameraTransform.columns.3.z)
    }

    /// Detections that have been resolved into the world. The engine only
    /// places against these.
    var resolvedDetections: [SpatailDetection] { detections.filter { $0.isResolved } }
}

// Explicit Codable so the matrix encodes as a flat [Float] (column-major).
extension SpatailPerceptionFrame: Codable {
    enum CodingKeys: String, CodingKey {
        case frameId, timestamp, cameraTransform, detections, sceneContext
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        frameId = try c.decode(Int.self, forKey: .frameId)
        timestamp = try c.decode(TimeInterval.self, forKey: .timestamp)
        let floats = try c.decode([Float].self, forKey: .cameraTransform)
        cameraTransform = SpatailPerceptionFrame.matrix(from: floats)
        detections = try c.decode([SpatailDetection].self, forKey: .detections)
        sceneContext = try c.decode(SpatailSceneContext.self, forKey: .sceneContext)
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(frameId, forKey: .frameId)
        try c.encode(timestamp, forKey: .timestamp)
        try c.encode(SpatailPerceptionFrame.floats(from: cameraTransform), forKey: .cameraTransform)
        try c.encode(detections, forKey: .detections)
        try c.encode(sceneContext, forKey: .sceneContext)
    }

    /// Column-major flatten / unflatten.
    static func floats(from m: simd_float4x4) -> [Float] {
        [m.columns.0.x, m.columns.0.y, m.columns.0.z, m.columns.0.w,
         m.columns.1.x, m.columns.1.y, m.columns.1.z, m.columns.1.w,
         m.columns.2.x, m.columns.2.y, m.columns.2.z, m.columns.2.w,
         m.columns.3.x, m.columns.3.y, m.columns.3.z, m.columns.3.w]
    }

    static func matrix(from f: [Float]) -> simd_float4x4 {
        guard f.count == 16 else { return matrix_identity_float4x4 }
        return simd_float4x4(SIMD4(f[0], f[1], f[2], f[3]),
                             SIMD4(f[4], f[5], f[6], f[7]),
                             SIMD4(f[8], f[9], f[10], f[11]),
                             SIMD4(f[12], f[13], f[14], f[15]))
    }
}
