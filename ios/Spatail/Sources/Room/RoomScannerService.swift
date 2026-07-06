// RoomScannerService.swift — the room-truth producer (LIVE_BRAIN_SPEC §1.2 surfaces).
//
// Listens to the hub's ARSessionDelegate fan-out and turns ARKit's anchors into the
// merged `RoomSurface` list every other module consumes (uplink room.update, the
// placement solver's RoomModel, the Truth Overlay, support-surface linking):
//
//   • ARPlaneAnchors are the surface source: classification (.floor/.table/.wall/…)
//     when the device supports it, alignment + height inference otherwise. Horizontal
//     planes of the same kind at the same height merge into ONE RoomSurface (one real
//     table = one surface — spec: "replaces the per-anchor convex slabs"), with the
//     merged boundary as the convex hull of every member's boundary vertices.
//     Vertical planes (walls/doors/windows) stay one-surface-per-anchor: their
//     boundaries live in different planes and merging geometry would fabricate
//     geometry ARKit never measured.
//   • ARMeshAnchors (LiDAR) feed the coverage instrument: mesh extraction runs on a
//     dedicated background queue (spec §0 law 2 — NEVER on main), single-flight
//     latched, publishing an immutable (area, bbox) snapshot back to main. Coverage
//     is honest-by-construction: mapped mesh area over the area the room's own
//     bounding box implies, so it climbs as the scan fills the space it has seen.
//
// Surface IDs are stable across updates (seeded by the cluster's smallest member
// anchor UUID) so diff renderers and supportSurfaceId links don't churn.
//
// Threading: anchor bookkeeping + plane merging run on main — plane boundaries are
// tiny (tens of vertices); the merge is microseconds. The ONLY heavy thing here is
// walking LiDAR mesh buffers, and that is background-only.

import Foundation
import ARKit
import simd

@MainActor
final class RoomScannerService: NSObject, ObservableObject {

    // MARK: Published truth

    /// Merged room surfaces, world space. Everyone reads these.
    @Published private(set) var surfaces: [RoomSurface] = []

    /// 0–100. LiDAR: mesh area vs. the area the scanned bounding box implies.
    /// No LiDAR: plane area vs. the same estimate from plane extents.
    @Published private(set) var coveragePercent: Double = 0

    /// Furniture-classified mesh regions (v3 §2) — same-entity evidence for the
    /// registry's merge pass. EVIDENCE ONLY per canon: clusters group and
    /// propose extent; dimensions come from fused depth, never the mesh.
    @Published private(set) var furnitureClusters: [FurnitureCluster] = []

    /// True when the device reconstructs a classified LiDAR mesh (spec §1.2 source).
    /// Instance tag — the 2026-07-03 field sessions showed the console's
    /// scanner publishing surfaces while the UI's scanner showed none. Every
    /// truth-line and observer carries this tag until the split is explained.
    nonisolated let tag = String(UUID().uuidString.prefix(4))

    nonisolated let usingLiDAR =
        ARWorldTrackingConfiguration.supportsSceneReconstruction(.meshWithClassification)

    // MARK: Tunables

    /// Horizontal planes of the same kind merge when their heights are this close…
    private static let mergeHeightTolerance: Float = 0.04
    /// …and their XZ bounding boxes are within this gap (or overlap).
    private static let mergeGapXZ: Float = 0.25
    /// Plane-merge rebuild debounce — anchors update at ARKit rate, surfaces don't need to.
    private static let rebuildDebounce: TimeInterval = 0.35
    /// Background mesh coverage pass cadence ceiling.
    private static let meshPassInterval: TimeInterval = 1.5
    /// Confidence assigned to ARKit-classified planes / inferred kinds / unknowns.
    private static let confidenceClassified: Float = 0.9
    private static let confidenceInferred: Float = 0.45
    private static let confidenceUnknown: Float = 0.25

    // MARK: State

    private var planeAnchors: [UUID: ARPlaneAnchor] = [:]
    private var meshAnchors: [UUID: ARMeshAnchor] = [:]
    private var rebuildScheduled = false

    /// Dedicated background lane for LiDAR mesh walking (law 2: never on main).
    private let meshQueue = DispatchQueue(label: "com.spatail.room.mesh-extract",
                                          qos: .utility)
    private let meshPassBusy = NetBox(false)
    private var lastMeshPassAt: TimeInterval = 0
    /// Latest immutable mesh snapshot (published by the background pass).
    private var meshSnapshot: MeshSnapshot?

    /// Immutable output of one background mesh pass.
    private struct MeshSnapshot {
        let totalAreaM2: Float
        let bboxMin: SIMD3<Float>
        let bboxMax: SIMD3<Float>
    }

    // MARK: - Anchor intake (hub fan-out, main queue)

    private func absorb(_ anchors: [ARAnchor]) {
        var planesChanged = false
        var meshChanged = false
        for anchor in anchors {
            if let plane = anchor as? ARPlaneAnchor {
                planeAnchors[plane.identifier] = plane
                planesChanged = true
            } else if let mesh = anchor as? ARMeshAnchor {
                meshAnchors[mesh.identifier] = mesh
                meshChanged = true
            }
        }
        if planesChanged { scheduleRebuild() }
        if meshChanged { scheduleMeshPassIfDue() }
    }

    private func drop(_ anchors: [ARAnchor]) {
        var planesChanged = false
        for anchor in anchors {
            if planeAnchors.removeValue(forKey: anchor.identifier) != nil {
                planesChanged = true
            }
            meshAnchors.removeValue(forKey: anchor.identifier)
        }
        if planesChanged { scheduleRebuild() }
    }

    // MARK: - Surface rebuild (main; tiny geometry, debounced)

    private func scheduleRebuild() {
        guard !rebuildScheduled else { return }
        rebuildScheduled = true
        Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(Self.rebuildDebounce * 1_000_000_000))
            guard let self else { return }
            self.rebuildScheduled = false
            self.rebuildSurfaces()
        }
    }

    private struct ClassifiedPlane {
        let anchor: ARPlaneAnchor
        let kind: SurfaceKind
        let inferred: Bool          // kind guessed from alignment+height, not ARKit
        let worldBoundary: [SIMD3<Float>]
        let y: Float                // dominant plane height (world Y)
        let areaM2: Float
        let xzMin: SIMD2<Float>
        let xzMax: SIMD2<Float>
    }

    private func rebuildSurfaces() {
        let planes = classifyPlanes(Array(planeAnchors.values))

        var merged: [RoomSurface] = []
        // Horizontal kinds merge into one surface per (kind, height, adjacency) cluster.
        let horizontalKinds: [SurfaceKind] = [.floor, .table, .seat, .ceiling, .unknown]
        for kind in horizontalKinds {
            let members = planes.filter { $0.kind == kind && Self.isHorizontal($0.anchor) }
            merged.append(contentsOf: mergeHorizontal(members, kind: kind))
        }
        // Vertical planes: one surface each (their boundaries live in distinct planes).
        for plane in planes where !Self.isHorizontal(plane.anchor) {
            merged.append(RoomSurface(
                id: "surf-" + plane.anchor.identifier.uuidString.lowercased(),
                kind: plane.kind,
                boundary: plane.worldBoundary,
                y: plane.y,
                areaM2: plane.areaM2,
                confidence: confidence(for: plane),
                anchorIdentifiers: [plane.anchor.identifier]))
        }

        surfaces = merged.sorted { $0.areaM2 > $1.areaM2 }
        updateCoverage()
    }

    private func classifyPlanes(_ anchors: [ARPlaneAnchor]) -> [ClassifiedPlane] {
        // Floor estimate for inference on devices without plane classification:
        // the lowest horizontal plane is the floor candidate.
        let horizontalYs = anchors
            .filter { $0.alignment == .horizontal }
            .map { $0.transform.columns.3.y }
        let floorY = horizontalYs.min()

        return anchors.compactMap { anchor in
            let boundary = anchor.geometry.boundaryVertices.map { v in
                let w = anchor.transform * SIMD4<Float>(v.x, v.y, v.z, 1)
                return SIMD3<Float>(w.x, w.y, w.z)
            }
            guard boundary.count >= 3 else { return nil }

            var lo = SIMD2<Float>(repeating: .greatestFiniteMagnitude)
            var hi = SIMD2<Float>(repeating: -.greatestFiniteMagnitude)
            var ySum: Float = 0
            for p in boundary {
                lo = simd_min(lo, SIMD2(p.x, p.z))
                hi = simd_max(hi, SIMD2(p.x, p.z))
                ySum += p.y
            }
            let isVertical = anchor.alignment == .vertical
            let y: Float = isVertical
                ? ySum / Float(boundary.count)                      // mid-height
                : anchor.transform.columns.3.y                      // plane height
            let area = anchor.planeExtent.width * anchor.planeExtent.height

            let (kind, inferred) = Self.kind(for: anchor, floorY: floorY, y: y)
            return ClassifiedPlane(anchor: anchor, kind: kind, inferred: inferred,
                                   worldBoundary: boundary, y: y,
                                   areaM2: Float(area),
                                   xzMin: lo, xzMax: hi)
        }
    }

    /// ARKit classification when supported; alignment + height inference otherwise.
    private static func kind(for anchor: ARPlaneAnchor, floorY: Float?,
                             y: Float) -> (SurfaceKind, inferred: Bool) {
        if ARPlaneAnchor.isClassificationSupported {
            switch anchor.classification {
            case .floor:   return (.floor, false)
            case .ceiling: return (.ceiling, false)
            case .table:   return (.table, false)
            case .seat:    return (.seat, false)
            case .wall:    return (.wall, false)
            case .door:    return (.door, false)
            case .window:  return (.window, false)
            case .none:    break
            @unknown default: break
            }
        }
        // Inference path (older devices / unclassified planes).
        if anchor.alignment == .vertical { return (.wall, true) }
        guard let floorY else { return (.unknown, true) }
        let above = y - floorY
        if above < 0.15 { return (.floor, true) }
        if above > 0.25 && above < 1.3 { return (.table, true) }
        if above > 2.0 { return (.ceiling, true) }
        return (.unknown, true)
    }

    private static func isHorizontal(_ anchor: ARPlaneAnchor) -> Bool {
        anchor.alignment == .horizontal
    }

    private func confidence(for plane: ClassifiedPlane) -> Float {
        if plane.kind == .unknown { return Self.confidenceUnknown }
        return plane.inferred ? Self.confidenceInferred : Self.confidenceClassified
    }

    /// Cluster same-kind horizontal planes by height + XZ adjacency; each cluster
    /// becomes ONE RoomSurface whose boundary is the XZ convex hull of every member.
    private func mergeHorizontal(_ members: [ClassifiedPlane],
                                 kind: SurfaceKind) -> [RoomSurface] {
        guard !members.isEmpty else { return [] }

        // Union-find-lite: greedy transitive clustering (member counts are tiny).
        var clusterOf = Array(0..<members.count)
        func find(_ i: Int) -> Int {
            var r = i
            while clusterOf[r] != r { r = clusterOf[r] }
            return r
        }
        for i in 0..<members.count {
            for j in (i + 1)..<members.count {
                let a = members[i], b = members[j]
                guard abs(a.y - b.y) <= Self.mergeHeightTolerance else { continue }
                let gapX = max(a.xzMin.x - b.xzMax.x, b.xzMin.x - a.xzMax.x)
                let gapZ = max(a.xzMin.y - b.xzMax.y, b.xzMin.y - a.xzMax.y)
                guard max(gapX, gapZ) <= Self.mergeGapXZ else { continue }
                let (ri, rj) = (find(i), find(j))
                if ri != rj { clusterOf[max(ri, rj)] = min(ri, rj) }
            }
        }

        var groups: [Int: [ClassifiedPlane]] = [:]
        for (i, m) in members.enumerated() { groups[find(i), default: []].append(m) }

        return groups.values.map { cluster in
            // Stable id: the smallest member anchor UUID seeds it.
            let seed = cluster.map { $0.anchor.identifier.uuidString.lowercased() }
                .min() ?? UUID().uuidString.lowercased()
            let totalArea = cluster.reduce(Float(0)) { $0 + $1.areaM2 }
            let y = cluster.reduce(Float(0)) { $0 + $1.y * $1.areaM2 }
                / max(totalArea, 1e-4)

            let hull2 = OrientedBoxFitter.convexHull(
                cluster.flatMap { $0.worldBoundary.map { SIMD2($0.x, $0.z) } })
            let boundary = hull2.map { SIMD3<Float>($0.x, y, $0.y) }

            let best = cluster.max { confidence(for: $0) < confidence(for: $1) }
            return RoomSurface(
                id: "surf-" + seed,
                kind: kind,
                boundary: boundary,
                y: y,
                areaM2: totalArea,
                confidence: best.map(confidence(for:)) ?? Self.confidenceUnknown,
                anchorIdentifiers: cluster.map { $0.anchor.identifier })
        }
    }

    // MARK: - Coverage (LiDAR mesh pass on background; planes as fallback)

    private func scheduleMeshPassIfDue() {
        let now = Date().timeIntervalSince1970
        guard now - lastMeshPassAt >= Self.meshPassInterval else { return }
        // Single-flight latch BEFORE dispatch (law 2 shape, same as inference).
        let latched = meshPassBusy.withLock { busy -> Bool in
            if busy { return false }
            busy = true
            return true
        }
        guard latched else { return }
        lastMeshPassAt = now

        let anchors = Array(meshAnchors.values)
        meshQueue.async { [weak self] in
            let (snapshot, clusters) = Self.extractMeshSnapshot(anchors)   // heavy, background
            Task { @MainActor [weak self] in
                guard let self else { return }
                self.meshPassBusy.set(false)
                if let snapshot { self.meshSnapshot = snapshot }
                self.furnitureClusters = clusters
                self.updateCoverage()
            }
        }
    }

    /// Last (surfaceCount, wholePercent) printed — console truth-line dedupe.
    private var lastLogged: (Int, Int) = (-1, -1)

    private func updateCoverage() {
        defer {
            let now = (surfaces.count, Int(coveragePercent.rounded()))
            if now != lastLogged {
                lastLogged = now
                print("[Room \(tag)] \(now.0) surfaces, coverage \(now.1)% " +
                      "(planes \(planeAnchors.count), mesh \(meshAnchors.count))")
            }
        }
        if usingLiDAR, let snap = meshSnapshot {
            coveragePercent = Self.coverage(mappedArea: snap.totalAreaM2,
                                            bboxMin: snap.bboxMin, bboxMax: snap.bboxMax)
        } else {
            // No mesh: plane area vs. the extent the planes themselves imply.
            var lo = SIMD3<Float>(repeating: .greatestFiniteMagnitude)
            var hi = SIMD3<Float>(repeating: -.greatestFiniteMagnitude)
            var area: Float = 0
            for s in surfaces {
                area += s.areaM2
                for p in s.boundary { lo = simd_min(lo, p); hi = simd_max(hi, p) }
            }
            guard area > 0, lo.x < hi.x else { coveragePercent = 0; return }
            coveragePercent = Self.coverage(mappedArea: area, bboxMin: lo, bboxMax: hi)
        }
    }

    /// Mapped area over the surface area the scanned bounding box implies
    /// (floor + ceiling + four walls of the box) — self-normalizing: the target
    /// grows as the user reveals more room, so the number never lies at 100%
    /// after two seconds of scanning one corner.
    private static func coverage(mappedArea: Float,
                                 bboxMin: SIMD3<Float>,
                                 bboxMax: SIMD3<Float>) -> Double {
        let d = simd_max(bboxMax - bboxMin, SIMD3<Float>(repeating: 0))
        let w = max(d.x, 0.5), h = max(d.y, 0.5), depth = max(d.z, 0.5)
        let implied = 2 * (w * depth) + 2 * (w + depth) * h
        guard implied > 0.1 else { return 0 }
        return Double(min(1, mappedArea / implied)) * 100
    }

    /// Background-only: walk every mesh anchor's triangles, summing area,
    /// growing the world bbox, AND bucketing furniture-classified faces
    /// (.seat/.table) into 0.4 m XZ cells for the cluster pass (v3 §2).
    /// Immutable output; anchors' geometry buffers are read once, nothing is
    /// retained.
    nonisolated private static func extractMeshSnapshot(_ anchors: [ARMeshAnchor])
        -> (MeshSnapshot?, [FurnitureCluster]) {
        guard !anchors.isEmpty else { return (nil, []) }
        var total: Float = 0
        var lo = SIMD3<Float>(repeating: .greatestFiniteMagnitude)
        var hi = SIMD3<Float>(repeating: -.greatestFiniteMagnitude)

        // Per-class cell accumulators (0.4 m XZ grid).
        struct CellStats {
            var faceCount = 0
            var xzMin = SIMD2<Float>(repeating: .greatestFiniteMagnitude)
            var xzMax = SIMD2<Float>(repeating: -.greatestFiniteMagnitude)
            var yMin = Float.greatestFiniteMagnitude
            var yMax = -Float.greatestFiniteMagnitude
        }
        let cellSize: Float = 0.4
        var cells: [SurfaceKind: [SIMD2<Int32>: CellStats]] = [.seat: [:], .table: [:]]

        for anchor in anchors {
            let geometry = anchor.geometry
            let vertices = geometry.vertices
            let faces = geometry.faces
            guard vertices.format == .float3,
                  faces.indexCountPerPrimitive == 3 else { continue }

            let vertexBase = vertices.buffer.contents() + vertices.offset
            let vertexStride = vertices.stride
            let indexBase = faces.buffer.contents()
            let bytesPerIndex = faces.bytesPerIndex
            let transform = anchor.transform

            // Per-face classification source (one uint8 per face) when present.
            var classifyAt: ((Int) -> SurfaceKind?)?
            if let classification = geometry.classification,
               classification.format == .uchar,     // one MTL uchar per face
               classification.count == faces.count {
                let base = classification.buffer.contents() + classification.offset
                let stride = classification.stride
                classifyAt = { face in
                    let raw = base.advanced(by: face * stride)
                        .assumingMemoryBound(to: UInt8.self).pointee
                    switch ARMeshClassification(rawValue: Int(raw)) {
                    case .seat: return .seat
                    case .table: return .table
                    default: return nil
                    }
                }
            }

            func vertex(_ index: Int) -> SIMD3<Float> {
                let p = vertexBase + index * vertexStride
                let v = p.assumingMemoryBound(to: SIMD3<Float>.self).pointee
                let w = transform * SIMD4<Float>(v.x, v.y, v.z, 1)
                return SIMD3(w.x, w.y, w.z)
            }
            func faceIndex(_ i: Int) -> Int {
                let p = indexBase + i * bytesPerIndex
                switch bytesPerIndex {
                case 2: return Int(p.assumingMemoryBound(to: UInt16.self).pointee)
                default: return Int(p.assumingMemoryBound(to: UInt32.self).pointee)
                }
            }

            for f in 0..<faces.count {
                let a = vertex(faceIndex(f * 3))
                let b = vertex(faceIndex(f * 3 + 1))
                let c = vertex(faceIndex(f * 3 + 2))
                total += simd_length(simd_cross(b - a, c - a)) / 2
                lo = simd_min(lo, simd_min(a, simd_min(b, c)))
                hi = simd_max(hi, simd_max(a, simd_max(b, c)))

                if let classifyAt, let kind = classifyAt(f) {
                    let centroid = (a + b + c) / 3
                    let key = SIMD2<Int32>(Int32(floor(centroid.x / cellSize)),
                                           Int32(floor(centroid.z / cellSize)))
                    var stats = cells[kind]?[key] ?? CellStats()
                    stats.faceCount += 1
                    stats.xzMin = simd_min(stats.xzMin, SIMD2(centroid.x, centroid.z))
                    stats.xzMax = simd_max(stats.xzMax, SIMD2(centroid.x, centroid.z))
                    stats.yMin = min(stats.yMin, centroid.y)
                    stats.yMax = max(stats.yMax, centroid.y)
                    cells[kind]?[key] = stats
                }
            }
        }
        guard total > 0, lo.x < hi.x else { return (nil, []) }

        // Connected components over 8-adjacent cells per class → clusters.
        var clusters: [FurnitureCluster] = []
        for (kind, grid) in cells where !grid.isEmpty {
            var unvisited = Set(grid.keys)
            while let seed = unvisited.first {
                unvisited.remove(seed)
                var stack = [seed]
                var faceCount = 0
                var xzMin = SIMD2<Float>(repeating: .greatestFiniteMagnitude)
                var xzMax = SIMD2<Float>(repeating: -.greatestFiniteMagnitude)
                var yMin = Float.greatestFiniteMagnitude
                var yMax = -Float.greatestFiniteMagnitude
                while let cell = stack.popLast() {
                    guard let stats = grid[cell] else { continue }
                    faceCount += stats.faceCount
                    xzMin = simd_min(xzMin, stats.xzMin)
                    xzMax = simd_max(xzMax, stats.xzMax)
                    yMin = min(yMin, stats.yMin)
                    yMax = max(yMax, stats.yMax)
                    for dx in Int32(-1)...1 {
                        for dz in Int32(-1)...1 where dx != 0 || dz != 0 {
                            let neighbor = SIMD2<Int32>(cell.x + dx, cell.y + dz)
                            if unvisited.remove(neighbor) != nil {
                                stack.append(neighbor)
                            }
                        }
                    }
                }
                let cluster = FurnitureCluster(kind: kind, xzMin: xzMin,
                                               xzMax: xzMax, yMin: yMin,
                                               yMax: yMax, faceCount: faceCount)
                if cluster.faceCount >= RegistryCoherence.clusterMinFaces,
                   cluster.footprintArea >= RegistryCoherence.clusterMinArea {
                    clusters.append(cluster)
                }
            }
        }

        return (MeshSnapshot(totalAreaM2: total, bboxMin: lo, bboxMax: hi), clusters)
    }
}

// MARK: - ARSessionDelegate (hub fan-out; callbacks arrive on main)

extension RoomScannerService: ARSessionDelegate {
    func session(_ session: ARSession, didAdd anchors: [ARAnchor]) {
        absorb(anchors)
    }
    func session(_ session: ARSession, didUpdate anchors: [ARAnchor]) {
        absorb(anchors)
    }
    func session(_ session: ARSession, didRemove anchors: [ARAnchor]) {
        drop(anchors)
    }
}
