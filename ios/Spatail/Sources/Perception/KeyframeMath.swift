// KeyframeMath.swift — Perception v3 §6: the PURE math of timestamp-true binding.
//
// A keyframe is a frozen (pose, intrinsics, depth) triple. Everything that
// projects into or out of one lives here, Foundation + simd + CoreGraphics ONLY,
// so the exact shipped logic runs in the off-device harness:
//
//   • DepthGrid            — a copied, compact snapshot of one frame's sceneDepth
//     (Float16 — iPhone dToF carries ~mm-at-close-range precision; half floats
//     lose nothing we use) + optional confidence bytes.
//   • KeyframeGeometry     — world OBB → normalized sensor rect against a STORED
//     pose (the projector for late VLM results), and normalized sensor box →
//     world OBB against STORED depth+pose (part boxes resolve in the frame they
//     were seen in, not the frame the answer arrived in).
//   • MotionGate           — angular/linear camera velocity between two poses;
//     blurred frames mint ghosts and poison identity crops, so the detect tick
//     skips them (PERCEPTION_V3 §6 motion gate).
//
// Conventions match DepthSampler exactly: normalized [0–1] top-left SENSOR space
// indexes the depth grid directly; camera space is +x right +y up −z forward;
// unproject is world = T·(x, −y, −d, 1) with x=(px−cx)d/fx, y=(py−cy)d/fy — so
// the projection here is the exact inverse of the sampler's unprojection.

import Foundation
import CoreGraphics
import simd

// MARK: - DepthGrid

/// A compact copy of one frame's scene-depth (+ confidence) maps. Native LiDAR
/// resolution is 256×192 — ~98 KB as Float16 — cheap enough to keep a minute of.
struct DepthGrid {
    let width: Int
    let height: Int
    /// Row-major depths (m), sensor orientation. Non-finite/out-of-range stored
    /// as 0 (0 is below the sensor's minimum and reads as "no depth").
    let depths: [Float16]
    /// ARConfidenceLevel raw values (0/1/2), row-major. nil = no confidence map.
    let confidences: [UInt8]?

    /// Accepted metric depth range — mirrors DepthSampler.depthRange.
    static let depthRange: ClosedRange<Float> = 0.05...8.0

    /// Depth at a normalized top-left sensor-space point; nil out of range/grid.
    /// `minConfidence` gates on the stored confidence map when present (2 = high).
    func depth(nx: CGFloat, ny: CGFloat, minConfidence: UInt8 = 0) -> Float? {
        guard (0...1).contains(nx), (0...1).contains(ny), width > 0, height > 0
        else { return nil }
        let col = min(width - 1, max(0, Int(nx * CGFloat(width))))
        let row = min(height - 1, max(0, Int(ny * CGFloat(height))))
        if minConfidence > 0, let conf = confidences,
           conf[row * width + col] < minConfidence { return nil }
        let d = Float(depths[row * width + col])
        return Self.depthRange.contains(d) ? d : nil
    }
}

// MARK: - KeyframeGeometry

enum KeyframeGeometry {

    /// Taps deviating more than this fraction from the median depth are
    /// background — the same §3 foreground rule as DepthSampler.
    static let medianRejectFraction: Float = 0.25
    static let minSurvivingTaps = 3

    /// Project a world OBB into a stored frame's normalized sensor space
    /// (top-left origin) — the same contract as the live projector
    /// (LivePerceptionPipeline.project(obb:camera:)), computed from raw
    /// intrinsics so it runs against keyframes and in the harness.
    /// nil when the box is behind the camera or vanishingly small on-frame.
    static func projectRect(obb: OrientedBox,
                            cameraTransform: simd_float4x4,
                            intrinsics: simd_float3x3,
                            imageResolution: CGSize) -> CGRect? {
        let resW = Float(imageResolution.width), resH = Float(imageResolution.height)
        guard resW > 0, resH > 0 else { return nil }
        let fx = intrinsics.columns.0.x, fy = intrinsics.columns.1.y
        let cx = intrinsics.columns.2.x, cy = intrinsics.columns.2.y
        guard fx > 1, fy > 1 else { return nil }

        let hx = obb.extents.x / 2, hy = obb.extents.y / 2, hz = obb.extents.z / 2
        let c = cos(obb.yaw), s = sin(obb.yaw)
        let inverseView = cameraTransform.inverse

        var minX = Float.greatestFiniteMagnitude, minY = Float.greatestFiniteMagnitude
        var maxX = -Float.greatestFiniteMagnitude, maxY = -Float.greatestFiniteMagnitude
        var inFront = 0

        for sx in [Float(-1), 1] {
            for sy in [Float(-1), 1] {
                for sz in [Float(-1), 1] {
                    let lx = sx * hx, ly = sy * hy, lz = sz * hz
                    let world = SIMD3<Float>(obb.center.x + c * lx + s * lz,
                                             obb.center.y + ly,
                                             obb.center.z - s * lx + c * lz)
                    let cam = inverseView * SIMD4<Float>(world.x, world.y, world.z, 1)
                    guard cam.z < -0.01 else { continue }
                    inFront += 1
                    let d = -cam.z
                    // Inverse of DepthSampler's unproject: px = cx + fx·x/d, py = cy − fy·y/d.
                    let px = cx + fx * cam.x / d
                    let py = cy - fy * cam.y / d
                    minX = min(minX, px); maxX = max(maxX, px)
                    minY = min(minY, py); maxY = max(maxY, py)
                }
            }
        }
        guard inFront >= 4 else { return nil }   // most of the box must be in front

        let rect = CGRect(x: CGFloat(minX / resW), y: CGFloat(minY / resH),
                          width: CGFloat((maxX - minX) / resW),
                          height: CGFloat((maxY - minY) / resH))
            .intersection(CGRect(x: 0, y: 0, width: 1, height: 1))
        guard !rect.isNull, rect.width > 0.005, rect.height > 0.005 else { return nil }
        return rect
    }

    /// Resolve a normalized sensor-space box against a STORED frame: grid-tap the
    /// keyframe's depth, median-filter foreground (spec §3 rule), unproject with
    /// the keyframe's own intrinsics+pose, fit the yaw-only OBB. This is the
    /// timestamp-true replacement for resolving VLM part boxes "against the
    /// current frame" — the answer lands where it was SEEN.
    static func backprojectBox(_ box: CGRect,
                               depth: DepthGrid,
                               cameraTransform: simd_float4x4,
                               intrinsics: simd_float3x3,
                               imageResolution: CGSize,
                               gridSize: Int = 5) -> OrientedBox? {
        guard gridSize > 0, box.width > 0, box.height > 0 else { return nil }

        var taps: [(nx: CGFloat, ny: CGFloat, depth: Float)] = []
        for gy in 0..<gridSize {
            for gx in 0..<gridSize {
                let nx = box.minX + box.width * (CGFloat(gx) + 0.5) / CGFloat(gridSize)
                let ny = box.minY + box.height * (CGFloat(gy) + 0.5) / CGFloat(gridSize)
                if let d = depth.depth(nx: nx, ny: ny) { taps.append((nx, ny, d)) }
            }
        }
        guard taps.count >= minSurvivingTaps else { return nil }

        let sorted = taps.map(\.depth).sorted()
        let median = sorted[sorted.count / 2]
        let survivors = taps.filter { abs($0.depth - median) <= medianRejectFraction * median }
        guard survivors.count >= minSurvivingTaps else { return nil }

        let fx = intrinsics.columns.0.x, fy = intrinsics.columns.1.y
        let cx = intrinsics.columns.2.x, cy = intrinsics.columns.2.y
        guard fx > 1, fy > 1 else { return nil }
        let capW = Float(imageResolution.width), capH = Float(imageResolution.height)

        let points = survivors.map { tap -> SIMD3<Float> in
            let px = Float(tap.nx) * capW
            let py = Float(tap.ny) * capH
            let x = (px - cx) * tap.depth / fx
            let y = (py - cy) * tap.depth / fy
            let world = cameraTransform * SIMD4<Float>(x, -y, -tap.depth, 1)
            return SIMD3(world.x, world.y, world.z)
        }
        return OrientedBoxFitter.fit(points: points)
    }
}

// MARK: - MotionGate

/// Camera motion between two poses. ARKit's world-tracking transform is the
/// VIO-fused (gyro + accelerometer + camera) 6-DoF pose — strictly better than
/// raw gyro: it carries translation and doesn't drift on the timescales we gate.
enum MotionGate {

    /// Above these, the frame is motion-blurred at indoor exposure times:
    /// detection boxes smear, depth-RGB alignment lies, identity crops are mush.
    static let maxAngularVelocity: Float = 0.6    // rad/s
    static let maxLinearVelocity: Float = 0.8     // m/s

    struct Measure {
        let angularVelocity: Float   // rad/s
        let linearVelocity: Float    // m/s
        var blocked: Bool {
            angularVelocity > MotionGate.maxAngularVelocity
                || linearVelocity > MotionGate.maxLinearVelocity
        }
        /// 0 (still) … grows with motion — keyframe preference score.
        var score: Float {
            angularVelocity / MotionGate.maxAngularVelocity
                + linearVelocity / MotionGate.maxLinearVelocity
        }
    }

    /// Velocities between two camera transforms `dt` seconds apart.
    /// Angular velocity from the trace of the relative rotation (the standard
    /// axis-angle magnitude); degenerate dt → treated as still (no basis to block).
    static func measure(previous: simd_float4x4, current: simd_float4x4,
                        dt: TimeInterval) -> Measure {
        guard dt > 0.001 else {
            return Measure(angularVelocity: 0, linearVelocity: 0)
        }
        let dp = SIMD3<Float>(current.columns.3.x - previous.columns.3.x,
                              current.columns.3.y - previous.columns.3.y,
                              current.columns.3.z - previous.columns.3.z)
        let linear = simd_length(dp) / Float(dt)

        let rPrev = simd_float3x3(SIMD3(previous.columns.0.x, previous.columns.0.y, previous.columns.0.z),
                                  SIMD3(previous.columns.1.x, previous.columns.1.y, previous.columns.1.z),
                                  SIMD3(previous.columns.2.x, previous.columns.2.y, previous.columns.2.z))
        let rCurr = simd_float3x3(SIMD3(current.columns.0.x, current.columns.0.y, current.columns.0.z),
                                  SIMD3(current.columns.1.x, current.columns.1.y, current.columns.1.z),
                                  SIMD3(current.columns.2.x, current.columns.2.y, current.columns.2.z))
        let rel = rCurr * rPrev.transpose
        // trace = 1 + 2·cos(θ) for a rotation matrix; clamp for numeric safety.
        let trace = rel.columns.0.x + rel.columns.1.y + rel.columns.2.z
        let cosTheta = max(-1, min(1, (trace - 1) / 2))
        let angular = acos(cosTheta) / Float(dt)

        return Measure(angularVelocity: angular, linearVelocity: linear)
    }
}
