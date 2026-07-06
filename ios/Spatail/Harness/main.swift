// FormFitter + Scene Coherence off-device harness (spec §0 law 5:
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
//     Sources/Registry/RegistryFusionMath.swift \
//     Sources/Registry/RegistryCoherence.swift \
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

// MARK: 7. Scene Coherence — registry merge pass (the dedupe backstop)

func makeObject(label: String? = nil, confidence: Float = 0,
                center: SIMD3<Float>, extents: SIMD3<Float>, yaw: Float = 0,
                parts: [SpatailPart] = [], form: ObjectForm? = nil,
                seenStreak: Int = 0, established: Bool = false,
                firstSeenAt: TimeInterval, lastMeasuredAt: TimeInterval)
    -> SpatailObject {
    SpatailObject(label: label, confidence: confidence,
                  obb: OrientedBox(center: center, extents: extents, yaw: yaw),
                  parts: parts, form: form,
                  seenStreak: seenStreak, established: established,
                  firstSeenAt: firstSeenAt, lastMeasuredAt: lastMeasuredAt)
}

do {
    print("[7] RegistryCoherence merge pass")

    // (a) two overlapping OBBs → ONE survivor with absorbed parts + label
    let labeled = makeObject(label: "headphones", confidence: 0.85,
                             center: SIMD3(0.50, 0.15, -0.80),
                             extents: SIMD3(0.20, 0.22, 0.18),
                             parts: [SpatailPart(label: "cap", box: nil,
                                                 region: nil, confidence: 0.9)],
                             firstSeenAt: 1, lastMeasuredAt: 10)
    var dup = makeObject(center: SIMD3(0.55, 0.16, -0.78),          // 5–6 cm offset
                         extents: SIMD3(0.18, 0.20, 0.16),
                         firstSeenAt: 6, lastMeasuredAt: 11)
    dup.parts = [SpatailPart(label: "speaker", box: nil, region: nil, confidence: 0.5)]

    let far = makeObject(label: "mug", confidence: 0.7,
                         center: SIMD3(1.60, 0.10, -0.80),          // 1.1 m away
                         extents: SIMD3(0.09, 0.10, 0.09),
                         firstSeenAt: 2, lastMeasuredAt: 10)

    let (merged, aliases) = RegistryCoherence.mergePass([labeled, dup, far])
    check(merged.count == 2, "overlapping pair merged, distant object kept",
          "count \(merged.count)")
    let survivor = merged.first { $0.id == labeled.id }
    check(survivor != nil, "labeled object survives (labeled beats unlabeled)")
    check(aliases[dup.id] == labeled.id, "loser id aliased to survivor")
    check(survivor?.parts.contains { $0.label == "speaker" } == true,
          "survivor absorbed the loser's parts")
    check(survivor?.parts.contains { $0.label == "cap" } == true,
          "survivor kept its own parts")
    check(survivor?.lastMeasuredAt == 11, "freshness = max of both")
    check(merged.contains { $0.id == far.id }, "non-overlapping stays separate")

    // (b) survivor rules: labeled-no-form beats unlabeled-with-measured-form,
    //     but absorbs the measured form + its OBB (geometry truth travels)
    let measuredForm = ObjectForm(kind: .revolution,
                                  dimensions: ["bodyDiameter": 0.063, "height": 0.218],
                                  source: .measured, arcCoverage: 0.6, residual: 0.002)
    let formOnly = makeObject(center: SIMD3(0.02, 0.11, 0.02),
                              extents: SIMD3(0.063, 0.218, 0.063),
                              form: measuredForm,
                              firstSeenAt: 3, lastMeasuredAt: 12)
    let labeledOnly = makeObject(label: "bottle", confidence: 0.9,
                                 center: SIMD3(0.00, 0.10, 0.00),
                                 extents: SIMD3(0.07, 0.20, 0.07),
                                 firstSeenAt: 8, lastMeasuredAt: 12)
    let (merged2, aliases2) = RegistryCoherence.mergePass([formOnly, labeledOnly])
    check(merged2.count == 1, "form-only + labeled merge to one")
    check(merged2.first?.id == labeledOnly.id, "labeled survives over measured form")
    check(merged2.first?.form?.source == .measured, "measured form absorbed")
    check(merged2.first?.obb.extents.y == 0.218, "form OBB absorbed with the form")
    check(aliases2[formOnly.id] == labeledOnly.id, "alias recorded")

    // (c) re-detection after merge matches the survivor (hysteresis: the loser
    //     is gone; a fresh duplicate OBB merges straight into the survivor)
    if let s = survivor {
        let redetect = makeObject(center: SIMD3(0.56, 0.15, -0.77),
                                  extents: SIMD3(0.19, 0.21, 0.17),
                                  seenStreak: 1,
                                  firstSeenAt: 20, lastMeasuredAt: 20)
        check(RegistryCoherence.shouldMerge(s, redetect),
              "re-detection near survivor merges into it")
        let (again, aliases3) = RegistryCoherence.mergePass([s, redetect, far])
        check(again.count == 2 && aliases3[redetect.id] == s.id,
              "re-detection collapsed onto the SAME survivor id")
        // stability: a second pass over the merged set changes nothing
        let (stable, aliases4) = RegistryCoherence.mergePass(again)
        check(stable.count == again.count && aliases4.isEmpty,
              "merge is stable — no oscillation")
    }

    // (d) center-branch height gate: same XZ spot, different shelf → NO merge
    let low = makeObject(center: SIMD3(0, 0.10, 0), extents: SIMD3(0.1, 0.1, 0.1),
                         firstSeenAt: 1, lastMeasuredAt: 5)
    let high = makeObject(center: SIMD3(0, 0.60, 0), extents: SIMD3(0.1, 0.1, 0.1),
                          firstSeenAt: 1, lastMeasuredAt: 5)
    check(!RegistryCoherence.shouldMerge(low, high),
          "stacked shelf levels never merge")

    // (e) XZ IoU sanity: identical footprints = 1, half-shift ≈ 1/3
    let boxA = OrientedBox(center: SIMD3(0, 0, 0), extents: SIMD3(0.2, 0.2, 0.2), yaw: 0)
    let boxB = OrientedBox(center: SIMD3(0.1, 0, 0), extents: SIMD3(0.2, 0.2, 0.2), yaw: 0)
    check(approx(RegistryCoherence.xzIoU(boxA, boxA), 1.0, tol: 0.001), "IoU self = 1")
    check(approx(RegistryCoherence.xzIoU(boxA, boxB), 1.0 / 3.0, tol: 0.01),
          "IoU half-shift = 1/3", "\(RegistryCoherence.xzIoU(boxA, boxB))")
}

// MARK: 8. Scene Coherence — display-worthiness gating

do {
    print("[8] display-worthiness (labeled OR form OR 3 consecutive ticks, cap 12)")

    let form = ObjectForm(kind: .box, dimensions: [:], source: .prior,
                          arcCoverage: 0, residual: 0)
    var objs: [SpatailObject] = [
        makeObject(label: "bottle", confidence: 0.9, center: SIMD3(0, 0, 0),
                   extents: SIMD3(0.1, 0.1, 0.1), firstSeenAt: 1, lastMeasuredAt: 10),
        makeObject(center: SIMD3(2, 0, 0), extents: SIMD3(0.1, 0.1, 0.1),
                   form: form, firstSeenAt: 1, lastMeasuredAt: 10),      // form only
        makeObject(center: SIMD3(4, 0, 0), extents: SIMD3(0.1, 0.1, 0.1),
                   seenStreak: 3, firstSeenAt: 1, lastMeasuredAt: 10),   // streak 3
        makeObject(center: SIMD3(6, 0, 0), extents: SIMD3(0.1, 0.1, 0.1),
                   seenStreak: 1, firstSeenAt: 1, lastMeasuredAt: 10),   // candidate
        makeObject(center: SIMD3(8, 0, 0), extents: SIMD3(0.1, 0.1, 0.1),
                   seenStreak: 0, established: true,
                   firstSeenAt: 1, lastMeasuredAt: 10),                  // hysteresis
    ]
    RegistryCoherence.assignDisplayWorthiness(&objs)
    check(objs[0].displayWorthy, "labeled → worthy")
    check(objs[1].displayWorthy, "measured/prior form → worthy")
    check(objs[2].displayWorthy, "3 consecutive ticks → worthy")
    check(!objs[3].displayWorthy, "1 tick, no label/form → internal")
    check(objs[4].displayWorthy, "once-established survives a missed tick")

    // Cap at 12: 14 labeled (conf 0.99 … 0.86) + 1 established-unlabeled →
    // exactly 12 worthy, all labeled, lowest-confidence labeled excluded.
    var crowd: [SpatailObject] = (0..<14).map { i in
        makeObject(label: "obj\(i)", confidence: 0.99 - Float(i) * 0.01,
                   center: SIMD3(Float(i), 0, 0), extents: SIMD3(0.1, 0.1, 0.1),
                   firstSeenAt: 1, lastMeasuredAt: 10)
    }
    crowd.append(makeObject(center: SIMD3(20, 0, 0), extents: SIMD3(0.1, 0.1, 0.1),
                            established: true, firstSeenAt: 1, lastMeasuredAt: 11))
    RegistryCoherence.assignDisplayWorthiness(&crowd)
    let worthy = crowd.filter(\.displayWorthy)
    check(worthy.count == 12, "cap at 12", "\(worthy.count)")
    check(worthy.allSatisfy { $0.label != nil }, "labeled outrank unlabeled at the cap")
    check(!crowd[12].displayWorthy && !crowd[13].displayWorthy,
          "lowest-confidence labeled excluded")
}

// MARK: 9. Scene Coherence — named-slice geometry (generic part regions)

do {
    print("[9] named OBB slices (top/base/left_side/right_side/front/back)")
    let parent = OrientedBox(center: SIMD3(1.0, 0.5, -2.0),
                             extents: SIMD3(0.30, 0.25, 0.18), yaw: 0)

    if let top = RegistryCoherence.namedSlice("top", of: parent) {
        check(approx(top.center.y, 0.6, tol: 0.0001) &&
              approx(top.extents.y, 0.05, tol: 0.0001) &&
              approx(top.extents.x, 0.30, tol: 0.0001),
              "top = upper 20% slice", "\(top)")
    } else { check(false, "top slice produced") }

    if let base = RegistryCoherence.namedSlice("base", of: parent) {
        check(approx(base.center.y, 0.4, tol: 0.0001) &&
              approx(base.extents.y, 0.05, tol: 0.0001),
              "base = lower 20% slice", "\(base)")
    } else { check(false, "base slice produced") }
    check(RegistryCoherence.namedSlice("bottom", of: parent)
          == RegistryCoherence.namedSlice("base", of: parent),
          "'bottom' aliases 'base'")

    // Minor horizontal axis is Z (0.18 < 0.30): sides are outer 30% along Z.
    if let left = RegistryCoherence.namedSlice("left_side", of: parent) {
        check(approx(left.extents.z, 0.054, tol: 0.0001) &&
              approx(left.center.z, -2.063, tol: 0.0001) &&
              approx(left.extents.x, 0.30, tol: 0.0001) &&
              approx(left.extents.y, 0.25, tol: 0.0001) &&
              approx(left.center.x, 1.0, tol: 0.0001),
              "left_side = outer 30% along minor axis (−Z)",
              "center \(left.center), extents \(left.extents)")
    } else { check(false, "left_side slice produced") }
    if let right = RegistryCoherence.namedSlice("right_side", of: parent) {
        check(approx(right.center.z, -1.937, tol: 0.0001),
              "right_side mirrors on +Z", "\(right.center)")
    } else { check(false, "right_side slice produced") }

    // Major axis is X: front faces the camera when one is given.
    let camera = SIMD3<Float>(3.0, 0.5, -2.0)          // off the +X end
    if let front = RegistryCoherence.namedSlice("front", of: parent,
                                                cameraPosition: camera) {
        check(approx(front.center.x, 1.105, tol: 0.0001) &&
              approx(front.extents.x, 0.09, tol: 0.0001),
              "front = outer 30% along major axis, nearer the camera",
              "\(front.center)")
    } else { check(false, "front slice produced") }
    if let back = RegistryCoherence.namedSlice("back", of: parent,
                                               cameraPosition: camera) {
        check(approx(back.center.x, 0.895, tol: 0.0001),
              "back = far end from the camera", "\(back.center)")
    } else { check(false, "back slice produced") }
    if let frontAxis = RegistryCoherence.namedSlice("front", of: parent) {
        check(approx(frontAxis.center.x, 1.105, tol: 0.0001),
              "no camera → axis-aligned front (+axis)")
    } else { check(false, "axis-aligned front produced") }

    // Yaw carries through: yaw π/2 maps local +Z onto world +X.
    let turned = OrientedBox(center: SIMD3(1.0, 0.5, -2.0),
                             extents: SIMD3(0.30, 0.25, 0.18), yaw: .pi / 2)
    if let right = RegistryCoherence.namedSlice("right_side", of: turned) {
        check(approx(right.center.x, 1.063, tol: 0.001) &&
              approx(right.center.z, -2.0, tol: 0.001),
              "yawed OBB: side slice follows the local frame", "\(right.center)")
        check(right.yaw == turned.yaw, "slice oriented to the parent")
    } else { check(false, "yawed right_side produced") }

    check(RegistryCoherence.namedSlice("spout", of: parent) == nil,
          "unknown name → nil (solver falls back to the whole OBB)")

    // Byte-compat with PlacementSolver's §3 cap/lid/top fallback: same math.
    if let cap = RegistryCoherence.namedSlice("cap", of: parent) {
        let topSlice = max(parent.extents.y * 0.2, Float(0.01))
        let solverCenterY = parent.center.y + parent.extents.y / 2 - topSlice / 2
        check(cap.center.y == solverCenterY && cap.extents.y == topSlice,
              "'cap' slice byte-compatible with the §3 solver fallback")
    } else { check(false, "cap slice produced") }
}

// MARK: 10. Local identity — on-device detector labels (the weaker source)

do {
    print("[10] attachLocalLabel (fills empty identity, never fights the VLM)")

    // (a) fills EMPTY identity after 2 agreeing ticks, not 1.
    var obj = makeObject(center: SIMD3(0, 0, 0), extents: SIMD3(0.1, 0.2, 0.1),
                         firstSeenAt: 1, lastMeasuredAt: 1)
    RegistryFusion.attachLocalLabel("Water Bottle", confidence: 0.4, at: 1, into: &obj)
    check(obj.label == nil && obj.pendingLabel == "Water Bottle",
          "tick 1 → pending, not adopted")
    RegistryFusion.attachLocalLabel("Water Bottle", confidence: 0.45, at: 2, into: &obj)
    check(obj.label == "Water Bottle", "tick 2 → adopted")

    // (b) instant adopt at high confidence.
    var quick = makeObject(center: SIMD3(1, 0, 0), extents: SIMD3(0.1, 0.2, 0.1),
                           firstSeenAt: 1, lastMeasuredAt: 1)
    RegistryFusion.attachLocalLabel("Cat", confidence: 0.9, at: 1, into: &quick)
    check(quick.label == "Cat", "conf ≥ 0.8 → instant adopt")

    // (c) same-label re-confirm refreshes freshness, keeps best confidence.
    RegistryFusion.attachLocalLabel("cat", confidence: 0.5, at: 5, into: &quick)
    check(quick.label == "Cat" && quick.confidence == 0.9
          && quick.lastIdentifiedAt == 5,
          "re-confirm refreshes freshness, keeps max confidence")

    // (d) NEVER dethrones a different held label.
    var held = makeObject(label: "coke bottle", confidence: 0.6,
                          center: SIMD3(2, 0, 0), extents: SIMD3(0.1, 0.2, 0.1),
                          firstSeenAt: 1, lastMeasuredAt: 1)
    RegistryFusion.attachLocalLabel("Water Bottle", confidence: 0.95, at: 2, into: &held)
    RegistryFusion.attachLocalLabel("Water Bottle", confidence: 0.95, at: 3, into: &held)
    check(held.label == "coke bottle" && held.pendingLabel == nil,
          "different held label untouched, no pending pollution")

    // (e) never evicts a DIFFERENT pending candidate — the VLM's debounce
    // progress survives local ticks and still flips identity.
    var contested = makeObject(center: SIMD3(3, 0, 0), extents: SIMD3(0.1, 0.2, 0.1),
                               firstSeenAt: 1, lastMeasuredAt: 1)
    RegistryFusion.debounce(label: "espresso machine", confidence: 0.6,
                            at: 1, into: &contested)                    // VLM tick 1
    RegistryFusion.attachLocalLabel("Coffee Maker", confidence: 0.5,
                                    at: 2, into: &contested)            // local tick
    check(contested.pendingLabel == "espresso machine" && contested.pendingCount == 1,
          "local tick leaves the VLM's pending candidate alone")
    RegistryFusion.debounce(label: "espresso machine", confidence: 0.6,
                            at: 3, into: &contested)                    // VLM tick 2
    check(contested.label == "espresso machine", "VLM debounce completes past local ticks")

    // (f) after a LOCAL adoption, a different VLM label still wins via debounce.
    var localFirst = makeObject(center: SIMD3(4, 0, 0), extents: SIMD3(0.1, 0.2, 0.1),
                                firstSeenAt: 1, lastMeasuredAt: 1)
    RegistryFusion.attachLocalLabel("Bottle", confidence: 0.4, at: 1, into: &localFirst)
    RegistryFusion.attachLocalLabel("Bottle", confidence: 0.4, at: 2, into: &localFirst)
    check(localFirst.label == "Bottle", "local label adopted first")
    RegistryFusion.debounce(label: "olive oil bottle", confidence: 0.6,
                            at: 3, into: &localFirst)                   // VLM tick 1
    RegistryFusion.attachLocalLabel("Bottle", confidence: 0.4, at: 4, into: &localFirst)
    check(localFirst.pendingLabel == "olive oil bottle",
          "held-label local re-confirm keeps the VLM candidate pending")
    RegistryFusion.debounce(label: "olive oil bottle", confidence: 0.6,
                            at: 5, into: &localFirst)                   // VLM tick 2
    check(localFirst.label == "olive oil bottle", "VLM replaces the local label")
}

// MARK: 11. Entity layer v3 — class-scaled merge, adjacency, supported-by

do {
    print("[11] entity layer v3 (PERCEPTION_V3 §0/§1)")

    // (a) the founder's couch, verbatim: two `couch · 0.95` halves —
    // 1470×868×621 and 1401×681×625 mm, centers ~1.05 m apart. Bottle-scale
    // gates could never merge these; the class gate (couch: 1.2 m) must.
    let halfA = makeObject(label: "couch", confidence: 0.95,
                           center: SIMD3(0.00, 0.43, -2.0),
                           extents: SIMD3(1.470, 0.868, 0.621),
                           firstSeenAt: 1, lastMeasuredAt: 10)
    let halfB = makeObject(label: "couch", confidence: 0.95,
                           center: SIMD3(1.05, 0.34, -2.0),
                           extents: SIMD3(1.401, 0.681, 0.625),
                           firstSeenAt: 2, lastMeasuredAt: 10)
    check(RegistryCoherence.shouldMerge(halfA, halfB),
          "two half-couches merge via the class gate")
    let (couchMerged, couchAliases) = RegistryCoherence.mergePass([halfA, halfB])
    check(couchMerged.count == 1, "one couch survives", "\(couchMerged.count)")
    check(couchAliases[halfB.id] == halfA.id,
          "older half survives (stability tiebreak)")

    // (b) adjacency: same class, centers BEYOND the gate, footprints nearly
    // touching (gap 0.15 m ≤ 15 % of class length 2.0 m) → still one thing.
    let fragA = makeObject(label: "sofa", confidence: 0.9,
                           center: SIMD3(0, 0.4, 0),
                           extents: SIMD3(1.4, 0.8, 0.9),
                           firstSeenAt: 1, lastMeasuredAt: 10)
    let fragB = makeObject(label: "couch", confidence: 0.9,      // alias token
                           center: SIMD3(1.55, 0.4, 0),
                           extents: SIMD3(1.4, 0.8, 0.9),
                           firstSeenAt: 2, lastMeasuredAt: 10)
    check(RegistryCoherence.sharedClassToken(fragA, fragB) == "couch",
          "sofa/couch normalize to one class token")
    check(RegistryCoherence.shouldMerge(fragA, fragB),
          "adjacent same-class fragments merge (gap ≤ 15 % class length)")

    // (c) stacked same-class boxes with NO vertical overlap stay apart.
    let lower = makeObject(label: "shelf", confidence: 0.9,
                           center: SIMD3(0, 0.2, 1), extents: SIMD3(0.9, 0.3, 0.35),
                           firstSeenAt: 1, lastMeasuredAt: 10)
    let upper = makeObject(label: "shelf", confidence: 0.9,
                           center: SIMD3(0, 0.9, 1), extents: SIMD3(0.9, 0.3, 0.35),
                           firstSeenAt: 2, lastMeasuredAt: 10)
    check(!RegistryCoherence.shouldMerge(lower, upper),
          "stacked shelf levels still don't merge")

    // (d) the laundry pile: unlabeled textile-hinted box ON the couch seat —
    // v2's center gate would have swallowed it; v3 links it as a CHILD.
    let couch = makeObject(label: "couch", confidence: 0.95,
                           center: SIMD3(0, 0.45, 0), extents: SIMD3(2.0, 0.9, 0.9),
                           firstSeenAt: 1, lastMeasuredAt: 10)
    var laundry = makeObject(center: SIMD3(0.15, 0.635, 0.05),   // bottom at seat
                             extents: SIMD3(0.555, 0.37, 0.216),
                             firstSeenAt: 3, lastMeasuredAt: 10)
    laundry.classHint = "Textile"
    check(RegistryCoherence.isChild(laundry, of: couch), "laundry qualifies as child")
    var linked = [couch, laundry]
    RegistryCoherence.assignSupportLinks(&linked)
    check(linked[1].parentId == couch.id, "support pass links laundry → couch")
    check(!RegistryCoherence.shouldMerge(linked[0], linked[1]),
          "parent/child pair never merges")
    let (afterMerge, _) = RegistryCoherence.mergePass(linked)
    check(afterMerge.count == 2, "merge pass preserves the child",
          "\(afterMerge.count)")

    // (e) child follows a merged-away parent to the survivor.
    var childOfB = makeObject(center: SIMD3(1.0, 0.87, -2.0),
                              extents: SIMD3(0.4, 0.2, 0.3),
                              firstSeenAt: 3, lastMeasuredAt: 10)
    childOfB.parentId = halfB.id
    let (survived, aliases2) = RegistryCoherence.mergePass([halfA, halfB, childOfB])
    check(survived.count == 2, "couch halves merged, child kept")
    if let child = survived.first(where: { $0.id == childOfB.id }) {
        check(child.parentId == aliases2[halfB.id],
              "child repointed to the surviving couch")
    } else {
        check(false, "child survived the merge pass")
    }

    // (f) display: unlabeled children stay internal; parents outrank children.
    var display = [couch, laundry]
    display[1].parentId = couch.id
    display[1].established = true            // established BUT unlabeled child
    RegistryCoherence.assignDisplayWorthiness(&display)
    check(display[0].displayWorthy, "parent couch displays")
    check(!display[1].displayWorthy, "unlabeled child stays internal (+N on it)")
    display[1].label = "shirt"
    RegistryCoherence.assignDisplayWorthiness(&display)
    check(display[1].displayWorthy, "labeled child earns its own chip")

    // (g) priors sanity: token normalization + scale class.
    check(FormPriors.furnitureToken(for: "Brown Leather Couch") == "couch",
          "token match inside a longer label")
    check(FormPriors.scaleClass(for: "water bottle") == .small,
          "bottles stay small-tier")
    check(FormPriors.scaleClass(for: "sofa") == .furniture, "sofa is furniture-tier")
}

// MARK: 12. Keyframe math — timestamp-true projection/backprojection + motion

do {
    print("[12] keyframe math (PERCEPTION_V3 §6)")

    // Camera at origin looking down -Z (identity transform), 1920×1440 frame.
    let intrinsics = simd_float3x3(SIMD3<Float>(1500, 0, 0),
                                   SIMD3<Float>(0, 1500, 0),
                                   SIMD3<Float>(960, 720, 1))
    let resolution = CGSize(width: 1920, height: 1440)
    let pose = matrix_identity_float4x4
    let obb = OrientedBox(center: SIMD3(0, 0, -2), extents: SIMD3(0.4, 0.4, 0.4), yaw: 0)

    // (a) projection: centered box → centered rect of the right size.
    let rect = KeyframeGeometry.projectRect(obb: obb, cameraTransform: pose,
                                            intrinsics: intrinsics,
                                            imageResolution: resolution)
    check(rect != nil, "projectRect produced")
    if let rect {
        check(approx(Float(rect.midX), 0.5, tol: 0.01), "rect centered X",
              "\(rect.midX)")
        check(approx(Float(rect.midY), 0.5, tol: 0.01), "rect centered Y",
              "\(rect.midY)")
        check(approx(Float(rect.width), 0.174, tol: 0.02), "rect width",
              "\(rect.width)")

        // (b) round trip: flat depth at 2 m under that rect → world OBB back
        // at the object (fronto-parallel plane: thin in Z by construction).
        let grid = DepthGrid(width: 256, height: 192,
                             depths: [Float16](repeating: Float16(2.0),
                                               count: 256 * 192),
                             confidences: nil)
        let back = KeyframeGeometry.backprojectBox(rect, depth: grid,
                                                   cameraTransform: pose,
                                                   intrinsics: intrinsics,
                                                   imageResolution: resolution)
        check(back != nil, "backprojectBox produced")
        if let back {
            check(approx(back.center.z, -2.0, tol: 0.03), "backprojected depth",
                  "\(back.center.z)")
            check(approx(back.center.x, 0, tol: 0.05)
                    && approx(back.center.y, 0, tol: 0.05),
                  "backprojected center on-axis")
            check(back.extents.x > 0.25 && back.extents.x < 0.5,
                  "backprojected width sane", "\(back.extents.x)")
        }
    }

    // (c) behind the camera → nil.
    let behind = OrientedBox(center: SIMD3(0, 0, 2), extents: SIMD3(0.4, 0.4, 0.4),
                             yaw: 0)
    check(KeyframeGeometry.projectRect(obb: behind, cameraTransform: pose,
                                       intrinsics: intrinsics,
                                       imageResolution: resolution) == nil,
          "behind-camera box projects to nil")

    // (d) motion gate: still → not blocked; a fast pan → blocked.
    let still = MotionGate.measure(previous: pose, current: pose, dt: 0.1)
    check(!still.blocked, "still camera passes the gate")
    let c = cos(Float(0.12)), s = sin(Float(0.12))
    let panned = simd_float4x4(SIMD4<Float>(c, 0, -s, 0),
                               SIMD4<Float>(0, 1, 0, 0),
                               SIMD4<Float>(s, 0, c, 0),
                               SIMD4<Float>(0, 0, 0, 1))
    let pan = MotionGate.measure(previous: pose, current: panned, dt: 0.1)
    check(approx(pan.angularVelocity, 1.2, tol: 0.05), "angular velocity measured",
          "\(pan.angularVelocity)")
    check(pan.blocked, "fast pan blocked")
}

// MARK: 13. Assembly fit — couch → seat/backrest/armrests, table → top/base

do {
    print("[13] assembly fit (PERCEPTION_V3 §3/§4)")

    // Synthetic couch: 2.0 × 0.9 × 0.8 m at yaw 0.35, center (1, 0.4, −2).
    // Local frame: u along length, w along depth (+w = rear), b up from base.
    // Seat 0.45 m; backrest w ∈ [0.18, 0.45] to full height; armrests at the
    // u-ends to 0.62 m; a front skirt face. ~2.6 k points, 4 mm noise.
    let cYaw: Float = 0.35
    let cCos = cos(cYaw), cSin = sin(cYaw)
    let couchCenter = SIMD3<Float>(1, 0.4, -2)
    func couchWorld(u: Float, b: Float, w: Float) -> SIMD3<Float> {
        SIMD3(couchCenter.x + cCos * u + cSin * w,
              b,                                        // bottom at y = 0
              couchCenter.z - cSin * u + cCos * w)
    }
    var couchPts: [SIMD3<Float>] = []
    for _ in 0..<900 {   // seat surface (the dense horizontal band)
        let u = Float.random(in: -0.70...0.70, using: &rng)
        let w = Float.random(in: -0.45...0.18, using: &rng)
        couchPts.append(couchWorld(u: u, b: 0.45 + gauss(0.004), w: w))
    }
    for _ in 0..<500 {   // backrest inner face + top
        let u = Float.random(in: -1.0...1.0, using: &rng)
        if Bool.random(using: &rng) {
            let b = Float.random(in: 0.45...0.80, using: &rng)
            couchPts.append(couchWorld(u: u, b: b, w: 0.18 + gauss(0.004)))
        } else {
            let w = Float.random(in: 0.18...0.45, using: &rng)
            couchPts.append(couchWorld(u: u, b: 0.80 + gauss(0.004), w: w))
        }
    }
    for side in [Float(-1), 1] {   // armrests: outer band, tops at 0.62
        for _ in 0..<220 {
            let u = side * Float.random(in: 0.72...1.0, using: &rng)
            if Bool.random(using: &rng) {
                let w = Float.random(in: -0.45...0.18, using: &rng)
                couchPts.append(couchWorld(u: u, b: 0.62 + gauss(0.004), w: w))
            } else {
                let b = Float.random(in: 0.45...0.62, using: &rng)
                couchPts.append(couchWorld(u: u, b: b, w: -0.45 + gauss(0.004)))
            }
        }
    }
    for _ in 0..<300 {   // front skirt (base → seat)
        let u = Float.random(in: -0.7...0.7, using: &rng)
        let b = Float.random(in: 0.02...0.45, using: &rng)
        couchPts.append(couchWorld(u: u, b: b, w: -0.45 + gauss(0.004)))
    }

    let couchFit = FormFitter.fit(points: couchPts, classLabel: "couch",
                                  arcCoverage: 0.25)
    check(couchFit != nil, "couch fit produced")
    if let fit = couchFit {
        check(fit.form.kind == .assembly, "kind == assembly", "\(fit.form.kind)")
        check(fit.form.source == .measured, "source == measured")
        let prims = fit.form.primitives ?? []
        let names = Set(prims.map(\.name))
        check(names.contains("seat"), "seat primitive", "\(names)")
        check(names.contains("backrest"), "backrest primitive", "\(names)")
        check(names.contains("armrest_left") && names.contains("armrest_right"),
              "both armrests", "\(names)")
        if let seat = fit.form.primitive(named: "seat") {
            let top = seat.obb.center.y + seat.obb.extents.y / 2
            check(approx(top, 0.45, tol: 0.06), "seat top ≈ 0.45 m", "\(top)")
        }
        if let back = fit.form.primitive(named: "backrest") {
            let top = back.obb.center.y + back.obb.extents.y / 2
            check(approx(top, 0.80, tol: 0.06), "backrest top ≈ 0.80 m", "\(top)")
        }
        check(approx(fit.form.dimensions["width"] ?? -1, 2.0, tol: 0.12),
              "length ≈ 2.0 m", "\(fit.form.dimensions["width"] ?? -1)")
        check(fit.form.residual < 0.05, "assembly residual sane",
              "\(fit.form.residual)")

        // Affordances against the fitted assembly (v3 §4).
        var couchObj = makeObject(label: "couch", confidence: 0.95,
                                  center: fit.obb?.center ?? couchCenter,
                                  extents: fit.obb?.extents ?? SIMD3(2, 0.8, 0.9),
                                  yaw: fit.obb?.yaw ?? cYaw,
                                  firstSeenAt: 1, lastMeasuredAt: 10)
        couchObj.form = fit.form
        if let seatRegion = RegistryCoherence.affordanceSlice("seat", of: couchObj) {
            let top = seatRegion.center.y + seatRegion.extents.y / 2
            check(approx(top, 0.45, tol: 0.07), "seat affordance lands on seat top",
                  "\(top)")
        } else { check(false, "seat affordance resolves") }
        // Camera near the LEFT end → plain "armrest" picks the left one.
        let camNearLeft = couchWorld(u: -1.4, b: 1.2, w: -1.2)
        if let arm = RegistryCoherence.affordanceSlice("armrest", of: couchObj,
                                                       cameraPosition: camNearLeft),
           let leftArm = fit.form.primitive(named: "armrest_left"),
           let rightArm = fit.form.primitive(named: "armrest_right") {
            let dLeft = simd_distance(arm.center, leftArm.obb.center)
            let dRight = simd_distance(arm.center, rightArm.obb.center)
            check(dLeft < dRight, "armrest affordance picks the nearer arm")
            let top = arm.center.y + arm.extents.y / 2
            check(approx(top, 0.62, tol: 0.07), "armrest affordance at arm top",
                  "\(top)")
        } else { check(false, "armrest affordance resolves") }
        // Front edge: strip on the CAMERA side of the seat's depth axis.
        let camFront = couchWorld(u: 0, b: 1.0, w: -2.0)
        if let edge = RegistryCoherence.affordanceSlice("edge", of: couchObj,
                                                        cameraPosition: camFront) {
            check(simd_distance(edge.center, camFront)
                    < simd_distance(couchObj.obb.center, camFront),
                  "front edge lands on the camera side")
        } else { check(false, "edge affordance resolves") }
        if let center = RegistryCoherence.affordanceSlice("middle", of: couchObj) {
            check(center.extents.x < couchObj.obb.extents.x * 0.6,
                  "center patch is a patch, not the whole top")
        } else { check(false, "center affordance resolves") }
    }

    // Sparse cloud → no assembly; falls back to the plain box fit.
    let sparse = Array(couchPts.prefix(150))
    if let fallback = FormFitter.fit(points: sparse, classLabel: "couch",
                                     arcCoverage: 0.1) {
        check(fallback.form.kind == .box, "sparse couch falls back to box",
              "\(fallback.form.kind)")
    } else { check(false, "sparse couch still fits a box") }

    // Synthetic table: 1.4 × 0.8 × 0.74, dense top at 0.74, four legs.
    var tablePts: [SIMD3<Float>] = []
    for _ in 0..<800 {
        let x = Float.random(in: -0.7...0.7, using: &rng)
        let z = Float.random(in: -0.4...0.4, using: &rng)
        tablePts.append(SIMD3(3 + x, 0.74 + gauss(0.004), 1 + z))
    }
    for lx in [Float(-0.63), 0.63] {
        for lz in [Float(-0.33), 0.33] {
            for _ in 0..<60 {
                let b = Float.random(in: 0.02...0.70, using: &rng)
                tablePts.append(SIMD3(3 + lx + gauss(0.006), b, 1 + lz + gauss(0.006)))
            }
        }
    }
    let tableFit = FormFitter.fit(points: tablePts, classLabel: "table",
                                  arcCoverage: 0.3)
    check(tableFit?.form.kind == .assembly, "table → assembly",
          "\(String(describing: tableFit?.form.kind))")
    if let top = tableFit?.form.primitive(named: "top") {
        let bottom = top.obb.center.y - top.obb.extents.y / 2
        check(bottom > 0.5, "tabletop slab starts high", "\(bottom)")
    } else { check(false, "table has a top primitive") }
}

// MARK: 14. Mesh-cluster evidence — same-entity through the seat cluster

do {
    print("[14] mesh-cluster evidence (PERCEPTION_V3 §2)")

    // One seat cluster spanning the whole couch footprint.
    let seatCluster = FurnitureCluster(kind: .seat,
                                       xzMin: SIMD2(-1.2, -2.5),
                                       xzMax: SIMD2(1.2, -1.6),
                                       yMin: 0.05, yMax: 0.85, faceCount: 900)

    // Two UNLABELED fragments (no class token — the same-class branch can't
    // help), centers 1.1 m apart, both inside the cluster.
    let fragA = makeObject(center: SIMD3(-0.55, 0.4, -2.0),
                           extents: SIMD3(1.0, 0.7, 0.8),
                           firstSeenAt: 1, lastMeasuredAt: 10)
    let fragB = makeObject(center: SIMD3(0.55, 0.4, -2.0),
                           extents: SIMD3(1.0, 0.7, 0.8),
                           firstSeenAt: 2, lastMeasuredAt: 10)
    check(!RegistryCoherence.shouldMerge(fragA, fragB),
          "unlabeled fragments alone don't merge (no class evidence)")
    check(RegistryCoherence.sameClusterEvidence(fragA, fragB,
                                                clusters: [seatCluster]),
          "the seat cluster says they are one thing")
    let (merged, _) = RegistryCoherence.mergePass([fragA, fragB], alsoMerge: {
        RegistryCoherence.sameClusterEvidence($0, $1, clusters: [seatCluster])
    })
    check(merged.count == 1, "cluster evidence merges them", "\(merged.count)")

    // The laundry-sized box inside the same cluster does NOT cluster-merge
    // (volume ratio guard) — it reaches the couch as a CHILD instead.
    let laundry = makeObject(center: SIMD3(0.1, 0.55, -2.0),
                             extents: SIMD3(0.5, 0.3, 0.25),
                             firstSeenAt: 3, lastMeasuredAt: 10)
    check(!RegistryCoherence.sameClusterEvidence(fragA, laundry,
                                                 clusters: [seatCluster]),
          "small stuff on the seat never cluster-merges")

    // A labeled BOX-template thing (a TV on a stand inside the footprint)
    // never cluster-merges either.
    let tv = makeObject(label: "tv", confidence: 0.9,
                        center: SIMD3(0.0, 0.45, -1.8),
                        extents: SIMD3(1.0, 0.6, 0.7),
                        firstSeenAt: 4, lastMeasuredAt: 10)
    check(!RegistryCoherence.sameClusterEvidence(fragA, tv,
                                                 clusters: [seatCluster]),
          "labeled box-class things are exempt from cluster merges")

    // Outside the cluster → no evidence.
    let elsewhere = makeObject(center: SIMD3(3.0, 0.4, -2.0),
                               extents: SIMD3(1.0, 0.7, 0.8),
                               firstSeenAt: 5, lastMeasuredAt: 10)
    check(!RegistryCoherence.sameClusterEvidence(fragA, elsewhere,
                                                 clusters: [seatCluster]),
          "outside the footprint → no cluster evidence")
}

// MARK: 15. Ask planner — evidence specs + dossier freshness

do {
    print("[15] ask planner (PERCEPTION_V3 §7)")

    let keys = AskPlanner.evidenceSpec(
        for: "what language are the letters on my keyboard?")
    check(keys.wantedAttributes.contains("language")
            && keys.wantedAttributes.contains("textContent"),
          "language ask wants language + text", "\(keys.wantedAttributes)")
    check(keys.isQuestion, "reads as a question")

    let color = AskPlanner.evidenceSpec(for: "make something matching the couch color")
    check(color.wantedAttributes == ["colors"], "color ask wants colors",
          "\(color.wantedAttributes)")

    let geometry = AskPlanner.evidenceSpec(for: "put a lamp on the armrest edge")
    check(geometry.needsGeometry, "edge/armrest ask needs geometry")
    check(geometry.wantedAttributes.isEmpty, "no dossier fields for pure placement")
    check(!geometry.wantsEvidence, "pure placement asks skip the focus pass")

    let plain = AskPlanner.evidenceSpec(for: "explain the water cycle")
    check(!plain.isQuestion && plain.wantedAttributes.isEmpty,
          "imperative topic ask wants no evidence")

    // Dossier freshness: answers only when every wanted field is present + fresh.
    var keyboard = makeObject(label: "keyboard", confidence: 0.9,
                              center: SIMD3(0, 0.75, -1),
                              extents: SIMD3(0.36, 0.03, 0.13),
                              firstSeenAt: 1, lastMeasuredAt: 100)
    keyboard.attributes = ObjectAttributes(colors: ["blue", "black"],
                                           textContent: ["QWERTY"],
                                           language: "English")
    keyboard.attributesUpdatedAt = 95
    check(AskPlanner.dossierAnswers(keyboard, wanted: ["language", "textContent"],
                                    now: 100),
          "fresh dossier answers the language ask")
    check(!AskPlanner.dossierAnswers(keyboard, wanted: ["brand"], now: 100),
          "missing field → dossier does not answer")
    check(!AskPlanner.dossierAnswers(keyboard, wanted: ["language"], now: 200),
          "stale dossier does not answer")

    let line = AskPlanner.dossierLine(label: keyboard.label,
                                      attributes: keyboard.attributes)
    check(line == "keyboard — blue/black · “QWERTY” · English",
          "dossier line reads like the founder's acceptance case",
          line ?? "nil")

    // Dossier merge semantics (v3 §5): union bounded, newest-first, no thrash.
    var dossier = ObjectAttributes(colors: ["blue"])
    dossier.merge(ObjectAttributes(colors: ["Blue", "black"], language: "English"))
    check(dossier.colors == ["Blue", "black"], "case-insensitive union, newest kept",
          "\(dossier.colors ?? [])")
    check(dossier.language == "English", "scalar filled")
    dossier.merge(ObjectAttributes(brand: "Keychron"))
    check(dossier.language == "English" && dossier.brand == "Keychron",
          "later merge keeps earlier scalars")
}

print("\n\(passed) passed, \(failed) failed")
exit(failed == 0 ? 0 : 1)
