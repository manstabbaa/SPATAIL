// FormFitter off-device harness — Perception v2 verification (spec §0 law 5:
// if you can't see what it saw, it doesn't ship).
//
// Compiles the EXACT shipped sources (no copies) against macOS:
//
//   cd ios/Spatail
//   DEVELOPER_DIR=/Applications/Xcode.app swiftc -O \
//     Sources/Core/SpatailCore.swift \
//     Sources/Perception/Spatial/GeometryFit.swift \
//     Sources/Perception/Form/FormPriors.swift \
//     Sources/Perception/Form/FormFitter.swift \
//     Sources/Perception/Form/FormPointCloud.swift \
//     Harness/main.swift -o /tmp/form_harness && /tmp/form_harness
//
// Lives OUTSIDE Sources/ so xcodegen never compiles it into the app.

import Foundation
import simd

// MARK: - Tiny assert rig

var passed = 0
var failed = 0

func check(_ condition: Bool, _ name: String, _ detail: String = "") {
    if condition {
        passed += 1
        print("  PASS  \(name)")
    } else {
        failed += 1
        print("  FAIL  \(name)\(detail.isEmpty ? "" : " — \(detail)")")
    }
}

func approx(_ a: Float, _ b: Float, tol: Float) -> Bool { abs(a - b) <= tol }

// Deterministic LCG so runs are reproducible.
struct LCG: RandomNumberGenerator {
    var state: UInt64
    mutating func next() -> UInt64 {
        state = state &* 6364136223846793005 &+ 1442695040888963407
        return state
    }
}
var rng = LCG(state: 0x5EED_5EED)

func gauss(_ sigma: Float) -> Float {
    // Box–Muller
    let u1 = max(Float.random(in: 0..<1, using: &rng), 1e-7)
    let u2 = Float.random(in: 0..<1, using: &rng)
    return sigma * sqrt(-2 * log(u1)) * cos(2 * .pi * u2)
}

// MARK: - Synthetic clouds

/// Points on a surface of revolution: body radius to `stepY`, cap radius above.
/// `arcDegrees` limits azimuth (single-sided observation), `noise` is radial σ.
func revolutionCloud(axis: SIMD2<Float>, baseY: Float,
                     bodyRadius: Float, height: Float,
                     capRadius: Float?, capHeight: Float,
                     arcDegrees: Float, noise: Float,
                     count: Int) -> [SIMD3<Float>] {
    var pts: [SIMD3<Float>] = []
    let arc = arcDegrees * .pi / 180
    for _ in 0..<count {
        let y = baseY + Float.random(in: 0..<height, using: &rng)
        let inCap = capRadius != nil && y > baseY + height - capHeight
        let r = (inCap ? capRadius! : bodyRadius) + gauss(noise)
        let theta = Float.random(in: -arc / 2..<arc / 2, using: &rng)
        pts.append(SIMD3(axis.x + r * cos(theta), y, axis.y + r * sin(theta)))
    }
    return pts
}

print("— FormFitter harness —")

// MARK: 1. The founder's bottle: body ⌀63, h 218, cap ⌀31 × 22 mm, 140° arc

do {
    print("[1] capped bottle (⌀63 h218 cap ⌀31×22, 140° arc, 2 mm noise)")
    let axis = SIMD2<Float>(0.42, -0.31)
    let baseY: Float = 0.74
    let pts = revolutionCloud(axis: axis, baseY: baseY,
                              bodyRadius: 0.0315, height: 0.218,
                              capRadius: 0.0155, capHeight: 0.022,
                              arcDegrees: 140, noise: 0.002, count: 4000)

    // Route through the shipped voxel cloud (stage 2) exactly as the engine does.
    let cloud = FormPointCloud()
    cloud.insert(points: pts, viewpoint: SIMD3(0.42, 1.2, 0.6),
                 measurementCenter: SIMD3(axis.x, baseY + 0.109, axis.y), now: 1)

    let fit = FormFitter.fit(points: cloud.snapshot, classLabel: "water bottle",
                             arcCoverage: cloud.arcCoverage)
    check(fit != nil, "fit produced")
    if let fit {
        check(fit.form.kind == .revolution, "kind == revolution", "\(fit.form.kind)")
        check(fit.form.source == .measured, "source == measured")
        check(approx(fit.form.bodyDiameter ?? -1, 0.063, tol: 0.008),
              "bodyDiameter ≈ 63 mm", "\((fit.form.bodyDiameter ?? -1) * 1000) mm")
        check(approx(fit.form.height ?? -1, 0.218, tol: 0.012),
              "height ≈ 218 mm", "\((fit.form.height ?? -1) * 1000) mm")
        check(fit.form.capDiameter != nil, "cap found")
        if let cd = fit.form.capDiameter, let ch = fit.form.capHeight {
            check(approx(cd, 0.031, tol: 0.008), "capDiameter ≈ 31 mm", "\(cd * 1000) mm")
            check(approx(ch, 0.022, tol: 0.012), "cap step within tolerance",
                  "capHeight \(ch * 1000) mm")
        }
        check(fit.capRegion != nil, "measured cap region emitted")
        if let region = fit.capRegion {
            let expectedCenterY = baseY + 0.218 - (fit.form.capHeight ?? 0) / 2
            check(approx(region.center.y, expectedCenterY, tol: 0.012),
                  "cap region seated at profile step",
                  "y \(region.center.y) vs \(expectedCenterY)")
            check(approx(region.center.x, axis.x, tol: 0.01) &&
                  approx(region.center.z, axis.y, tol: 0.01),
                  "cap region on the axis")
        }
        if let obb = fit.obb {
            check(approx(obb.center.x, axis.x, tol: 0.012) &&
                  approx(obb.center.z, axis.y, tol: 0.012),
                  "OBB centered on axis (partial-arc bias corrected)",
                  "(\(obb.center.x), \(obb.center.z)) vs \(axis)")
        }
        check(fit.form.residual < 0.006, "residual sane", "\(fit.form.residual * 1000) mm")
    }
}

// MARK: 2. Full-arc can, no cap

do {
    print("[2] can (⌀66 h120, 360° arc) — no false cap")
    let pts = revolutionCloud(axis: SIMD2(-0.2, 0.5), baseY: 0.0,
                              bodyRadius: 0.033, height: 0.12,
                              capRadius: nil, capHeight: 0,
                              arcDegrees: 360, noise: 0.0015, count: 3000)
    let fit = FormFitter.fit(points: pts, classLabel: "soda can", arcCoverage: 0.9)
    check(fit != nil, "fit produced")
    if let fit {
        check(fit.form.kind == .revolution, "kind == revolution")
        check(approx(fit.form.bodyDiameter ?? -1, 0.066, tol: 0.006),
              "bodyDiameter ≈ 66 mm", "\((fit.form.bodyDiameter ?? -1) * 1000) mm")
        check(fit.form.capDiameter == nil, "no cap hallucinated")
    }
}

// MARK: 3. Transparent bottle: garbage depth → the PRIOR path

do {
    print("[3] sparse/garbage cloud (clear plastic) → .prior")
    // (a) the collapse decision fires
    check(FormFitter.depthCollapsed(highConfFraction: 0.08, validCount: 400,
                                    pointSpread: 1.2, expectedSize: 0.25),
          "collapse: high-conf fraction 8% < 20%")
    check(FormFitter.depthCollapsed(highConfFraction: 0.9, validCount: 12,
                                    pointSpread: 0.1, expectedSize: 0.25),
          "collapse: almost no valid pixels")
    check(FormFitter.depthCollapsed(highConfFraction: 0.6, validCount: 500,
                                    pointSpread: 1.4, expectedSize: 0.25),
          "collapse: point spread absurd vs box size")
    check(!FormFitter.depthCollapsed(highConfFraction: 0.85, validCount: 900,
                                     pointSpread: 0.24, expectedSize: 0.25),
          "no collapse on healthy depth")

    // (b) sparse garbage never yields a measured revolution
    var garbage: [SIMD3<Float>] = []
    for _ in 0..<60 {
        garbage.append(SIMD3(Float.random(in: -0.6..<0.6, using: &rng),
                             Float.random(in: 0..<1.0, using: &rng),
                             Float.random(in: -1.2..<0.2, using: &rng)))
    }
    check(FormFitter.revolutionFit(points: garbage, arcCoverage: 0.1) == nil,
          "sparse garbage → no revolution fit")

    // (c) the prior path: silhouette-scaled, seated on the support
    let seat = SIMD3<Float>(1.0, 0.75, -0.5)
    let fit = FormFitter.priorFit(classLabel: "water bottle",
                                  silhouetteHeightMeters: 0.20,
                                  seatPoint: seat, arcCoverage: 0.05)
    check(fit != nil, "prior fit produced for 'water bottle'")
    if let fit {
        check(fit.form.source == .prior, "source == prior (honest tag)")
        check(approx(fit.form.height ?? -1, 0.20, tol: 0.001),
              "height = silhouette 200 mm")
        let expectedD: Float = 0.065 * (0.20 / 0.22)
        check(approx(fit.form.bodyDiameter ?? -1, expectedD, tol: 0.002),
              "diameter scaled with silhouette",
              "\((fit.form.bodyDiameter ?? -1) * 1000) vs \(expectedD * 1000) mm")
        check(fit.obb != nil, "prior OBB seated")
        if let obb = fit.obb {
            check(approx(obb.center.y, seat.y + 0.10, tol: 0.002),
                  "seated ON the support surface (base at seat)")
        }
        check(fit.form.residual == 0, "prior carries no fake residual")
    }
    check(FormFitter.priorFit(classLabel: "stapler", silhouetteHeightMeters: 0.2,
                              seatPoint: seat, arcCoverage: 0) == nil,
          "unknown class → no prior (stays unmeasured, never lies)")
    // scale clamp: absurd silhouette can't make a 2 m bottle
    if let clamped = FormFitter.priorFit(classLabel: "bottle",
                                         silhouetteHeightMeters: 2.0,
                                         seatPoint: seat, arcCoverage: 0) {
        check(approx(clamped.form.height ?? -1, 0.22 * 2.0, tol: 0.001),
              "silhouette scale clamped at 2×")
    }
}

// MARK: 4. Boxy object → OBB path

do {
    print("[4] box cloud (0.30×0.20×0.15, yaw 0.3) → box fit")
    let yaw: Float = 0.3
    let c = cos(yaw), s = sin(yaw)
    let center = SIMD3<Float>(0.1, 0.5, -0.4)
    var pts: [SIMD3<Float>] = []
    func world(_ lx: Float, _ ly: Float, _ lz: Float) -> SIMD3<Float> {
        SIMD3(center.x + c * lx + s * lz, center.y + ly, center.z - s * lx + c * lz)
    }
    for _ in 0..<1200 {
        let face = Int.random(in: 0..<3, using: &rng)
        let u = Float.random(in: -0.15..<0.15, using: &rng)
        let v = Float.random(in: -0.075..<0.075, using: &rng)
        let w = Float.random(in: -0.10..<0.10, using: &rng)
        let n = gauss(0.002)
        switch face {
        case 0:  pts.append(world(u, v, 0.10 + n))          // front face
        case 1:  pts.append(world(0.15 + n, v, w))          // side face
        default: pts.append(world(u, 0.075 + n, w))         // top face
        }
    }
    let fit = FormFitter.fit(points: pts, classLabel: "book", arcCoverage: 0.3)
    check(fit != nil, "fit produced")
    if let fit, let obb = fit.obb {
        check(fit.form.kind == .box, "kind == box")
        let dims = [obb.extents.x, obb.extents.z].sorted()
        check(approx(dims[1], 0.30, tol: 0.03), "long side ≈ 300 mm", "\(dims[1] * 1000)")
        check(approx(dims[0], 0.20, tol: 0.03), "short side ≈ 200 mm", "\(dims[0] * 1000)")
        check(approx(obb.extents.y, 0.15, tol: 0.02), "height ≈ 150 mm",
              "\(obb.extents.y * 1000)")
        var dyaw = abs(obb.yaw - yaw).truncatingRemainder(dividingBy: .pi / 2)
        dyaw = min(dyaw, .pi / 2 - dyaw)
        check(dyaw < 0.1, "yaw recovered mod π/2", "\(obb.yaw) vs \(yaw)")
    }
}

// MARK: 5. Voxel cloud invariants (stage 2 laws)

do {
    print("[5] FormPointCloud: hard cap + arc coverage")
    let cloud = FormPointCloud()
    var pts: [SIMD3<Float>] = []
    for _ in 0..<60_000 {
        pts.append(SIMD3(Float.random(in: 0..<0.5, using: &rng),
                         Float.random(in: 0..<0.5, using: &rng),
                         Float.random(in: 0..<0.5, using: &rng)))
    }
    cloud.insert(points: pts, viewpoint: SIMD3(2, 1, 0),
                 measurementCenter: SIMD3(0.25, 0.25, 0.25), now: 1)
    check(cloud.count <= FormPointCloud.maxPoints,
          "hard cap enforced (\(cloud.count) ≤ \(FormPointCloud.maxPoints))")
    check(cloud.snapshot.count == cloud.count, "snapshot consistent")

    let arcCloud = FormPointCloud()
    let obj = SIMD3<Float>(0, 0, 0)
    for (i, angle) in [Float(0), .pi / 2, .pi].enumerated() {
        let view = SIMD3<Float>(cos(angle), 0.5, sin(angle))
        arcCloud.insert(points: [SIMD3(0.01 * Float(i), 0.01, 0)],
                        viewpoint: view, measurementCenter: obj,
                        now: TimeInterval(i))
    }
    let expected = 3.0 / Float(FormPointCloud.azimuthBins)
    check(approx(arcCloud.arcCoverage, expected, tol: 0.06),
          "arc coverage ≈ 3 bins", "\(arcCloud.arcCoverage) vs \(expected)")

    // Jump reset: the object moved → stale cloud drops
    arcCloud.insert(points: [SIMD3(1, 0, 1)], viewpoint: SIMD3(2, 1, 2),
                    measurementCenter: SIMD3(1, 0, 1), now: 4)
    check(arcCloud.count == 1, "cloud reset on center jump", "\(arcCloud.count)")
}

// MARK: 6. Class conditioning sanity

do {
    print("[6] FormPriors token matching")
    check(FormPriors.revolutionClass(for: "Water Bottle") == "bottle",
          "'Water Bottle' → bottle")
    check(FormPriors.revolutionClass(for: "sunglasses") == nil,
          "'sunglasses' does NOT match 'glass'")
    check(FormPriors.revolutionClass(for: "Coffee Mug") == "mug", "'Coffee Mug' → mug")
    check(FormPriors.prior(for: "wine glass")?.height == 0.14, "'wine glass' prior")
}

print("\n\(passed) passed, \(failed) failed")
exit(failed == 0 ? 0 : 1)
