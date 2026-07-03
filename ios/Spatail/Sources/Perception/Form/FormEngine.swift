// FormEngine.swift — Perception v2, the Form Engine orchestrator. Owns all four
// stages OFF-MAIN (spec §0 law 2), behind a single-flight latch (drop-on-busy):
//
//   stage 1  MaskProvider (Vision instance mask, once per pass) → per-detection
//            eroded mask at depth resolution → DepthSampler.sampleMasked
//            (.high-confidence pixels only).
//   stage 2  FormPointCloud per registry object — voxel-fused, hard-capped,
//            arc-coverage-tracked accumulation across frames.
//   stage 3  FormFitter — class-conditioned parametric fit of the fused cloud.
//   stage 4  depth-collapse detection → class-prior form, silhouette-scaled and
//            seated on the support surface. HONEST: every result carries
//            .measured or .prior end to end.
//
// Clock honesty: mask latency is measured every pass; two consecutive passes over
// budget degrade the engine to the box-mask (old grid region) path for 30 s, then
// it probes again — the 1–2 Hz perception clock never silently stretches.
//
// Threading: `submit` runs on MAIN (cheap capture of buffer references + camera
// numbers), everything else on a dedicated serial queue via InferenceExecutor
// (latched BEFORE dispatch). The registry only ever receives IMMUTABLE
// FittedFormResult values, delivered on main.

import Foundation
import CoreVideo
import CoreGraphics
import ImageIO
import Vision
import simd

// MARK: - Immutable fitted result (the only thing that crosses back to main)

struct FittedFormResult {
    let objectId: UUID
    let form: ObjectForm
    /// Compatibility OBB derived from the profile/prior; nil = keep the existing OBB.
    let obb: OrientedBox?
    /// Measured cap sub-cylinder (revolution profile step), when found.
    let capRegion: OrientedBox?
    /// Frame timestamp (uptime clock) the fit landed on.
    let fittedAt: TimeInterval
}

// MARK: - Engine

final class FormEngine {

    // MARK: Tunables

    /// Mask latency budget (ms). VNGenerateForegroundInstanceMaskRequest is heavier
    /// than the old grid — past this the 1–2 Hz clock is at risk.
    static let maskLatencyBudgetMillis: Double = 400
    /// Consecutive over-budget passes before degrading.
    static let maskLatencyStrikes = 2
    /// How long a degraded engine waits before probing the mask path again.
    static let degradedRetrySeconds: TimeInterval = 30
    /// Clouds for objects unseen this long are dropped (bounded memory).
    static let cloudIdleExpirySeconds: TimeInterval = 20
    /// Max detections fitted per pass (bounded work; largest boxes first).
    static let maxCandidatesPerPass = 5
    /// Max masked sample points fed to the cloud per detection per frame.
    static let maxSamplesPerDetection = 700

    // MARK: Inputs (built on MAIN, consumed on the form queue)

    /// Support-surface observation under a detection (raycast on main — ARView).
    struct SupportObservation {
        /// World-space hit point on the horizontal surface under the silhouette.
        let point: SIMD3<Float>
        /// Pinhole z-depth of that point (m) — scales the RGB silhouette.
        let zDepth: Float
    }

    struct Candidate {
        let objectId: UUID
        /// Detection box, normalized sensor space (the pipeline's canonical 2D).
        let sensorBox: CGRect
        /// Best known class label (registry identity first, detector label second).
        let classLabel: String?
        /// This frame's grid-path center estimate (cloud jump detection).
        let measurementCenter: SIMD3<Float>
        let support: SupportObservation?
    }

    /// Everything one pass needs, captured on main. Pixel buffers are retained
    /// references (single-flight ⇒ at most one frame's buffers held off-main).
    struct Pass {
        let capturedImage: CVPixelBuffer
        let depthMap: CVPixelBuffer
        let confidenceMap: CVPixelBuffer?
        let intrinsics: simd_float3x3
        let imageResolution: CGSize
        let cameraTransform: simd_float4x4
        let cameraPosition: SIMD3<Float>
        let orientation: CGImagePropertyOrientation
        let timestamp: TimeInterval
        let candidates: [Candidate]
        /// Live registry ids — clouds outside this set are pruned.
        let liveObjectIds: Set<UUID>
    }

    // MARK: Output

    struct Publication {
        let results: [FittedFormResult]
        /// Wall-clock cost of the Vision mask request this pass (nil = skipped).
        let maskMillis: Double?
        let degraded: Bool
        /// Terse per-candidate truth lines for the debug model.
        let notes: [String]
        let timestamp: TimeInterval
    }

    /// Delivered on MAIN once per completed pass. Set once at wiring time.
    var onResults: ((Publication) -> Void)?

    // MARK: State (queue-confined unless noted)

    private let executor = InferenceExecutor(label: "dev.spatail.perception.form")
    private let maskProvider = MaskProvider()
    private var clouds: [UUID: FormPointCloud] = [:]
    private var overBudgetStrikes = 0
    private var degradedUntil: TimeInterval = 0

    // MARK: Submit (main; latch before dispatch, drop-on-busy)

    /// Returns false when the previous pass is still in flight — the frame is
    /// dropped (latest-wins) and the clouds simply accumulate one frame later.
    @discardableResult
    func submit(_ pass: Pass) -> Bool {
        guard !pass.candidates.isEmpty else { return false }
        return executor.submit { [weak self] in
            guard let self else { return }
            let publication = self.run(pass)
            DispatchQueue.main.async { self.onResults?(publication) }
        }
    }

    // MARK: The pass (form queue)

    private func run(_ pass: Pass) -> Publication {
        pruneClouds(live: pass.liveObjectIds, now: pass.timestamp)

        var notes: [String] = []

        // ── Stage 1: one Vision instance-mask pass (unless degraded) ──
        var degraded = pass.timestamp < degradedUntil
        var observation: VNInstanceMaskObservation?
        var maskMillis: Double?
        if !degraded {
            let started = Date()
            let obs = try? maskProvider.instanceObservation(
                pixelBuffer: pass.capturedImage, orientation: pass.orientation)
            let elapsed = Date().timeIntervalSince(started) * 1000
            maskMillis = elapsed
            observation = obs.flatMap { $0 }
            if elapsed > Self.maskLatencyBudgetMillis {
                overBudgetStrikes += 1
                if overBudgetStrikes >= Self.maskLatencyStrikes {
                    degradedUntil = pass.timestamp + Self.degradedRetrySeconds
                    overBudgetStrikes = 0
                    notes.append(String(format: "mask %.0f ms > %.0f budget — " +
                                        "degrading to grid masks for %.0f s",
                                        elapsed, Self.maskLatencyBudgetMillis,
                                        Self.degradedRetrySeconds))
                }
            } else {
                overBudgetStrikes = 0
            }
        } else {
            notes.append("degraded (mask over budget) — box masks")
        }
        if !degraded, observation == nil, maskMillis != nil {
            notes.append("no foreground instances — box masks this pass")
        }
        degraded = degraded || observation == nil

        let dw = CVPixelBufferGetWidth(pass.depthMap)
        let dh = CVPixelBufferGetHeight(pass.depthMap)
        let ordered = pass.candidates
            .sorted { $0.sensorBox.width * $0.sensorBox.height
                    > $1.sensorBox.width * $1.sensorBox.height }
            .prefix(Self.maxCandidatesPerPass)

        var results: [FittedFormResult] = []
        for candidate in ordered {
            guard min(candidate.sensorBox.width, candidate.sensorBox.height) > 0.02
            else { continue }

            // Per-detection mask: instance mask when available, box mask otherwise.
            var mask: MaskProvider.BinaryMask?
            if let observation {
                mask = maskProvider.mask(for: candidate.sensorBox,
                                         observation: observation,
                                         orientation: pass.orientation,
                                         depthWidth: dw, depthHeight: dh)
            }
            let usedInstanceMask = mask != nil
            if mask == nil {
                mask = MaskProvider.boxMask(for: candidate.sensorBox,
                                            depthWidth: dw, depthHeight: dh)
            }
            guard let mask else { continue }

            guard let sample = DepthSampler.sampleMasked(
                mask: mask, depthMap: pass.depthMap,
                confidenceMap: pass.confidenceMap,
                intrinsics: pass.intrinsics,
                imageResolution: pass.imageResolution,
                cameraTransform: pass.cameraTransform,
                maxPoints: Self.maxSamplesPerDetection) else { continue }

            let label = candidate.classLabel ?? "object"

            // ── Stage 4 gate: is the depth under this detection usable? ──
            let expectedSize = Self.expectedMetricSize(box: candidate.sensorBox,
                                                       medianDepth: sample.medianDepth,
                                                       intrinsics: pass.intrinsics,
                                                       imageResolution: pass.imageResolution)
            let collapsed = FormFitter.depthCollapsed(
                highConfFraction: sample.highConfFraction,
                validCount: sample.highConfCount,
                pointSpread: sample.pointSpread,
                expectedSize: expectedSize)

            if collapsed {
                // PRIOR path: dimensions from the class table, scaled by the RGB
                // silhouette when the support raycast gave a range, seated on the
                // support surface. Garbage points NEVER enter the cloud.
                let silhouette = Self.silhouetteHeightMeters(
                    mask: mask, support: candidate.support, pass: pass)
                let arc = clouds[candidate.objectId]?.arcCoverage ?? 0
                if let fit = FormFitter.priorFit(classLabel: candidate.classLabel,
                                                 silhouetteHeightMeters: silhouette,
                                                 seatPoint: candidate.support?.point,
                                                 arcCoverage: arc) {
                    results.append(FittedFormResult(objectId: candidate.objectId,
                                                    form: fit.form, obb: fit.obb,
                                                    capRegion: nil,
                                                    fittedAt: pass.timestamp))
                    notes.append(String(format: "%@: depth collapsed (%.0f%% high-conf)" +
                                        " → PRIOR%@", label,
                                        sample.highConfFraction * 100,
                                        silhouette != nil ? " (silhouette-scaled)" : ""))
                } else {
                    notes.append(String(format: "%@: depth collapsed, no class prior" +
                                        " — unmeasurable this pass", label))
                }
                continue
            }

            // ── Stage 2: accumulate into the object's fused cloud ──
            let cloud = clouds[candidate.objectId] ?? {
                let c = FormPointCloud()
                clouds[candidate.objectId] = c
                return c
            }()
            cloud.insert(points: sample.points, viewpoint: pass.cameraPosition,
                         measurementCenter: candidate.measurementCenter,
                         now: pass.timestamp)

            // ── Stage 3: class-conditioned parametric fit ──
            if let fit = FormFitter.fit(points: cloud.snapshot,
                                        classLabel: candidate.classLabel,
                                        arcCoverage: cloud.arcCoverage) {
                results.append(FittedFormResult(objectId: candidate.objectId,
                                                form: fit.form, obb: fit.obb,
                                                capRegion: fit.capRegion,
                                                fittedAt: pass.timestamp))
                notes.append(String(format: "%@: %@ fit, %d pts, %.0f%% arc, " +
                                    "res %.1f mm%@%@", label,
                                    fit.form.kind.rawValue, cloud.count,
                                    cloud.arcCoverage * 100,
                                    fit.form.residual * 1000,
                                    fit.capRegion != nil ? ", cap found" : "",
                                    usedInstanceMask ? "" : " (box mask)"))
            } else {
                notes.append(String(format: "%@: accumulating (%d pts)", label,
                                    cloud.count))
            }
        }

        return Publication(results: results, maskMillis: maskMillis,
                           degraded: degraded, notes: notes,
                           timestamp: pass.timestamp)
    }

    // MARK: Cloud lifecycle (bounded)

    private func pruneClouds(live: Set<UUID>, now: TimeInterval) {
        clouds = clouds.filter { id, cloud in
            live.contains(id) && now - cloud.lastTouchedAt < Self.cloudIdleExpirySeconds
        }
    }

    // MARK: Geometry helpers (queue side)

    /// Rough metric size of the box contents at the sampled depth — the yardstick
    /// for the "point spread absurd" collapse signal.
    private static func expectedMetricSize(box: CGRect, medianDepth: Float,
                                           intrinsics: simd_float3x3,
                                           imageResolution: CGSize) -> Float {
        guard medianDepth > 0 else { return 0 }
        let fx = intrinsics.columns.0.x, fy = intrinsics.columns.1.y
        guard fx > 1, fy > 1 else { return 0 }
        let wMeters = Float(box.width) * Float(imageResolution.width) * medianDepth / fx
        let hMeters = Float(box.height) * Float(imageResolution.height) * medianDepth / fy
        return max(wMeters, hMeters)
    }

    /// RGB silhouette height in METRES: mask pixel extent along the image-projected
    /// world-up direction × depth-at-support ÷ focal length. nil without a support
    /// range (an unscaled prior is then used).
    private static func silhouetteHeightMeters(mask: MaskProvider.BinaryMask,
                                               support: SupportObservation?,
                                               pass: Pass) -> Float? {
        guard let support, !mask.isEmpty else { return nil }

        // Project world-up at the object into sensor-pixel space.
        let inverse = pass.cameraTransform.inverse
        func projectPx(_ world: SIMD3<Float>) -> SIMD2<Float>? {
            let cam = inverse * SIMD4<Float>(world.x, world.y, world.z, 1)
            guard cam.z < -0.01 else { return nil }
            let d = -cam.z
            let fx = pass.intrinsics.columns.0.x, fy = pass.intrinsics.columns.1.y
            let cx = pass.intrinsics.columns.2.x, cy = pass.intrinsics.columns.2.y
            // Inverse of the sampler's unproject: camera space (x, -y, -d).
            return SIMD2<Float>(cx + fx * cam.x / d, cy - fy * cam.y / d)
        }
        guard let p0 = projectPx(support.point),
              let p1 = projectPx(support.point + SIMD3<Float>(0, 0.2, 0))
        else { return nil }
        var upDir = p1 - p0
        let len = simd_length(upDir)
        guard len > 1 else { return nil }       // camera looking along gravity
        upDir /= len

        // Mask pixel extent (captured-resolution px) projected on the up direction.
        let sx = Float(pass.imageResolution.width) / Float(mask.width)
        let sy = Float(pass.imageResolution.height) / Float(mask.height)
        var minS = Float.greatestFiniteMagnitude
        var maxS = -Float.greatestFiniteMagnitude
        for y in mask.minY...mask.maxY {
            for x in mask.minX...mask.maxX where mask.bits[y * mask.width + x] {
                let px = SIMD2<Float>((Float(x) + 0.5) * sx, (Float(y) + 0.5) * sy)
                let s = simd_dot(px - p0, upDir)
                minS = min(minS, s)
                maxS = max(maxS, s)
            }
        }
        guard maxS > minS else { return nil }
        let fx = pass.intrinsics.columns.0.x, fy = pass.intrinsics.columns.1.y
        let f = (fx + fy) / 2
        guard f > 1 else { return nil }
        let height = (maxS - minS) * support.zDepth / f
        return height > 0.01 ? height : nil
    }
}
