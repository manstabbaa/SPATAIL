// DepthSampler.swift — LiDAR depth → world points. Two sampling modes:
//
//   • GRID (LIVE_BRAIN_SPEC §3): "for each detection box, depth-sample a 5×5 grid
//     inside the box; reject taps deviating > ±25 % from the median depth" — the
//     fast per-tick path that keeps the registry's OBBs live.
//   • MASKED (Perception v2 Form Engine, stage 1): sample the smoothed scene depth
//     ONLY under an eroded instance mask, keep ONLY `.high`-confidence pixels
//     (ARFrame sceneDepth confidenceMap), unproject with the camera intrinsics —
//     the dense, honest points the multi-view fusion cloud accumulates.
//
// Input boxes/masks are in the pipeline's canonical 2D space — the CAPTURED image,
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

    // MARK: - Masked sampling (Form Engine stage 1)

    /// One masked sampling pass — the Form Engine's per-detection measurement.
    struct MaskedSample {
        /// World-space points: in-mask, `.high` confidence, median-filtered,
        /// stride-subsampled to `maxPoints`.
        var points: [SIMD3<Float>]
        /// (# in-mask pixels with `.high` depth confidence) / (# in-mask pixels) —
        /// THE transparency signal (clear plastic collapses this).
        var highConfFraction: Float
        /// In-mask pixels carrying `.high` confidence AND a finite in-range depth.
        var highConfCount: Int
        /// In-mask pixels with a finite in-range depth (any confidence).
        var validCount: Int
        /// Median depth (m) of the pixels that fed `points`.
        var medianDepth: Float
        /// Largest AABB extent of the surviving world points (m) — the "point
        /// spread absurd vs box size" collapse signal.
        var pointSpread: Float
    }

    /// Sample depth ONLY under an eroded binary mask (depth-map resolution),
    /// keeping ONLY `.high`-confidence pixels when a confidence map is present.
    /// Returns a sample even when few/no points survive — the caller needs the
    /// confidence statistics to detect depth collapse. nil = unusable inputs.
    static func sampleMasked(mask: MaskProvider.BinaryMask,
                             depthMap: CVPixelBuffer,
                             confidenceMap: CVPixelBuffer?,
                             intrinsics: simd_float3x3,
                             imageResolution: CGSize,
                             cameraTransform: simd_float4x4,
                             maxPoints: Int = 700) -> MaskedSample? {
        guard !mask.isEmpty,
              CVPixelBufferGetPixelFormatType(depthMap) == kCVPixelFormatType_DepthFloat32
        else { return nil }
        let dw = CVPixelBufferGetWidth(depthMap)
        let dh = CVPixelBufferGetHeight(depthMap)
        guard dw == mask.width, dh == mask.height else { return nil }

        CVPixelBufferLockBaseAddress(depthMap, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(depthMap, .readOnly) }
        guard let depthBase = CVPixelBufferGetBaseAddress(depthMap) else { return nil }
        let depthRow = CVPixelBufferGetBytesPerRow(depthMap)

        var confBase: UnsafeMutableRawPointer?
        var confRow = 0
        if let confidenceMap,
           CVPixelBufferGetWidth(confidenceMap) == dw,
           CVPixelBufferGetHeight(confidenceMap) == dh {
            CVPixelBufferLockBaseAddress(confidenceMap, .readOnly)
            confBase = CVPixelBufferGetBaseAddress(confidenceMap)
            confRow = CVPixelBufferGetBytesPerRow(confidenceMap)
        }
        defer {
            if let confidenceMap, confBase != nil {
                CVPixelBufferUnlockBaseAddress(confidenceMap, .readOnly)
            }
        }

        // ── Collect candidate pixels: in-mask, valid depth, high confidence ──
        var candidates: [(x: Int, y: Int, depth: Float)] = []
        var validCount = 0
        var highConfCount = 0
        for y in mask.minY...mask.maxY {
            let dRow = depthBase.advanced(by: y * depthRow)
                .assumingMemoryBound(to: Float32.self)
            let cRow = confBase?.advanced(by: y * confRow)
                .assumingMemoryBound(to: UInt8.self)
            for x in mask.minX...mask.maxX where mask.bits[y * mask.width + x] {
                let d = dRow[x]
                guard d.isFinite, depthRange.contains(d) else { continue }
                validCount += 1
                // ARConfidenceLevel.high == 2; no confidence map → trust valid depth.
                let high = cRow.map { $0[x] >= 2 } ?? true
                guard high else { continue }
                highConfCount += 1
                candidates.append((x, y, d))
            }
        }
        let highConfFraction = mask.setCount > 0
            ? Float(highConfCount) / Float(mask.setCount) : 0

        guard candidates.count >= minSurvivors else {
            return MaskedSample(points: [], highConfFraction: highConfFraction,
                                highConfCount: highConfCount, validCount: validCount,
                                medianDepth: 0, pointSpread: 0)
        }

        // ── ±25 % median filter (residual silhouette bleed the erosion missed) ──
        let sorted = candidates.map(\.depth).sorted()
        let median = sorted[sorted.count / 2]
        var survivors = candidates.filter {
            abs($0.depth - median) <= medianRejectFraction * median
        }

        // ── Stride-subsample: bounded work per frame (law 3) ──
        if survivors.count > maxPoints {
            let stride = Float(survivors.count) / Float(maxPoints)
            var picked: [(x: Int, y: Int, depth: Float)] = []
            picked.reserveCapacity(maxPoints)
            var cursor: Float = 0
            while Int(cursor) < survivors.count, picked.count < maxPoints {
                picked.append(survivors[Int(cursor)])
                cursor += stride
            }
            survivors = picked
        }

        // ── Unproject with intrinsics scaled to depth resolution ──
        // Intrinsics are for the capture resolution; depth aligns with the captured
        // image, so normalized coordinates transfer directly (same as the grid path).
        let fx = intrinsics.columns.0.x, fy = intrinsics.columns.1.y
        let cx = intrinsics.columns.2.x, cy = intrinsics.columns.2.y
        let capW = Float(imageResolution.width), capH = Float(imageResolution.height)

        var points: [SIMD3<Float>] = []
        points.reserveCapacity(survivors.count)
        var lo = SIMD3<Float>(repeating: .greatestFiniteMagnitude)
        var hi = SIMD3<Float>(repeating: -.greatestFiniteMagnitude)
        for s in survivors {
            let px = (Float(s.x) + 0.5) / Float(dw) * capW
            let py = (Float(s.y) + 0.5) / Float(dh) * capH
            let x = (px - cx) * s.depth / fx
            let y = (py - cy) * s.depth / fy
            let world4 = cameraTransform * SIMD4<Float>(x, -y, -s.depth, 1)
            let world = SIMD3<Float>(world4.x, world4.y, world4.z)
            points.append(world)
            lo = simd_min(lo, world)
            hi = simd_max(hi, world)
        }
        let spread = points.isEmpty ? 0 : simd_reduce_max(hi - lo)

        return MaskedSample(points: points, highConfFraction: highConfFraction,
                            highConfCount: highConfCount, validCount: validCount,
                            medianDepth: median, pointSpread: spread)
    }
}
