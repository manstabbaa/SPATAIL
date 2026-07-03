// FormFitter.swift — Perception v2 Form Engine, stage 3: class-conditioned
// parametric fitting. PURE MATH (Foundation + simd + Core types only — no ARKit,
// no Vision) so every branch runs identically in the off-device harness.
//
//   (a) revolution classes (bottle, can, cup, mug, jar, glass, vase, candle) →
//       surface-of-revolution fit about the gravity axis:
//         • axis: Kåsa least-squares circle fit on the XZ projection of the
//           mid-body points (robust to one-sided observation, where the centroid
//           of a partial shell is biased toward the camera);
//         • profile: bin fused points by height (5 mm bins); per-bin radius =
//           median of point radii about the axis with MAD outlier rejection;
//         • CAP: a sustained radius drop ≥ 25 % vs. the body median, in the top
//           quarter of the profile → a measured sub-cylinder "cap" region. This
//           REPLACES the §3 top-20%-slice heuristic when present.
//   (b) boxy/unknown classes → oriented-box fit of the fused cloud (yaw-only about
//       gravity, via the existing OrientedBoxFitter), with a robust trim first.
//
//   Also home of the PURE halves of stage 4: the depth-collapse decision and the
//   class-prior fit (dimensions from FormPriors, silhouette-scaled, seated on the
//   support surface) — so the transparent-bottle path is harness-testable too.

import Foundation
import simd

enum FormFitter {

    // MARK: Tunables (guidance values — tune against calipers, keep the shape)

    /// Height bin for the radius-vs-height profile (m).
    static let heightBinSize: Float = 0.005
    /// Fewer fused points than this → no revolution fit yet (keep accumulating).
    static let minRevolutionPoints = 120
    /// Fewer fused points than this → no box fit yet.
    static let minBoxPoints = 40
    /// Minimum points for a height bin to yield a radius.
    static let minBinPoints = 3
    /// The cap is a SUSTAINED radius drop of at least this fraction vs. body median.
    static let capDropFraction: Float = 0.25
    /// Fraction of bins above the step that must comply for "sustained".
    static let capSustainFraction: Float = 0.8
    /// A cap needs at least this many profile bins (2 × 5 mm = 1 cm).
    static let capMinBins = 2
    /// Revolution fit accepted only under this RMS radial residual (m); above it
    /// the object is not a revolution solid — fall back to the box fit.
    static let maxRevolutionResidual: Float = 0.012
    /// Dimension sanity gates for accepting a revolution fit (m).
    static let heightSanity: ClosedRange<Float> = 0.03...0.6
    static let diameterSanity: ClosedRange<Float> = 0.02...0.35

    // MARK: Output

    /// One immutable fitted result. `obb` is the compatibility OrientedBox derived
    /// from the profile/prior (nil only for an unseatable prior); `capRegion` is the
    /// measured cap sub-cylinder (as an OBB) when the profile found the step.
    struct Fit {
        var form: ObjectForm
        var obb: OrientedBox?
        var capRegion: OrientedBox?
    }

    // MARK: - Entry: class-conditioned fit of a fused cloud

    /// Fit a fused, voxel-downsampled cloud. Revolution classes get the profile fit
    /// (falling back to the box fit when the cloud isn't a revolution solid);
    /// boxy/unknown classes go straight to the box fit. nil = not enough data yet.
    static func fit(points: [SIMD3<Float>], classLabel: String?,
                    arcCoverage: Float) -> Fit? {
        if FormPriors.revolutionClass(for: classLabel) != nil {
            if let fit = revolutionFit(points: points, arcCoverage: arcCoverage) {
                return fit
            }
        }
        return boxFit(points: points, arcCoverage: arcCoverage)
    }

    // MARK: - (a) Surface-of-revolution fit

    static func revolutionFit(points: [SIMD3<Float>], arcCoverage: Float) -> Fit? {
        guard points.count >= minRevolutionPoints else { return nil }

        // Percentile-trimmed height range (1 %–99 %) kills stragglers.
        let ys = points.map(\.y).sorted()
        let y0 = percentile(ys, 0.01)
        let y1 = percentile(ys, 0.99)
        let height = y1 - y0
        guard height > 0.02 else { return nil }

        // Axis from a Kåsa circle fit over the mid-body XZ (25–75 % height band).
        let midBody = points.filter { $0.y >= y0 + 0.25 * height && $0.y <= y0 + 0.75 * height }
        let bodyXZ = (midBody.count >= 20 ? midBody : points).map { SIMD2<Float>($0.x, $0.z) }
        let axis = circleCenter(bodyXZ) ?? centroid2(bodyXZ)

        // Radius-vs-height profile: 5 mm bins, robust per-bin radius.
        let binCount = max(Int(ceil(height / heightBinSize)), 1)
        var binRadii = [[Float]](repeating: [], count: binCount)
        for p in points {
            guard p.y >= y0, p.y <= y1 else { continue }
            let bin = min(Int((p.y - y0) / heightBinSize), binCount - 1)
            binRadii[bin].append(simd_distance(SIMD2(p.x, p.z), axis))
        }
        var profile = [Float?](repeating: nil, count: binCount)
        for (i, radii) in binRadii.enumerated() where radii.count >= minBinPoints {
            profile[i] = robustRadius(radii)
        }
        // Light 3-bin median smoothing (nil bins stay nil).
        profile = medianSmooth(profile)

        // Body median: bins below the top quarter.
        let topQuarterStart = Int(Float(binCount) * 0.75)
        let bodyRadii = (0..<topQuarterStart).compactMap { profile[$0] }
        guard bodyRadii.count >= 3, let bodyMedian = median(bodyRadii) else { return nil }

        let bodyDiameter = 2 * bodyMedian
        guard diameterSanity.contains(bodyDiameter), heightSanity.contains(height)
        else { return nil }

        // ── Cap: sustained radius drop ≥ 25 % vs body median, top quarter ──
        var capRegion: OrientedBox?
        var capDiameter: Float?
        var capHeight: Float?
        let stepThreshold = (1 - capDropFraction) * bodyMedian
        let topBins = (topQuarterStart..<binCount).compactMap { i -> (index: Int, radius: Float)? in
            profile[i].map { (i, $0) }
        }
        if let stepAt = topBins.firstIndex(where: { $0.radius <= stepThreshold }) {
            let capBins = Array(topBins[stepAt...])
            let compliant = capBins.filter { $0.radius <= (1 - capDropFraction + 0.05) * bodyMedian }
            let sustained = capBins.count >= capMinBins
                && Float(compliant.count) / Float(capBins.count) >= capSustainFraction
                && capBins.allSatisfy { $0.radius <= 0.95 * bodyMedian }
            if sustained {
                // The physical cap is the TOP of the capped section (a bottle's
                // neck sits below it) — measure diameter from the upper half.
                let upper = capBins.suffix(max(capMinBins, capBins.count / 2))
                if let capRadius = median(upper.map(\.radius)) {
                    let stepY = y0 + Float(capBins[0].index) * heightBinSize
                    let capH = y1 - stepY
                    capDiameter = 2 * capRadius
                    capHeight = capH
                    capRegion = OrientedBox(
                        center: SIMD3(axis.x, stepY + capH / 2, axis.y),
                        extents: SIMD3(2 * capRadius, capH, 2 * capRadius),
                        yaw: 0)
                }
            }
        }

        // ── Residual: RMS radial error of all binned points vs the profile ──
        var sumSq: Float = 0
        var n = 0
        for (i, radii) in binRadii.enumerated() {
            guard let r = profile[i] else { continue }
            for radius in radii {
                let d = radius - r
                sumSq += d * d
                n += 1
            }
        }
        guard n > 0 else { return nil }
        let residual = sqrt(sumSq / Float(n))
        guard residual <= maxRevolutionResidual else { return nil }

        // Compatibility OBB: contain the profile (95th-percentile bin radius).
        let allRadii = profile.compactMap { $0 }.sorted()
        let containRadius = percentile(allRadii, 0.95)
        let obb = OrientedBox(center: SIMD3(axis.x, (y0 + y1) / 2, axis.y),
                              extents: SIMD3(2 * containRadius, height, 2 * containRadius),
                              yaw: 0)

        var dims: [String: Float] = ["bodyDiameter": bodyDiameter, "height": height]
        if let capDiameter, let capHeight {
            dims["capDiameter"] = capDiameter
            dims["capHeight"] = capHeight
        }
        return Fit(form: ObjectForm(kind: .revolution, dimensions: dims,
                                    source: .measured, arcCoverage: arcCoverage,
                                    residual: residual),
                   obb: obb, capRegion: capRegion)
    }

    // MARK: - (b) Oriented-box fit (boxy / unknown / non-revolution clouds)

    static func boxFit(points: [SIMD3<Float>], arcCoverage: Float) -> Fit? {
        guard points.count >= minBoxPoints else { return nil }

        // Robust trim: drop points far outside the per-axis MAD envelope so a few
        // background stragglers can't inflate the fused box.
        let trimmed = madTrim(points)
        guard trimmed.count >= minBoxPoints,
              let obb = OrientedBoxFitter.fit(points: trimmed) else { return nil }

        // Residual: RMS distance to the nearest box FACE, in the box's local frame
        // (0 for a perfect hollow shell of face points).
        let c = cos(obb.yaw), s = sin(obb.yaw)
        let h = obb.extents / 2
        var sumSq: Float = 0
        for p in trimmed {
            let d = p - obb.center
            let local = SIMD3<Float>(c * d.x - s * d.z, d.y, s * d.x + c * d.z)
            let outside = simd_max(simd_abs(local) - h, SIMD3<Float>(repeating: 0))
            let out = simd_length(outside)
            let faceGap = simd_reduce_min(h - simd_abs(simd_clamp(local, -h, h)))
            let dist = out > 0 ? out : faceGap
            sumSq += dist * dist
        }
        let residual = sqrt(sumSq / Float(trimmed.count))

        let dims: [String: Float] = ["width": obb.extents.x,
                                     "depth": obb.extents.z,
                                     "height": obb.extents.y]
        return Fit(form: ObjectForm(kind: .box, dimensions: dims, source: .measured,
                                    arcCoverage: arcCoverage, residual: residual),
                   obb: obb, capRegion: nil)
    }

    // MARK: - Stage 4 pure halves: depth collapse + class-prior fit

    /// Is this detection's depth unusable? (Transparent objects: dToF through clear
    /// plastic returns few high-confidence pixels, or points scattered far beyond
    /// the silhouette's plausible size.)
    static func depthCollapsed(highConfFraction: Float, validCount: Int,
                               pointSpread: Float, expectedSize: Float) -> Bool {
        if validCount < 30 { return true }
        if highConfFraction < 0.2 { return true }
        if expectedSize > 0.01, pointSpread > max(3 * expectedSize, expectedSize + 0.4) {
            return true
        }
        return false
    }

    /// Class-prior fallback: dimensions from FormPriors, scaled by the RGB
    /// silhouette height when known, seated on the support point when known.
    static func priorFit(classLabel: String?, silhouetteHeightMeters: Float?,
                         seatPoint: SIMD3<Float>?, arcCoverage: Float) -> Fit? {
        guard let prior = FormPriors.prior(for: classLabel) else { return nil }
        var scale: Float = 1
        if let silhouette = silhouetteHeightMeters, silhouette > 0.01 {
            scale = min(max(silhouette / prior.height, FormPriors.scaleClamp.lowerBound),
                        FormPriors.scaleClamp.upperBound)
        }
        let height = prior.height * scale
        let diameter = prior.diameter * scale
        let dims: [String: Float] = ["bodyDiameter": diameter, "height": height]
        let form = ObjectForm(kind: .revolution, dimensions: dims, source: .prior,
                              arcCoverage: arcCoverage, residual: 0)
        // Anchored SEATED on the support surface (base center at the seat point).
        let obb = seatPoint.map { seat in
            OrientedBox(center: SIMD3(seat.x, seat.y + height / 2, seat.z),
                        extents: SIMD3(diameter, height, diameter),
                        yaw: 0)
        }
        return Fit(form: form, obb: obb, capRegion: nil)
    }

    // MARK: - Small robust-statistics helpers (pure)

    static func median(_ values: [Float]) -> Float? {
        guard !values.isEmpty else { return nil }
        let s = values.sorted()
        return s[s.count / 2]
    }

    /// Median with MAD outlier rejection, re-medianed over the inliers.
    static func robustRadius(_ radii: [Float]) -> Float? {
        guard let med = median(radii) else { return nil }
        let deviations = radii.map { abs($0 - med) }
        guard let mad = median(deviations) else { return med }
        let gate = max(3 * 1.4826 * mad, 0.002)
        let inliers = radii.filter { abs($0 - med) <= gate }
        return median(inliers) ?? med
    }

    /// p-quantile of a SORTED array (nearest-rank).
    static func percentile(_ sorted: [Float], _ p: Float) -> Float {
        guard !sorted.isEmpty else { return 0 }
        let idx = Int(Float(sorted.count - 1) * min(max(p, 0), 1))
        return sorted[idx]
    }

    /// 3-wide median smoothing of an optional profile (nils pass through).
    static func medianSmooth(_ profile: [Float?]) -> [Float?] {
        guard profile.count >= 3 else { return profile }
        var out = profile
        for i in 1..<(profile.count - 1) {
            guard profile[i] != nil else { continue }
            let window = [profile[i - 1], profile[i], profile[i + 1]].compactMap { $0 }
            out[i] = median(window)
        }
        return out
    }

    static func centroid2(_ pts: [SIMD2<Float>]) -> SIMD2<Float> {
        guard !pts.isEmpty else { return .zero }
        return pts.reduce(SIMD2<Float>.zero, +) / Float(pts.count)
    }

    /// Kåsa least-squares circle fit: x² + z² = 2ax + 2bz + c → center (a, b).
    /// nil when ill-conditioned (tiny arc / collinear) or the answer is absurd —
    /// callers fall back to the centroid (biased for partial arcs, but bounded).
    static func circleCenter(_ pts: [SIMD2<Float>]) -> SIMD2<Float>? {
        guard pts.count >= 8 else { return nil }
        // Center the data for conditioning.
        let mean = centroid2(pts)
        var suu: Float = 0, svv: Float = 0, suv: Float = 0
        var suw: Float = 0, svw: Float = 0
        for p in pts {
            let u = p.x - mean.x, v = p.y - mean.y
            let w = u * u + v * v
            suu += u * u; svv += v * v; suv += u * v
            suw += u * w; svw += v * w
        }
        let det = suu * svv - suv * suv
        // Near-singular normal equations = the arc doesn't constrain a circle.
        guard abs(det) > 1e-9 * Float(pts.count) * Float(pts.count) else { return nil }
        let a = (suw * svv - svw * suv) / (2 * det)
        let b = (svw * suu - suw * suv) / (2 * det)
        let center = SIMD2<Float>(mean.x + a, mean.y + b)
        let radius = sqrt(max(a * a + b * b + (suu + svv) / Float(pts.count), 0))
        // Sanity: objects of interest are hand-to-tabletop scale.
        guard radius.isFinite, radius > 0.005, radius < 0.5,
              simd_distance(center, mean) < 0.5 else { return nil }
        return center
    }

    /// Per-axis MAD envelope trim (4×1.4826×MAD, floored at 5 cm) — kills
    /// background stragglers before the box fit.
    static func madTrim(_ points: [SIMD3<Float>]) -> [SIMD3<Float>] {
        guard points.count >= 8 else { return points }
        func axisGate(_ values: [Float]) -> (center: Float, halfWidth: Float)? {
            guard let med = median(values),
                  let mad = median(values.map { abs($0 - med) }) else { return nil }
            return (med, max(4 * 1.4826 * mad, 0.05))
        }
        guard let gx = axisGate(points.map(\.x)),
              let gy = axisGate(points.map(\.y)),
              let gz = axisGate(points.map(\.z)) else { return points }
        return points.filter {
            abs($0.x - gx.center) <= gx.halfWidth &&
            abs($0.y - gy.center) <= gy.halfWidth &&
            abs($0.z - gz.center) <= gz.halfWidth
        }
    }
}
