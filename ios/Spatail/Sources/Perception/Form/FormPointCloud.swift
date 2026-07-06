// FormPointCloud.swift — Perception v2 Form Engine, stage 2: multi-view fusion.
//
// One instance per registry object: accumulates world-space depth points across
// frames into a VOXEL-DOWNSAMPLED cloud (4 mm voxels) with a HARD CAP (~20k points,
// oldest voxels evicted beyond the cap — spec §0 law 3: no unbounded buffers), and
// tracks ARC COVERAGE: a view-direction azimuth histogram around the object's
// gravity axis (ARKit world +Y — worldAlignment is .gravity), so fit confidence can
// reflect single-sided vs. surrounded observation.
//
// Confined to the Form Engine's serial queue — no locks needed, never touched from
// main. Pure Foundation + simd so the exact shipped logic runs in the off-device
// harness.

import Foundation
import simd

final class FormPointCloud {

    // MARK: Tunables

    /// Voxel edge (m). 4 mm ≈ the useful resolution of upsampled iPhone dToF —
    /// the SMALL-tier default. Furniture (v3 §3) uses a coarser tier: 4 mm
    /// voxels capped at 20 k can't span a couch.
    static let voxelSize: Float = 0.004
    /// Hard cap on stored voxels; beyond it the OLDEST voxels are evicted.
    static let maxPoints = 20_000
    /// Azimuth histogram bins (10° each) around the gravity axis.
    static let azimuthBins = 36
    /// A new measurement center this far from the cloud's running center means the
    /// object MOVED — the accumulated cloud is stale and resets. Furniture tiers
    /// pass a class-scaled distance: half-couch detections legitimately report
    /// centers ~0.7 m apart and must not reset the accumulation.
    static let resetJumpDistance: Float = 0.25

    // MARK: Tier configuration (instance — v3 §3)

    let voxelSize: Float
    let maxPoints: Int
    let resetJumpDistance: Float

    /// Small-object tier by default; the Form Engine passes the furniture tier
    /// (2 cm voxels, 30 k cap, class-scaled reset) for furniture-scale classes.
    init(voxelSize: Float = FormPointCloud.voxelSize,
         maxPoints: Int = FormPointCloud.maxPoints,
         resetJumpDistance: Float = FormPointCloud.resetJumpDistance) {
        self.voxelSize = voxelSize
        self.maxPoints = maxPoints
        self.resetJumpDistance = resetJumpDistance
    }

    // MARK: Storage

    private struct Entry {
        var point: SIMD3<Float>
        var stamp: UInt64
    }

    /// Quantized voxel key → representative point (latest wins inside a voxel).
    private var voxels: [Int64: Entry] = [:]
    /// Insertion-order ring for O(1) amortized eviction; stale entries (voxel
    /// re-touched later with a newer stamp) are skipped lazily. Bounded: compacted
    /// whenever it grows past 2× the voxel count.
    private var evictionRing: [(key: Int64, stamp: UInt64)] = []
    private var stampCounter: UInt64 = 0

    /// Running centroid of stored points (incremental; good enough for azimuth).
    private var centroidSum = SIMD3<Float>(repeating: 0)
    private(set) var count = 0

    /// View-direction azimuth histogram (counts of contributing frames per bin).
    private var azimuthHistogram = [UInt32](repeating: 0, count: FormPointCloud.azimuthBins)

    /// Uptime timestamp of the last accepted insertion (pruning signal).
    private(set) var lastTouchedAt: TimeInterval = 0

    // MARK: Reads

    var centroid: SIMD3<Float> {
        count > 0 ? centroidSum / Float(count) : .zero
    }

    /// Immutable snapshot of the fused points (input to FormFitter).
    var snapshot: [SIMD3<Float>] {
        voxels.values.map(\.point)
    }

    /// Fraction (0–1) of azimuth bins the camera has observed the object from.
    var arcCoverage: Float {
        let filled = azimuthHistogram.lazy.filter { $0 > 0 }.count
        return Float(filled) / Float(Self.azimuthBins)
    }

    // MARK: Writes

    /// Fuse one frame's points. `viewpoint` is the camera position (world) — it
    /// feeds the azimuth histogram; `measurementCenter` is this frame's estimate of
    /// the object center (jump detection); `now` is the frame's uptime timestamp.
    func insert(points: [SIMD3<Float>], viewpoint: SIMD3<Float>,
                measurementCenter: SIMD3<Float>, now: TimeInterval) {
        guard !points.isEmpty else { return }

        // Object moved → stale cloud, start over (also clears arc coverage:
        // the old viewing arc no longer describes the new pose).
        if count > 0, simd_distance(centroid, measurementCenter) > resetJumpDistance {
            reset()
        }

        for p in points {
            guard p.x.isFinite, p.y.isFinite, p.z.isFinite else { continue }
            let key = voxelKey(p)
            stampCounter += 1
            if var entry = voxels[key] {
                // Re-touched voxel: refresh point + stamp (kept fresh vs eviction).
                centroidSum += p - entry.point
                entry.point = p
                entry.stamp = stampCounter
                voxels[key] = entry
            } else {
                voxels[key] = Entry(point: p, stamp: stampCounter)
                centroidSum += p
                count += 1
            }
            evictionRing.append((key, stampCounter))
        }

        // Azimuth of the VIEW DIRECTION around the gravity axis (world +Y),
        // measured about the cloud's centroid.
        let toCamera = viewpoint - centroid
        let planar = SIMD2<Float>(toCamera.x, toCamera.z)
        if simd_length(planar) > 0.05 {
            let azimuth = atan2(planar.y, planar.x)            // -π…π
            var bin = Int((azimuth + .pi) / (2 * .pi) * Float(Self.azimuthBins))
            bin = min(max(bin, 0), Self.azimuthBins - 1)
            azimuthHistogram[bin] &+= 1
        }

        enforceCap()
        compactRingIfNeeded()
        lastTouchedAt = now
    }

    func reset() {
        voxels.removeAll(keepingCapacity: true)
        evictionRing.removeAll(keepingCapacity: true)
        centroidSum = .zero
        count = 0
        azimuthHistogram = [UInt32](repeating: 0, count: Self.azimuthBins)
    }

    // MARK: Internals

    /// Evict OLDEST voxels while over the cap (lazy-deletion ring: an entry is live
    /// only when its stamp still matches the stored voxel's).
    private func enforceCap() {
        var head = 0
        while count > maxPoints, head < evictionRing.count {
            let (key, stamp) = evictionRing[head]
            head += 1
            guard let entry = voxels[key], entry.stamp == stamp else { continue }
            voxels[key] = nil
            centroidSum -= entry.point
            count -= 1
        }
        if head > 0 { evictionRing.removeFirst(head) }
    }

    /// The ring accumulates one entry per inserted point (re-touches included);
    /// drop entries whose stamp is stale once it doubles the live voxel count.
    private func compactRingIfNeeded() {
        guard evictionRing.count > max(2 * count, 1024) else { return }
        evictionRing = evictionRing.filter { voxels[$0.key]?.stamp == $0.stamp }
    }

    /// 21-bit signed quantized coordinates packed into one Int64 key
    /// (±2^20 voxels × 4 mm ≈ ±4.2 km — beyond any room).
    private func voxelKey(_ p: SIMD3<Float>) -> Int64 {
        let ix = Int64(floor(p.x / voxelSize)) & 0x1F_FFFF
        let iy = Int64(floor(p.y / voxelSize)) & 0x1F_FFFF
        let iz = Int64(floor(p.z / voxelSize)) & 0x1F_FFFF
        return ix | (iy << 21) | (iz << 42)
    }
}
