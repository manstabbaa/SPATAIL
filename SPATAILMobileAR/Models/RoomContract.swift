// RoomContract.swift
//
// What we learned about the user's actual room. Produced by the room
// scanner on first launch, persisted to Documents/rooms/<roomId>.json,
// and consumed by the planner so a `placement: wall` resolves to a
// specific surface in the user's space — not a fictional wall.
//
// JSON shape matches docs/SPATAIL_ROOM_CONTRACT.md (kept in lockstep
// with the contract schema's v0.4 bump on the backend side).

import Foundation
import simd

struct RoomContract: Codable, Identifiable {
    let schemaVersion: String   // "0.4.0-spatail-room"
    let roomId: String
    let capturedAt: String      // ISO-8601
    let device: String          // "iPhone 15 Pro"
    let lidar: Bool
    let boundingBox: AxisAlignedBox
    let surfaces: [RoomSurface]
    let anchorablePoints: [AnchorablePoint]
    /// Whichever surface the scanner found largest + facing the user
    /// at scan time. The planner uses this as the default for
    /// `placement: wall` when multiple walls qualify.
    let preferredWallId: String?
    let preferredTableId: String?

    var id: String { roomId }
}

struct AxisAlignedBox: Codable, Hashable {
    let min: [Float]   // [x, y, z], world-relative metres
    let max: [Float]
}

enum SurfaceKind: String, Codable, CaseIterable {
    case floor, ceiling, wall, table, seat, window, door, unknown
}

struct RoomSurface: Codable, Identifiable, Hashable {
    let id: String
    let kind: SurfaceKind
    /// Convex polygon in world space — list of [x, y, z] vertices
    /// laid out in CCW order when viewed from the surface's outward
    /// normal. For walls this means CCW from inside the room.
    let polygon: [[Float]]
    let normal: [Float]
    /// Square metres. Used by the planner to rank candidate walls.
    let area: Float
    /// Height in metres above the room's floor plane. Useful for
    /// distinguishing coffee table (~0.4m) from desk (~0.75m) on the
    /// no-LiDAR path where classification is heuristic.
    let height: Float?
    /// "lidar" | "plane_heuristic" — how this surface was identified.
    let source: String

    var simdNormal: SIMD3<Float> {
        SIMD3(normal.count > 0 ? normal[0] : 0,
              normal.count > 1 ? normal[1] : 0,
              normal.count > 2 ? normal[2] : 0)
    }
    var simdCentroid: SIMD3<Float> {
        guard !polygon.isEmpty else { return .zero }
        var acc = SIMD3<Float>(0, 0, 0)
        for v in polygon where v.count >= 3 {
            acc += SIMD3<Float>(v[0], v[1], v[2])
        }
        return acc / Float(polygon.count)
    }
}

/// A specific point in space the user (or the planner) tagged for
/// anchoring elements. Tap-to-place writes one of these whenever the
/// user drops a ghost in real space.
struct AnchorablePoint: Codable, Identifiable, Hashable {
    let id: String
    let position: [Float]       // world-relative metres
    let surfaceId: String?      // optional: which surface this sits on
    let label: String?          // user-provided or planner-assigned
    let createdAt: String
}

// ---------------------------------------------------------------------------
// Document I/O
// ---------------------------------------------------------------------------

enum RoomContractIO {
    static func documentsDirectory() throws -> URL {
        let urls = FileManager.default.urls(for: .documentDirectory,
                                            in: .userDomainMask)
        guard let docs = urls.first else {
            throw NSError(domain: "RoomContractIO", code: 1,
                          userInfo: [NSLocalizedDescriptionKey: "no documents dir"])
        }
        let roomsDir = docs.appendingPathComponent("rooms", isDirectory: true)
        try FileManager.default.createDirectory(at: roomsDir,
                                                withIntermediateDirectories: true)
        return roomsDir
    }

    static func write(_ room: RoomContract) throws -> URL {
        let dir = try documentsDirectory()
        let url = dir.appendingPathComponent("\(room.roomId).json")
        let enc = JSONEncoder()
        enc.outputFormatting = [.prettyPrinted, .sortedKeys]
        let data = try enc.encode(room)
        try data.write(to: url, options: .atomic)
        return url
    }

    static func read(roomId: String) throws -> RoomContract {
        let dir = try documentsDirectory()
        let url = dir.appendingPathComponent("\(roomId).json")
        let data = try Data(contentsOf: url)
        return try JSONDecoder().decode(RoomContract.self, from: data)
    }

    static func listIds() -> [String] {
        guard let dir = try? documentsDirectory() else { return [] }
        let urls = (try? FileManager.default.contentsOfDirectory(
            at: dir, includingPropertiesForKeys: nil,
        )) ?? []
        return urls
            .filter { $0.pathExtension == "json" }
            .map { $0.deletingPathExtension().lastPathComponent }
            .sorted()
    }

    static func mostRecent() -> RoomContract? {
        let ids = listIds()
        for id in ids.reversed() {
            if let room = try? read(roomId: id) { return room }
        }
        return nil
    }
}
