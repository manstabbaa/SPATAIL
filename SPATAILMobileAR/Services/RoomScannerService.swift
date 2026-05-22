// RoomScannerService.swift
//
// Runs an ARKit session whose only job is to map the user's room.
//
// Two capability tiers:
//   - LiDAR (Pro / Pro Max iPhones from 12 onward):
//     ARWorldTrackingConfiguration.sceneReconstruction = .meshWithClassification
//     ARSessionDelegate gets ARMeshAnchors with per-face ARMeshClassification.
//     We bucket faces into floor/wall/ceiling/table/seat/window/door.
//
//   - Non-LiDAR: meshWithClassification is unsupported; we fall back to
//     ARWorldTrackingConfiguration.planeDetection = [.horizontal, .vertical]
//     and classify ARPlaneAnchors heuristically:
//       horizontal at y <  0.2  → floor
//       horizontal at 0.3..1.2  → table
//       vertical                → wall
//     ceiling/seat/window/door are not classified on this tier.
//
// Coverage is a 0..1 confidence — square-metres-covered relative to a
// "good enough" 12 m^2 budget (4×3 m room baseline). The UI shows this
// as a single progress arc and unlocks the "Continue" button at 0.75.
//
// SPATAIL_NEEDS_MAC_BUILD_VERIFY: ARMeshClassification & ARMeshAnchor
// classification API has been stable since iOS 13.4 but the geometry
// accessors (`.classification`, `.faces.count`, `.vertices.buffer`)
// are bridged through C structs and can be picky about lifetime — keep
// the lookup short.

import Foundation
import ARKit
import Combine
import simd

@MainActor
final class RoomScannerService: NSObject, ObservableObject {
    /// 0..1, square-metres-mapped vs target.
    @Published private(set) var coverage: Float = 0
    /// Per-kind running surface area in square metres.
    @Published private(set) var areaByKind: [SurfaceKind: Float] = [:]
    @Published private(set) var lidarAvailable: Bool = false
    @Published private(set) var sessionState: String = "idle"

    /// Set when the user taps Continue and we freeze the scan into a contract.
    @Published private(set) var finalContract: RoomContract?

    private weak var session: ARSession?
    private var meshAnchors: [UUID: ARMeshAnchor] = [:]
    private var planeAnchors: [UUID: ARPlaneAnchor] = [:]
    private let coverageTargetSqM: Float = 12.0
    private let device = UIDevice.current.modelName

    func attach(session: ARSession) {
        self.session = session
        session.delegate = self
        lidarAvailable = ARWorldTrackingConfiguration.supportsSceneReconstruction(.meshWithClassification)
        sessionState = lidarAvailable ? "lidar" : "plane_heuristic"
    }

    func configuration() -> ARWorldTrackingConfiguration {
        let cfg = ARWorldTrackingConfiguration()
        cfg.planeDetection = [.horizontal, .vertical]
        if lidarAvailable {
            cfg.sceneReconstruction = .meshWithClassification
        }
        cfg.environmentTexturing = .none  // we don't need IBL during scan
        return cfg
    }

    /// Snapshot whatever we've collected into a RoomContract and persist it.
    /// Returns the written URL on success.
    @discardableResult
    func finalize() throws -> URL {
        let surfaces: [RoomSurface] = lidarAvailable
            ? surfacesFromMeshAnchors()
            : surfacesFromPlaneAnchors()
        let (preferredWall, preferredTable) = pickPreferred(surfaces)
        let bbox = boundingBox(surfaces)
        let roomId = "room_" + ISO8601DateFormatter().string(from: Date())
            .replacingOccurrences(of: ":", with: "-")

        let room = RoomContract(
            schemaVersion: "0.4.0-spatail-room",
            roomId: roomId,
            capturedAt: ISO8601DateFormatter().string(from: Date()),
            device: device,
            lidar: lidarAvailable,
            boundingBox: bbox,
            surfaces: surfaces,
            anchorablePoints: [],
            preferredWallId: preferredWall?.id,
            preferredTableId: preferredTable?.id,
        )
        let url = try RoomContractIO.write(room)
        self.finalContract = room
        return url
    }

    // MARK: - Surface extraction

    /// LiDAR path — bucket every ARMeshAnchor's classified faces into kind
    /// buckets, then synthesise one polygon per (anchor, kind) pair as
    /// the convex hull of that bucket's vertices.
    ///
    /// SPATAIL_NEEDS_MAC_BUILD_VERIFY: this implementation iterates
    /// `anchor.geometry.faces` using the per-face classification accessor
    /// (`anchor.geometry.classificationOf(faceWithIndex:)`). Apple's docs
    /// state this returns `ARMeshClassification`; if compile complains,
    /// the alternative is to walk the raw `classification` MTLBuffer.
    private func surfacesFromMeshAnchors() -> [RoomSurface] {
        var out: [RoomSurface] = []
        var areaCounters: [SurfaceKind: Float] = [:]
        for (uuid, anchor) in meshAnchors {
            let geom = anchor.geometry
            var byKind: [SurfaceKind: [SIMD3<Float>]] = [:]
            let nFaces = geom.faces.count
            // Walk classified faces — coarse triangulation is enough for
            // the convex hull below; we don't try to preserve concavity.
            for fi in 0..<nFaces {
                let raw = geom.classificationOf(faceWithIndex: fi)
                let kind = bridgeMeshClassification(raw)
                if kind == .unknown { continue }
                let triVerts = geom.verticesOfFace(faceWithIndex: fi, anchor: anchor)
                byKind[kind, default: []].append(contentsOf: triVerts)
            }
            for (kind, verts) in byKind {
                if verts.count < 3 { continue }
                let hull = convexHullXZ(verts)
                if hull.count < 3 { continue }
                let normal = bestNormal(kind: kind, verts: verts)
                let area = polygonArea(hull)
                areaCounters[kind, default: 0] += area
                out.append(RoomSurface(
                    id: "\(kind.rawValue).\(short(uuid))",
                    kind: kind,
                    polygon: hull.map { [$0.x, $0.y, $0.z] },
                    normal: [normal.x, normal.y, normal.z],
                    area: area,
                    height: kind == .table ? hull.first?.y : nil,
                    source: "lidar",
                ))
            }
        }
        self.areaByKind = areaCounters
        self.coverage = min(1.0, areaCounters.values.reduce(0, +) / coverageTargetSqM)
        return out
    }

    /// Non-LiDAR path — every ARPlaneAnchor becomes a surface. Kind is
    /// heuristic by alignment + height.
    private func surfacesFromPlaneAnchors() -> [RoomSurface] {
        var out: [RoomSurface] = []
        var areaCounters: [SurfaceKind: Float] = [:]
        for (uuid, plane) in planeAnchors {
            let polygonModel = plane.geometry.boundaryVertices.map { v -> SIMD3<Float> in
                let worldHomogeneous = plane.transform * SIMD4<Float>(v.x, v.y, v.z, 1)
                return SIMD3(worldHomogeneous.x, worldHomogeneous.y, worldHomogeneous.z)
            }
            if polygonModel.count < 3 { continue }
            let centroid = polygonModel.reduce(SIMD3<Float>(0, 0, 0), +) / Float(polygonModel.count)
            let kind: SurfaceKind = {
                if plane.alignment == .vertical { return .wall }
                if centroid.y < 0.2 { return .floor }
                if centroid.y > 0.3 && centroid.y < 1.2 { return .table }
                if centroid.y > 1.8 { return .ceiling }
                return .floor
            }()
            let normal: SIMD3<Float> = plane.alignment == .vertical
                ? approxVerticalNormal(plane: plane)
                : SIMD3(0, 1, 0)
            let area = polygonArea(polygonModel)
            areaCounters[kind, default: 0] += area
            out.append(RoomSurface(
                id: "\(kind.rawValue).\(short(uuid))",
                kind: kind,
                polygon: polygonModel.map { [$0.x, $0.y, $0.z] },
                normal: [normal.x, normal.y, normal.z],
                area: area,
                height: kind == .table ? centroid.y : nil,
                source: "plane_heuristic",
            ))
        }
        self.areaByKind = areaCounters
        self.coverage = min(1.0, areaCounters.values.reduce(0, +) / coverageTargetSqM)
        return out
    }

    private func pickPreferred(_ surfaces: [RoomSurface]) -> (RoomSurface?, RoomSurface?) {
        let walls = surfaces.filter { $0.kind == .wall }.sorted { $0.area > $1.area }
        let tables = surfaces.filter { $0.kind == .table }.sorted { $0.area > $1.area }
        return (walls.first, tables.first)
    }

    private func boundingBox(_ surfaces: [RoomSurface]) -> AxisAlignedBox {
        var lo = SIMD3<Float>(.infinity, .infinity, .infinity)
        var hi = SIMD3<Float>(-.infinity, -.infinity, -.infinity)
        for s in surfaces {
            for v in s.polygon where v.count >= 3 {
                let p = SIMD3<Float>(v[0], v[1], v[2])
                lo = .init(min(lo.x, p.x), min(lo.y, p.y), min(lo.z, p.z))
                hi = .init(max(hi.x, p.x), max(hi.y, p.y), max(hi.z, p.z))
            }
        }
        if !lo.x.isFinite {
            lo = .zero; hi = .zero
        }
        return AxisAlignedBox(
            min: [lo.x, lo.y, lo.z],
            max: [hi.x, hi.y, hi.z],
        )
    }

    private func bridgeMeshClassification(_ c: ARMeshClassification) -> SurfaceKind {
        switch c {
        case .floor:   return .floor
        case .ceiling: return .ceiling
        case .wall:    return .wall
        case .table:   return .table
        case .seat:    return .seat
        case .window:  return .window
        case .door:    return .door
        case .none:    return .unknown
        @unknown default: return .unknown
        }
    }

    private func bestNormal(kind: SurfaceKind, verts: [SIMD3<Float>]) -> SIMD3<Float> {
        switch kind {
        case .floor:   return SIMD3(0, 1, 0)
        case .ceiling: return SIMD3(0, -1, 0)
        case .wall:
            // Take an outward XZ direction from the room centroid → cluster mean.
            let mean = verts.reduce(SIMD3<Float>(0, 0, 0), +) / Float(verts.count)
            let n = SIMD3<Float>(mean.x, 0, mean.z)
            return simd_length(n) > 1e-4 ? simd_normalize(n) : SIMD3(0, 0, 1)
        default:       return SIMD3(0, 1, 0)
        }
    }

    private func approxVerticalNormal(plane: ARPlaneAnchor) -> SIMD3<Float> {
        // ARPlaneAnchor's transform encodes the plane's local frame —
        // its local +Y is the plane normal. Pull that out of the
        // upper-left 3×3.
        let t = plane.transform
        return simd_normalize(SIMD3(t.columns.1.x, t.columns.1.y, t.columns.1.z))
    }

    private func short(_ uuid: UUID) -> String {
        return String(uuid.uuidString.prefix(8)).lowercased()
    }

    // MARK: - Convex hull (XZ projection) + polygon area
    //
    // Walls live in the XZ plane after we project (their world Y is the
    // wall's vertical run); horizontal surfaces are XZ-natural. So a
    // single 2D hull on the XZ plane works for both bucket types
    // without losing the polygon's spatial sense.

    private func convexHullXZ(_ pts: [SIMD3<Float>]) -> [SIMD3<Float>] {
        if pts.count <= 3 { return pts }
        let projected = pts.map { (point: $0, key: SIMD2<Float>($0.x, $0.z)) }
        // Andrew's monotone chain over the XZ projection.
        let sorted = projected.sorted { lhs, rhs in
            lhs.key.x != rhs.key.x ? lhs.key.x < rhs.key.x : lhs.key.y < rhs.key.y
        }
        func cross(_ o: SIMD2<Float>, _ a: SIMD2<Float>, _ b: SIMD2<Float>) -> Float {
            (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x)
        }
        var lower: [(point: SIMD3<Float>, key: SIMD2<Float>)] = []
        for p in sorted {
            while lower.count >= 2 && cross(lower[lower.count - 2].key, lower[lower.count - 1].key, p.key) <= 0 {
                lower.removeLast()
            }
            lower.append(p)
        }
        var upper: [(point: SIMD3<Float>, key: SIMD2<Float>)] = []
        for p in sorted.reversed() {
            while upper.count >= 2 && cross(upper[upper.count - 2].key, upper[upper.count - 1].key, p.key) <= 0 {
                upper.removeLast()
            }
            upper.append(p)
        }
        return (lower.dropLast() + upper.dropLast()).map { $0.point }
    }

    private func polygonArea(_ verts: [SIMD3<Float>]) -> Float {
        // Shoelace in XZ — same projection as the hull, so wall areas
        // come out as plan-footprint of the wall slab (small for a thin
        // wall). Good enough to rank candidates.
        guard verts.count >= 3 else { return 0 }
        var sum: Float = 0
        for i in 0..<verts.count {
            let a = verts[i]
            let b = verts[(i + 1) % verts.count]
            sum += a.x * b.z - b.x * a.z
        }
        return abs(sum) / 2
    }
}

// MARK: - ARSessionDelegate

extension RoomScannerService: ARSessionDelegate {
    nonisolated func session(_ session: ARSession, didAdd anchors: [ARAnchor]) {
        Task { @MainActor in self.ingest(added: anchors) }
    }
    nonisolated func session(_ session: ARSession, didUpdate anchors: [ARAnchor]) {
        Task { @MainActor in self.ingest(added: anchors) }
    }
    nonisolated func session(_ session: ARSession, didRemove anchors: [ARAnchor]) {
        Task { @MainActor in
            for a in anchors {
                self.meshAnchors.removeValue(forKey: a.identifier)
                self.planeAnchors.removeValue(forKey: a.identifier)
            }
        }
    }

    @MainActor
    private func ingest(added: [ARAnchor]) {
        for a in added {
            if let mesh = a as? ARMeshAnchor { meshAnchors[a.identifier] = mesh }
            if let plane = a as? ARPlaneAnchor { planeAnchors[a.identifier] = plane }
        }
        // Cheap running coverage: project current buckets without writing.
        let snapshot = lidarAvailable
            ? surfacesFromMeshAnchors()
            : surfacesFromPlaneAnchors()
        _ = snapshot  // values are stored on self via the published vars
    }
}

// MARK: - ARMeshGeometry helpers
//
// These extension methods wrap the C-buffer accessors so the scanner's
// per-face loop stays readable. They're light wrappers — if Apple's
// API names drift, only this file needs updating.

extension ARMeshGeometry {
    /// SPATAIL_NEEDS_MAC_BUILD_VERIFY: per-face classification accessor.
    /// In iOS 13.4+ the API is `classificationOf(faceWithIndex:)`. If
    /// Xcode complains, fall back to reading `classification?.buffer`
    /// at offset `faceIndex * classification?.bytesPerIndex`.
    func classificationOf(faceWithIndex faceIndex: Int) -> ARMeshClassification {
        guard let cls = self.classification else { return .none }
        let buf = cls.buffer.contents().assumingMemoryBound(to: UInt8.self)
        let raw = Int(buf[faceIndex])
        return ARMeshClassification(rawValue: raw) ?? .none
    }

    /// Returns the 3 world-space vertices of a triangle face.
    func verticesOfFace(faceWithIndex faceIndex: Int, anchor: ARMeshAnchor) -> [SIMD3<Float>] {
        let face = faces
        let vertexCount = face.indexCountPerPrimitive
        let stride = vertexCount * face.bytesPerIndex
        let basePtr = face.buffer.contents().advanced(by: faceIndex * stride)
        var indices: [Int] = []
        for i in 0..<vertexCount {
            let bytes = basePtr.advanced(by: i * face.bytesPerIndex)
            switch face.bytesPerIndex {
            case 2: indices.append(Int(bytes.assumingMemoryBound(to: UInt16.self).pointee))
            case 4: indices.append(Int(bytes.assumingMemoryBound(to: UInt32.self).pointee))
            default: return []
            }
        }
        let vb = vertices
        let xform = anchor.transform
        return indices.compactMap { idx -> SIMD3<Float>? in
            let p = vb.buffer.contents().advanced(by: idx * vb.stride)
                .assumingMemoryBound(to: SIMD3<Float>.self).pointee
            let h = xform * SIMD4<Float>(p.x, p.y, p.z, 1)
            return SIMD3(h.x, h.y, h.z)
        }
    }
}

// MARK: - UIDevice model name

extension UIDevice {
    /// Marketing name where possible (e.g. "iPhone 15 Pro"). Falls back
    /// to the hardware identifier string. Used only as metadata in the
    /// stored RoomContract.
    var modelName: String {
        var sys = utsname()
        uname(&sys)
        let mirror = Mirror(reflecting: sys.machine)
        let id = mirror.children.reduce("") { acc, c in
            guard let v = c.value as? Int8, v != 0 else { return acc }
            return acc + String(UnicodeScalar(UInt8(v)))
        }
        return id.isEmpty ? UIDevice.current.model : id
    }
}
