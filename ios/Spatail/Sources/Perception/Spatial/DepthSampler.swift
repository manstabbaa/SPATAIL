// DepthSampler.swift — LiDAR depth → world points, upgraded from the legacy
// center-pixel tap to the GRID sampling LIVE_BRAIN_SPEC §3 mandates:
//
//   "for each detection box, depth-sample a 5×5 grid inside the box; reject taps
//    deviating > ±25 % from the median depth (background)"
//
// Input boxes are in the pipeline's canonical 2D space — the CAPTURED image,
// normalized [0–1], origin top-left, sensor-native orientation — which is exactly the
// space the sceneDepth map and the camera intrinsics live in, so taps index the depth
// buffer directly (no display transforms here).
//
// Pure CVPixelBuffer reads + simd math: safe to call from any thread. Returns nil on
// devices without sceneDepth (the resolver falls back to per-tap raycasting) and on
// boxes with fewer than `minSurvivors` valid taps.

import Foundation
import ARKit
import CoreVideo
import CoreGraphics
import simd

enum DepthSampler {

    /// The surviving foreground taps of one grid pass.
    struct GridSample {
        /// World-space positions of the taps that survived the median filter.
        var points: [SIMD3<Float>]
        /// Median depth (metres) of the valid taps — the foreground estimate.
        var medianDepth: Float
        /// survivors / taps-attempted (25 for a 5×5 grid) — the quality signal that
        /// flows onto ResolvedDetection.tapCoverage.
        var tapCoverage: Float
    }

    /// Depth range accepted from the sensor (metres).
    private static let depthRange: ClosedRange<Float> = 0.05...8.0
    /// Taps deviating more than this fraction from the median depth are background.
    private static let medianRejectFraction: Float = 0.25
    /// Fewer surviving taps than this → the box is not resolvable this frame.
    private static let minSurvivors = 3

    /// Sample a `gridSize`×`gridSize` grid of depth taps inside a normalized
    /// sensor-space box and unproject the foreground survivors into world space.
    static func sampleGrid(in box: CGRect,
                           frame: ARFrame,
                           gridSize: Int = 5) -> GridSample? {
        guard gridSize > 0, box.width > 0, box.height > 0,
              let sceneDepth = frame.sceneDepth ?? frame.smoothedSceneDepth
        else { return nil }
        let depthMap = sceneDepth.depthMap

        let dw = CVPixelBufferGetWidth(depthMap)
        let dh = CVPixelBufferGetHeight(depthMap)
        guard dw > 0, dh > 0,
              CVPixelBufferGetPixelFormatType(depthMap) == kCVPixelFormatType_DepthFloat32
        else { return nil }

        CVPixelBufferLockBaseAddress(depthMap, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(depthMap, .readOnly) }
        guard let base = CVPixelBufferGetBaseAddress(depthMap) else { return nil }
        let rowBytes = CVPixelBufferGetBytesPerRow(depthMap)

        func depthAt(_ nx: CGFloat, _ ny: CGFloat) -> Float? {
            guard (0...1).contains(nx), (0...1).contains(ny) else { return nil }
            let col = min(dw - 1, max(0, Int(nx * CGFloat(dw))))
            let row = min(dh - 1, max(0, Int(ny * CGFloat(dh))))
            let ptr = base.advanced(by: row * rowBytes + col * MemoryLayout<Float32>.size)
                .assumingMemoryBound(to: Float32.self)
            let d = ptr.pointee
            return (d.isFinite && depthRange.contains(d)) ? d : nil
        }

        // ── Tap the grid at cell centers (strictly inside the box) ──
        var taps: [(nx: CGFloat, ny: CGFloat, depth: Float)] = []
        let attempted = gridSize * gridSize
        for gy in 0..<gridSize {
            for gx in 0..<gridSize {
                let nx = box.minX + box.width * (CGFloat(gx) + 0.5) / CGFloat(gridSize)
                let ny = box.minY + box.height * (CGFloat(gy) + 0.5) / CGFloat(gridSize)
                if let d = depthAt(nx, ny) { taps.append((nx, ny, d)) }
            }
        }
        guard taps.count >= minSurvivors else { return nil }

        // ── Median-depth foreground filter (spec §3) ──
        let sorted = taps.map(\.depth).sorted()
        let median = sorted[sorted.count / 2]
        let survivors = taps.filter { abs($0.depth - median) <= medianRejectFraction * median }
        guard survivors.count >= minSurvivors else { return nil }

        // ── Unproject survivors with the camera intrinsics ──
        // Intrinsics correspond to the capture resolution; the depth map is aligned to
        // the captured image, so normalized coords transfer directly.
        let intr = frame.camera.intrinsics
        let res = frame.camera.imageResolution
        let fx = intr.columns.0.x, fy = intr.columns.1.y
        let cx = intr.columns.2.x, cy = intr.columns.2.y
        let transform = frame.camera.transform

        // Camera space is +x right, +y up, -z forward; image y is down → flip.
        func unproject(_ nx: CGFloat, _ ny: CGFloat, _ depth: Float) -> SIMD3<Float> {
            let px = Float(nx) * Float(res.width)
            let py = Float(ny) * Float(res.height)
            let x = (px - cx) * depth / fx
            let y = (py - cy) * depth / fy
            let world = transform * SIMD4<Float>(x, -y, -depth, 1)
            return SIMD3(world.x, world.y, world.z)
        }

        let points = survivors.map { unproject($0.nx, $0.ny, $0.depth) }
        return GridSample(points: points,
                          medianDepth: median,
                          tapCoverage: Float(survivors.count) / Float(attempted))
    }
}
