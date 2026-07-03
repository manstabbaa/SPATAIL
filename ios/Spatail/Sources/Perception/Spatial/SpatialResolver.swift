// SpatialResolver.swift — THE 2D → 3D BOUNDARY. "ARKit resolves WHERE it exists."
//
// Upgraded from the legacy single-point raycast resolver to the LIVE_BRAIN_SPEC §3
// measurement path: a detection box becomes a cloud of depth-grid world points
// (DepthSampler), the cloud becomes an ORIENTED BOUNDING BOX (XZ min-area rectangle
// about gravity-aligned +Y — rotating-calipers-lite over the convex hull of the XZ
// projections — plus the Y extent), and the OBB is linked to its support surface
// (the RoomSurface whose top plane sits within 4 cm below the OBB's bottom face and
// overlaps it in XZ). Output is Core's `ResolvedDetection`.
//
// LiDAR-less fallback: when the frame carries no sceneDepth, the same grid is resolved
// by RAYCASTING per tap (existing plane geometry, then estimated planes), with the same
// ±25 % median filter applied to camera-distance. Raycasts need the ARView → this path
// is main-actor; the depth path is pure math.

import Foundation
import ARKit
import RealityKit
import CoreGraphics
import UIKit
import simd

// MARK: - Resolver

@MainActor
struct SpatialResolver {

    /// How a resolution was obtained — surfaced by the Truth Overlay.
    enum Method: String {
        case depthGrid = "depth-grid"        // LiDAR sceneDepth, 5×5 grid
        case raycastGrid = "raycast-grid"    // no LiDAR: per-tap raycasts
    }

    struct Outcome {
        var resolved: ResolvedDetection
        var method: Method
    }

    /// Grid granularity (spec §3: 5×5).
    static let gridSize = 5

    /// Resolve one 2D detection into a measured 3D form against a specific frame.
    /// `arView` powers the raycast fallback; pass nil to run depth-only.
    func resolve(_ detection: Detection2D,
                 frame: ARFrame,
                 arView: ARView?,
                 interfaceOrientation: UIInterfaceOrientation,
                 surfaces: [RoomSurface]) -> Outcome? {
        // 1. Preferred: LiDAR depth grid (works on any surface, planar or not).
        if let grid = DepthSampler.sampleGrid(in: detection.box, frame: frame,
                                              gridSize: Self.gridSize) {
            return outcome(from: grid, detection: detection, surfaces: surfaces,
                           method: .depthGrid)
        }

        // 2. Fallback: raycast per tap (non-LiDAR devices).
        if let arView,
           let grid = raycastGrid(in: detection.box, frame: frame, arView: arView,
                                  interfaceOrientation: interfaceOrientation) {
            return outcome(from: grid, detection: detection, surfaces: surfaces,
                           method: .raycastGrid)
        }
        return nil
    }

    /// Depth-resolve an arbitrary normalized sensor-space box (used for VLM part
    /// boxes) into a world OBB — no detection wrapper, no surface link.
    func resolveBox(_ box: CGRect,
                    frame: ARFrame,
                    arView: ARView?,
                    interfaceOrientation: UIInterfaceOrientation) -> OrientedBox? {
        if let grid = DepthSampler.sampleGrid(in: box, frame: frame,
                                              gridSize: Self.gridSize) {
            return OrientedBoxFitter.fit(points: grid.points)
        }
        if let arView,
           let grid = raycastGrid(in: box, frame: frame, arView: arView,
                                  interfaceOrientation: interfaceOrientation) {
            return OrientedBoxFitter.fit(points: grid.points)
        }
        return nil
    }

    // MARK: internals

    private func outcome(from grid: DepthSampler.GridSample,
                         detection: Detection2D,
                         surfaces: [RoomSurface],
                         method: Method) -> Outcome? {
        guard let obb = OrientedBoxFitter.fit(points: grid.points) else { return nil }
        let supportId = SupportSurfaceLinker.link(obb: obb, surfaces: surfaces)
        let resolved = ResolvedDetection(detection: detection,
                                         obb: obb,
                                         supportSurfaceId: supportId,
                                         tapCoverage: grid.tapCoverage)
        return Outcome(resolved: resolved, method: method)
    }

    /// No-LiDAR path: raycast through each grid tap; filter outliers by camera
    /// distance with the same ±25 % median rule.
    private func raycastGrid(in box: CGRect,
                             frame: ARFrame,
                             arView: ARView,
                             interfaceOrientation: UIInterfaceOrientation) -> DepthSampler.GridSample? {
        let viewSize = arView.bounds.size
        guard viewSize.width > 0, viewSize.height > 0 else { return nil }
        // Sensor-image normalized coords → normalized view coords → view points.
        let display = frame.displayTransform(for: interfaceOrientation,
                                             viewportSize: viewSize)
        let camPos = frame.camera.transform.columns.3
        let cameraPosition = SIMD3<Float>(camPos.x, camPos.y, camPos.z)
        let n = Self.gridSize

        var hits: [(point: SIMD3<Float>, distance: Float)] = []
        for gy in 0..<n {
            for gx in 0..<n {
                let nx = box.minX + box.width * (CGFloat(gx) + 0.5) / CGFloat(n)
                let ny = box.minY + box.height * (CGFloat(gy) + 0.5) / CGFloat(n)
                let viewNorm = CGPoint(x: nx, y: ny).applying(display)
                guard (0...1).contains(viewNorm.x), (0...1).contains(viewNorm.y)
                else { continue }
                let screenPoint = CGPoint(x: viewNorm.x * viewSize.width,
                                          y: viewNorm.y * viewSize.height)
                // Real geometry first, then estimated planes.
                let result = arView.raycast(from: screenPoint,
                                            allowing: .existingPlaneGeometry,
                                            alignment: .any).first
                    ?? arView.raycast(from: screenPoint,
                                      allowing: .estimatedPlane,
                                      alignment: .any).first
                guard let hit = result else { continue }
                let c = hit.worldTransform.columns.3
                let p = SIMD3<Float>(c.x, c.y, c.z)
                hits.append((p, simd_distance(p, cameraPosition)))
            }
        }
        guard hits.count >= 3 else { return nil }

        let sorted = hits.map(\.distance).sorted()
        let median = sorted[sorted.count / 2]
        let survivors = hits.filter { abs($0.distance - median) <= 0.25 * median }
        guard survivors.count >= 3 else { return nil }

        return DepthSampler.GridSample(points: survivors.map(\.point),
                                       medianDepth: median,
                                       tapCoverage: Float(survivors.count) / Float(n * n))
    }
}

