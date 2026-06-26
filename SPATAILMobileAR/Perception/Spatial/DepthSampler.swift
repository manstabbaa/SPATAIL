// DepthSampler.swift
//
// Real LiDAR depth → world point. Samples ARKit's sceneDepth map at a
// detection's box center and unprojects it with the camera intrinsics, so a
// detection can be located on the actual surface it sits on — even when that
// surface is NOT a detected plane (an object, a part on a machine). This is
// the "recognition + reconstruction" path the Master File calls for (p.28).
//
// Degrades to nil on any device without LiDAR / any out-of-range sample, so
// the resolver simply falls back to raycasting.

import Foundation
import ARKit
import CoreVideo
import simd
import UIKit

@MainActor
enum DepthSampler {

    struct Sample {
        let worldPosition: SIMD3<Float>
        let depthMeters: Float
        /// Estimated surface normal (world space) from neighbouring taps, or
        /// nil when it could not be recovered reliably.
        let normal: SIMD3<Float>?
    }

    /// Sample depth at a normalized TOP-LEFT view point. Returns nil if no
    /// depth is available or the sample is invalid.
    static func sample(frame: ARFrame,
                       normalizedViewPoint: CGPoint,
                       viewportSize: CGSize,
                       interfaceOrientation: UIInterfaceOrientation) -> Sample? {
        guard viewportSize.width > 0, viewportSize.height > 0,
              let sceneDepth = frame.sceneDepth ?? frame.smoothedSceneDepth else { return nil }
        let depthMap = sceneDepth.depthMap

        // View → captured-image normalized coords (sensor orientation), which
        // is the space the intrinsics + depth map live in.
        let displayT = frame.displayTransform(for: interfaceOrientation, viewportSize: viewportSize)
        let imgNorm = normalizedViewPoint.applying(displayT.inverted())
        guard (0...1).contains(imgNorm.x), (0...1).contains(imgNorm.y) else { return nil }

        let dw = CVPixelBufferGetWidth(depthMap)
        let dh = CVPixelBufferGetHeight(depthMap)
        guard dw > 0, dh > 0,
              CVPixelBufferGetPixelFormatType(depthMap) == kCVPixelFormatType_DepthFloat32 else { return nil }

        CVPixelBufferLockBaseAddress(depthMap, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(depthMap, .readOnly) }
        guard let base = CVPixelBufferGetBaseAddress(depthMap) else { return nil }
        let rowBytes = CVPixelBufferGetBytesPerRow(depthMap)

        func depthAt(_ nx: CGFloat, _ ny: CGFloat) -> Float? {
            let col = min(dw - 1, max(0, Int(nx * CGFloat(dw))))
            let row = min(dh - 1, max(0, Int(ny * CGFloat(dh))))
            let ptr = base.advanced(by: row * rowBytes + col * MemoryLayout<Float32>.size)
                .assumingMemoryBound(to: Float32.self)
            let d = ptr.pointee
            return (d.isFinite && d > 0.05 && d < 8.0) ? d : nil
        }

        guard let centerDepth = depthAt(imgNorm.x, imgNorm.y) else { return nil }

        // Camera intrinsics correspond to the capture resolution.
        let intr = frame.camera.intrinsics
        let res = frame.camera.imageResolution
        let fx = intr.columns.0.x, fy = intr.columns.1.y
        let cx = intr.columns.2.x, cy = intr.columns.2.y
        let transform = frame.camera.transform

        // Unproject a normalized image point at a given depth into world space.
        // Camera space is +x right, +y up, -z forward; image y is down → flip.
        func unproject(_ nx: CGFloat, _ ny: CGFloat, _ depth: Float) -> SIMD3<Float> {
            let px = Float(nx) * Float(res.width)
            let py = Float(ny) * Float(res.height)
            let x = (px - cx) * depth / fx
            let y = (py - cy) * depth / fy
            let world = transform * SIMD4<Float>(x, -y, -depth, 1)
            return SIMD3(world.x, world.y, world.z)
        }

        let centerWorld = unproject(imgNorm.x, imgNorm.y, centerDepth)

        // Estimate the normal from a right + down neighbour tap (one lock).
        var normal: SIMD3<Float>?
        let step: CGFloat = 0.03
        if let dr = depthAt(min(1, imgNorm.x + step), imgNorm.y),
           let dd = depthAt(imgNorm.x, min(1, imgNorm.y + step)) {
            let right = unproject(min(1, imgNorm.x + step), imgNorm.y, dr) - centerWorld
            let down = unproject(imgNorm.x, min(1, imgNorm.y + step), dd) - centerWorld
            let n = simd_cross(down, right)
            if simd_length(n) > 1e-6 {
                var nn = simd_normalize(n)
                // Orient toward the camera.
                let camPos = SIMD3(transform.columns.3.x, transform.columns.3.y, transform.columns.3.z)
                if simd_dot(nn, camPos - centerWorld) < 0 { nn = -nn }
                normal = nn
            }
        }

        return Sample(worldPosition: centerWorld, depthMeters: centerDepth, normal: normal)
    }
}
