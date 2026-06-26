// SpatialResolver.swift
//
// ════════════════════════════════════════════════════════════════════════
//  THE 2D → 3D BOUNDARY.  "ARKit resolves WHERE it exists in the world."
// ════════════════════════════════════════════════════════════════════════
//
// Takes a 2D detection (normalized screen box) and the live AR session and
// returns the SAME detection enriched with a SpatailWorldHit: a world
// position, a surface normal, a resolved anchor type, and a confidence.
//
// This is the ONLY perception file besides the session host that imports
// ARKit. Everything upstream (detection) and downstream (engine) is AR-free.
//
// Strategy, best → worst:
//   1. raycast against existing detected-plane GEOMETRY  (most accurate)
//   2. raycast against an existing plane's infinite extent
//   3. raycast against an on-the-fly ESTIMATED plane
//   4. (future) sample LiDAR sceneDepth at the box center
//   5. fixed distance along the camera ray                (last resort)

import Foundation
import ARKit
import RealityKit
import simd

@MainActor
struct SpatialResolver {

    // MARK: - Public API

    /// Resolve one detection into the world. Returns an enriched copy
    /// (worldHit set) or the original unchanged if nothing could be hit.
    func resolve(_ detection: SpatailDetection,
                 in arView: ARView,
                 frame: ARFrame) -> SpatailDetection {
        let viewport = arView.bounds.size
        guard viewport.width > 0, viewport.height > 0 else { return detection }

        // Normalized (top-left) box center → a point in ARView coordinates.
        let center = detection.screenBoundingBox.center
        let normalizedViewPoint = CGPoint(x: center.x, y: center.y)
        let screenPoint = CGPoint(x: center.x * Double(viewport.width),
                                  y: center.y * Double(viewport.height))

        let tracking = SpatailTrackingQuality(frame.camera.trackingState)
        let interfaceOrientation = arView.window?.windowScene?.interfaceOrientation ?? .portrait

        // 1. LiDAR depth first when available — locates the actual surface at
        //    the box center, including non-planar objects.
        if let hit = depthHit(normalizedViewPoint: normalizedViewPoint, viewport: viewport,
                              interfaceOrientation: interfaceOrientation, frame: frame, tracking: tracking) {
            var enriched = detection
            enriched.worldHit = hit
            return enriched
        }

        // 2. Plane raycast (works on non-LiDAR devices too).
        if let hit = raycastHit(from: screenPoint, in: arView, frame: frame, tracking: tracking) {
            var enriched = detection
            enriched.worldHit = hit
            return enriched
        }

        // 3. Last resort: a point at a fixed distance straight down the ray.
        if let fallback = cameraRayFallback(from: screenPoint, in: arView, frame: frame, tracking: tracking) {
            var enriched = detection
            enriched.worldHit = fallback
            return enriched
        }

        return detection
    }

    /// Convenience: resolve a whole batch against the same frame.
    func resolveAll(_ detections: [SpatailDetection],
                    in arView: ARView,
                    frame: ARFrame) -> [SpatailDetection] {
        detections.map { resolve($0, in: arView, frame: frame) }
    }

    // MARK: - Raycast strategies

    private func raycastHit(from point: CGPoint,
                            in arView: ARView,
                            frame: ARFrame,
                            tracking: SpatailTrackingQuality) -> SpatailWorldHit? {
        // Ordered preference: real geometry, then infinite plane, then
        // estimated. `.any` alignment so we match tables, floors AND walls.
        let attempts: [(ARRaycastQuery.Target, SpatailWorldHit.ResolveMethod)] = [
            (.existingPlaneGeometry, .existingPlaneGeometry),
            (.existingPlaneInfinite, .existingPlane),
            (.estimatedPlane,        .estimatedPlane),
        ]

        for (target, method) in attempts {
            // SPATAIL_NEEDS_MAC_BUILD_VERIFY: ARView.raycast(from:allowing:alignment:)
            // signature is stable iOS 13.4+. `.existingPlaneGeometry` requires
            // plane detection on (it is — see LivePerceptionCoordinator config).
            guard let result = arView.raycast(from: point, allowing: target, alignment: .any).first
            else { continue }

            let position = result.worldTransform.columns.3
            let worldPos = SIMD3<Float>(position.x, position.y, position.z)
            let normal = surfaceNormal(from: result.worldTransform)
            let anchorType = classify(result: result, frame: frame, hitY: worldPos.y)
            let distance = simd_distance(worldPos, frame.camera.transform.columns.3.xyz)

            return SpatailWorldHit(
                worldPosition: worldPos,
                surfaceNormal: normal,
                anchorType: anchorType,
                distanceMeters: distance,
                confidence: confidence(for: method, distance: distance, tracking: tracking),
                trackingQuality: tracking,
                method: method,
            )
        }
        return nil
    }

    /// When no surface is found, place the hit a fixed distance ahead along
    /// the ray through the screen point. anchorType is `.unknown` and the
    /// confidence is low — the engine can choose to show a "scanning" state.
    private func cameraRayFallback(from point: CGPoint,
                                   in arView: ARView,
                                   frame: ARFrame,
                                   tracking: SpatailTrackingQuality) -> SpatailWorldHit? {
        guard let ray = arView.ray(through: point) else { return nil }
        let distance: Float = 0.8
        let worldPos = ray.origin + simd_normalize(ray.direction) * distance
        return SpatailWorldHit(
            worldPosition: worldPos,
            surfaceNormal: -simd_normalize(ray.direction),  // face the camera
            anchorType: .unknown,
            distanceMeters: distance,
            confidence: confidence(for: .cameraRayFallback, distance: distance, tracking: tracking),
            trackingQuality: tracking,
            method: .cameraRayFallback,
        )
    }

    // MARK: - DEPTH PATH (LiDAR)  — real, via DepthSampler

    /// Locate the detection on the actual surface at its box center using the
    /// LiDAR depth map. Returns nil on non-LiDAR devices / invalid samples,
    /// so the caller falls through to raycasting. Master File p.28.
    private func depthHit(normalizedViewPoint: CGPoint,
                          viewport: CGSize,
                          interfaceOrientation: UIInterfaceOrientation,
                          frame: ARFrame,
                          tracking: SpatailTrackingQuality) -> SpatailWorldHit? {
        guard let sample = DepthSampler.sample(
            frame: frame,
            normalizedViewPoint: normalizedViewPoint,
            viewportSize: viewport,
            interfaceOrientation: interfaceOrientation) else { return nil }

        let cameraPos = frame.camera.transform.columns.3.xyz
        let distance = simd_distance(sample.worldPosition, cameraPos)
        // No plane anchor here — classify floor vs. a thing-in-front by height.
        let cameraY = cameraPos.y
        let anchorType: SpatailAnchorType =
            (cameraY - sample.worldPosition.y) > 1.0 ? .floor : .object

        return SpatailWorldHit(
            worldPosition: sample.worldPosition,
            surfaceNormal: sample.normal,
            anchorType: anchorType,
            distanceMeters: distance,
            confidence: confidence(for: .sceneDepth, distance: distance, tracking: tracking),
            trackingQuality: tracking,
            method: .sceneDepth,
        )
    }

    // MARK: - Classification & scoring

    /// Resolve the surface class. Prefers ARKit's semantic plane
    /// classification (LiDAR), falls back to alignment + height heuristics.
    private func classify(result: ARRaycastResult,
                          frame: ARFrame,
                          hitY: Float) -> SpatailAnchorType {
        if let plane = result.anchor as? ARPlaneAnchor {
            switch plane.classification {
            case .floor:   return .floor
            case .table:   return .table
            case .wall:    return .wall
            case .ceiling: return .ceiling
            case .seat:    return .seat
            default:
                return heuristicType(alignment: plane.alignment, frame: frame, hitY: hitY)
            }
        }
        // Estimated plane (no anchor) — use the query's resolved alignment.
        return heuristicType(alignment: result.targetAlignment.planeAlignment,
                             frame: frame, hitY: hitY)
    }

    /// Without semantic classification, guess from orientation + how far
    /// below the camera the hit is. Crude but good enough for v1; LiDAR
    /// devices skip this entirely.
    private func heuristicType(alignment: ARPlaneAnchor.Alignment,
                               frame: ARFrame,
                               hitY: Float) -> SpatailAnchorType {
        if alignment == .vertical { return .wall }
        let cameraY = frame.camera.transform.columns.3.y
        // A surface more than ~1.0 m below eye level reads as the floor;
        // closer-to-eye horizontals read as a table.
        return (cameraY - hitY) > 1.0 ? .floor : .table
    }

    /// The hit transform's local +Y axis approximates the surface normal.
    private func surfaceNormal(from transform: simd_float4x4) -> SIMD3<Float> {
        // SPATAIL_NEEDS_MAC_BUILD_VERIFY: ARRaycastResult.worldTransform is
        // oriented with +Y along the surface normal for plane hits. If a
        // wall normal looks wrong on-device, derive it from the plane
        // anchor's transform instead.
        let up = transform.columns.1
        return simd_normalize(SIMD3<Float>(up.x, up.y, up.z))
    }

    /// 0..1 trust score. Real geometry > infinite plane > estimated >
    /// camera-ray; near hits beat far hits; degraded tracking penalizes all.
    private func confidence(for method: SpatailWorldHit.ResolveMethod,
                            distance: Float,
                            tracking: SpatailTrackingQuality) -> Float {
        let base: Float
        switch method {
        case .existingPlaneGeometry: base = 0.95
        case .existingPlane:         base = 0.85
        case .sceneDepth:            base = 0.90
        case .estimatedPlane:        base = 0.6
        case .cameraRayFallback:     base = 0.3
        }
        // Distance falloff: full trust within 1.5 m, tapering to ~0.6× at 4 m.
        let distanceFactor = max(0.55, 1.0 - max(0, distance - 1.5) * 0.13)
        let trackingFactor: Float = (tracking == .normal) ? 1.0 : (tracking == .limited ? 0.7 : 0.4)
        return min(1.0, base * distanceFactor * trackingFactor)
    }
}

// ---------------------------------------------------------------------------
// Scene-context builder — the semantic RoomModel snapshot (Master File p.22)
// ---------------------------------------------------------------------------

@MainActor
enum SceneContextBuilder {
    /// Summarize what ARKit currently knows about the room into the AR-free
    /// SpatailSceneContext the engine reasons over.
    static func build(from frame: ARFrame, arView: ARView) -> SpatailSceneContext {
        let planes = frame.anchors.compactMap { $0 as? ARPlaneAnchor }
        let horizontals = planes.filter { $0.alignment == .horizontal }
        let verticals = planes.filter { $0.alignment == .vertical }

        func has(_ c: ARPlaneAnchor.Classification) -> Bool {
            planes.contains { $0.classification == c }
        }

        let meshAvailable = ARWorldTrackingConfiguration.supportsSceneReconstruction(.mesh)
            && frame.anchors.contains { $0 is ARMeshAnchor }

        return SpatailSceneContext(
            trackingQuality: SpatailTrackingQuality(frame.camera.trackingState),
            detectedPlaneCount: planes.count,
            hasHorizontalSurface: !horizontals.isEmpty,
            hasVerticalSurface: !verticals.isEmpty,
            // Prefer semantic flags; fall back to "any horizontal = a table".
            hasFloor: has(.floor),
            hasTable: has(.table) || !horizontals.isEmpty,
            hasWall: has(.wall) || !verticals.isEmpty,
            sceneMeshAvailable: meshAvailable,
            ambientIntensityLux: frame.lightEstimate.map { Double($0.ambientIntensity) },
            worldMappingStatus: frame.worldMappingStatus.spatailDescription,
        )
    }
}

// ---------------------------------------------------------------------------
// ARKit → SPATAIL mappings (kept out of the pure contract on purpose)
// ---------------------------------------------------------------------------

extension SpatailTrackingQuality {
    init(_ state: ARCamera.TrackingState) {
        switch state {
        case .notAvailable: self = .unavailable
        case .limited:      self = .limited
        case .normal:       self = .normal
        @unknown default:   self = .unknown
        }
    }
}

extension ARRaycastQuery.TargetAlignment {
    /// Map a raycast target alignment back to a plane alignment for the
    /// height heuristic. `.any` resolves as horizontal (the common case).
    var planeAlignment: ARPlaneAnchor.Alignment {
        switch self {
        case .vertical: return .vertical
        case .horizontal, .any: return .horizontal
        @unknown default: return .horizontal
        }
    }
}

extension ARFrame.WorldMappingStatus {
    var spatailDescription: String {
        switch self {
        case .notAvailable: return "notAvailable"
        case .limited:      return "limited"
        case .extending:    return "extending"
        case .mapped:       return "mapped"
        @unknown default:   return "unknown"
        }
    }
}

extension SIMD4 where Scalar == Float {
    /// xyz of a homogeneous column.
    var xyz: SIMD3<Float> { SIMD3(x, y, z) }
}
