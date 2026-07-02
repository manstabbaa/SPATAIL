import Foundation

// One normalized asset from the SPATAIL AI Asset Factory, as listed by the PC at
// GET /factory/index (see studio/server/job_server.py _factory_index). Distinct
// from the generative library: these are ingested AI-generated assets that the
// factory cleaned + normalized to fixed bounds. Bounds are the factory's
// Blender-frame metres (frame-independent extents); the exported USDZ is Y-up.

struct FactoryBounds: Codable, Equatable {
    var x: Double = 0
    var y: Double = 0
    var z: Double = 0

    var longest: Double { Swift.max(x, Swift.max(y, z)) }

    init(x: Double = 0, y: Double = 0, z: Double = 0) { self.x = x; self.y = y; self.z = z }

    // Tolerant decode: a missing axis defaults to 0 rather than failing the whole list.
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        x = (try? c.decodeIfPresent(Double.self, forKey: .x)) ?? 0
        y = (try? c.decodeIfPresent(Double.self, forKey: .y)) ?? 0
        z = (try? c.decodeIfPresent(Double.self, forKey: .z)) ?? 0
    }
    enum CodingKeys: String, CodingKey { case x, y, z }
}

struct FactoryAsset: Codable, Identifiable, Equatable {
    let id: String
    let title: String
    let boundsMode: String?
    let originMode: String?
    let targetBounds: FactoryBounds
    let finalBounds: FactoryBounds
    let triangleCount: Int
    let unitSystem: String
    let glbUrl: String
    let usdzUrl: String
    let previewUrl: String
    let hasUsdz: Bool

    enum CodingKeys: String, CodingKey {
        case id, title
        case boundsMode = "bounds_mode"
        case originMode = "origin_mode"
        case targetBounds = "target_bounds_m"
        case finalBounds = "final_bounds_m"
        case triangleCount = "triangle_count"
        case unitSystem = "unit_system"
        case glbUrl = "glb_url"
        case usdzUrl = "usdz_url"
        case previewUrl = "preview_url"
        case hasUsdz = "has_usdz"
    }

    /// Does the normalized asset sit inside its configured target volume? (Should
    /// always be true — the factory guarantees it; shown in the QA readout.)
    var fitsTarget: Bool {
        finalBounds.x <= targetBounds.x + 1e-3
            && finalBounds.y <= targetBounds.y + 1e-3
            && finalBounds.z <= targetBounds.z + 1e-3
    }
}

struct FactoryIndex: Codable { let assets: [FactoryAsset]; let count: Int }
