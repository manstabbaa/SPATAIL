import Foundation

// The platform-agnostic experience contract — what the brain emits and the engine
// runs. It is a strict superset of today's modular contract: assets + (optional)
// steps for explainers, PLUS entities + components + rules + objectives + a genre/kit
// so any genre can be described in the same envelope. Decoding is tolerant: every
// field defaults, so a partial or legacy payload never fails the whole decode.

public struct ExperienceContract: Decodable {
    public var schemaVersion: String
    public var id: String
    public var title: String
    public var genre: String                 // "explainer" | "shooter" | ...
    public var kit: String                   // kit id (defaults to genre)
    public var params: [String: Value]       // kit params (targetScore, fireRate, ...)
    public var blackboard: [String: Value]   // initial world state

    public var assets: [AssetSpec]           // asset-library refs (presentation)
    public var entities: [EntitySpec]        // initial scene
    public var templates: [EntitySpec]       // spawnable (projectile, enemy, ...)
    public var rules: [Rule]                 // authored laws/reactions
    public var objectives: [Objective]       // authored goals
    public var steps: [StepSpec]             // explainer sequence (optional)
    public var systemsOverride: [String]?    // explicit system list (else the kit decides)

    public var anchoring: AnchoringSpec
    public var placement: PlacementSpec

    public init(from d: Decoder) throws {
        let c = try d.container(keyedBy: K.self)
        schemaVersion = (try? c.decode(String.self, forKey: .schemaVersion)) ?? "0.8-spatail-engine"
        id = (try? c.decode(String.self, forKey: .id))
           ?? (try? c.decode(String.self, forKey: .experienceId)) ?? UUID().uuidString
        title = (try? c.decode(String.self, forKey: .title)) ?? "Experience"
        genre = (try? c.decode(String.self, forKey: .genre)) ?? "explainer"
        kit = (try? c.decode(String.self, forKey: .kit)) ?? genre
        params = (try? c.decode([String: Value].self, forKey: .params)) ?? [:]
        blackboard = (try? c.decode([String: Value].self, forKey: .blackboard)) ?? [:]
        assets = (try? c.decode([AssetSpec].self, forKey: .assets)) ?? []
        entities = (try? c.decode([EntitySpec].self, forKey: .entities)) ?? []
        templates = (try? c.decode([EntitySpec].self, forKey: .templates)) ?? []
        rules = (try? c.decode([Rule].self, forKey: .rules)) ?? []
        objectives = (try? c.decode([Objective].self, forKey: .objectives)) ?? []
        steps = (try? c.decode([StepSpec].self, forKey: .steps))
             ?? (try? c.decode([StepSpec].self, forKey: .sequence)) ?? []
        systemsOverride = try? c.decode([String].self, forKey: .systems)
        anchoring = (try? c.decode(AnchoringSpec.self, forKey: .anchoring)) ?? AnchoringSpec()
        placement = (try? c.decode(PlacementSpec.self, forKey: .placement)) ?? PlacementSpec()
    }

    enum K: String, CodingKey {
        case schemaVersion, id, experienceId, title, genre, kit, params, blackboard,
             assets, entities, templates, rules, objectives, steps, sequence, systems,
             anchoring, placement
    }

    /// Decode from raw JSON (server response, sample file, SSE patch payload).
    public static func decode(_ data: Data) throws -> ExperienceContract {
        try JSONDecoder().decode(ExperienceContract.self, from: data)
    }
}

public struct AssetSpec: Decodable {
    public var id: String
    public var name: String
    public var role: String
    public var usdzUrl: String
    public var glbUrl: String
    public var fallbackPrimitive: String
    public var realSizeMeters: [Double]?
    public var realScaleBaked: Bool
    public var size: [Double]
    public var clips: [String]
    public var regions: [String]

    public init(from d: Decoder) throws {
        let c = try d.container(keyedBy: K.self)
        id = (try? c.decode(String.self, forKey: .id)) ?? UUID().uuidString
        name = (try? c.decode(String.self, forKey: .name)) ?? ""
        role = (try? c.decode(String.self, forKey: .role)) ?? "part"
        usdzUrl = (try? c.decode(String.self, forKey: .usdzUrl)) ?? ""
        glbUrl = (try? c.decode(String.self, forKey: .glbUrl)) ?? ""
        fallbackPrimitive = (try? c.decode(String.self, forKey: .fallbackPrimitive)) ?? "box"
        realSizeMeters = try? c.decode([Double].self, forKey: .realSizeMeters)
        realScaleBaked = (try? c.decode(Bool.self, forKey: .realScaleBaked)) ?? false
        size = (try? c.decode([Double].self, forKey: .size)) ?? [0.2, 0.2, 0.2]
        clips = (try? c.decode([String].self, forKey: .clips)) ?? []
        regions = (try? c.decode([String].self, forKey: .regions)) ?? []
    }
    enum K: String, CodingKey {
        case id, name, role, usdzUrl, glbUrl, fallbackPrimitive, realSizeMeters,
             realScaleBaked, size, clips, regions
    }
}

/// A node in the scene (or a spawnable template). Carries ECS data (tags, transform,
/// components) plus a presentation handle (asset ref or primitive) and optional physics.
public struct EntitySpec: Decodable {
    public var id: String              // logical name
    public var asset: String?          // AssetSpec id
    public var primitive: String?      // "box" | "sphere" | ...
    public var size: [Double]?         // primitive size (metres)
    public var color: String?          // hex tint for primitives
    public var tags: [String]
    public var transform: Transform
    public var components: [Component]
    public var hittable: Bool
    public var physics: PhysicsBlock?

    public init(from d: Decoder) throws {
        let c = try d.container(keyedBy: K.self)
        id = (try? c.decode(String.self, forKey: .id)) ?? UUID().uuidString
        asset = try? c.decode(String.self, forKey: .asset)
        primitive = try? c.decode(String.self, forKey: .primitive)
        size = try? c.decode([Double].self, forKey: .size)
        color = try? c.decode(String.self, forKey: .color)
        tags = (try? c.decode([String].self, forKey: .tags)) ?? []
        transform = (try? c.decode(Transform.self, forKey: .transform)) ?? .init()
        components = (try? c.decode([Component].self, forKey: .components)) ?? []
        hittable = (try? c.decode(Bool.self, forKey: .hittable)) ?? true
        physics = try? c.decode(PhysicsBlock.self, forKey: .physics)
    }
    enum K: String, CodingKey { case id, asset, primitive, size, color, tags, transform, components, hittable, physics }

    /// Build a live entity (ECS data only — visuals/physics are queued separately).
    public func instantiate(in w: World) -> Entity {
        var comps: [String: Component] = [:]
        for c in components { comps[c.type] = c }
        return w.spawn(name: id, tags: Set(tags), transform: transform, components: comps, template: id)
    }

    public func visualSpec(assets: [String: AssetSpec]) -> VisualSpec? {
        if let aid = asset, let a = assets[aid] {
            let rsm = a.realSizeMeters.flatMap { $0.count >= 3 ? Vec3(Float($0[0]), Float($0[1]), Float($0[2])) : nil }
            return VisualSpec(assetUSDZ: a.usdzUrl.isEmpty ? nil : a.usdzUrl,
                              primitive: a.fallbackPrimitive,
                              size: vec(a.size) ?? Vec3(0.2, 0.2, 0.2),
                              realScaleBaked: a.realScaleBaked,
                              realSizeMeters: rsm,
                              hittable: hittable,
                              color: color)
        }
        if let p = primitive {
            return VisualSpec(assetUSDZ: nil, primitive: p,
                              size: vec(size) ?? Vec3(0.2, 0.2, 0.2),
                              realScaleBaked: false, realSizeMeters: nil,
                              hittable: hittable, color: color)
        }
        return nil
    }

    public func physicsSpec() -> PhysicsSpec? {
        guard let p = physics else { return nil }
        return PhysicsSpec(mode: p.mode, body: p.body, mass: p.mass, collider: p.collider)
    }

    private func vec(_ a: [Double]?) -> Vec3? {
        guard let a = a, a.count >= 3 else { return nil }
        return Vec3(Float(a[0]), Float(a[1]), Float(a[2]))
    }

    public struct PhysicsBlock: Decodable {
        public var mode: String, body: String, collider: String
        public var mass: Float
        public init(from d: Decoder) throws {
            let c = try d.container(keyedBy: K.self)
            mode = (try? c.decode(String.self, forKey: .mode)) ?? "dynamic"
            body = (try? c.decode(String.self, forKey: .body)) ?? "rigid"
            collider = (try? c.decode(String.self, forKey: .collider)) ?? "box"
            mass = (try? c.decode(Float.self, forKey: .mass)) ?? 1
        }
        enum K: String, CodingKey { case mode, body, collider, mass }
    }
}

public struct AnchoringSpec: Decodable {
    public var mode: String, fallback: String, referenceObjectUrl: String, tracking: String
    public var pauseOnTrackingLoss: Bool
    public init() { mode = "world"; fallback = "world"; referenceObjectUrl = ""; tracking = "detection"; pauseOnTrackingLoss = true }
    public init(from d: Decoder) throws {
        let c = try d.container(keyedBy: K.self)
        mode = (try? c.decode(String.self, forKey: .mode)) ?? "world"
        fallback = (try? c.decode(String.self, forKey: .fallback)) ?? "world"
        referenceObjectUrl = (try? c.decode(String.self, forKey: .referenceObjectUrl)) ?? ""
        tracking = (try? c.decode(String.self, forKey: .tracking)) ?? "detection"
        pauseOnTrackingLoss = (try? c.decode(Bool.self, forKey: .pauseOnTrackingLoss)) ?? true
    }
    enum K: String, CodingKey { case mode, fallback, referenceObjectUrl, tracking, pauseOnTrackingLoss }
}

public struct PlacementSpec: Decodable {
    public var anchorType: String, scaleMode: String
    public var maxSurfaceCoverage: Double
    public var faceUser: Bool
    public init() { anchorType = "table"; scaleMode = "fit_to_surface"; maxSurfaceCoverage = 0.65; faceUser = true }
    public init(from d: Decoder) throws {
        let c = try d.container(keyedBy: K.self)
        anchorType = (try? c.decode(String.self, forKey: .anchorType)) ?? "table"
        scaleMode = (try? c.decode(String.self, forKey: .scaleMode)) ?? "fit_to_surface"
        maxSurfaceCoverage = (try? c.decode(Double.self, forKey: .maxSurfaceCoverage)) ?? 0.65
        faceUser = (try? c.decode(Bool.self, forKey: .faceUser)) ?? true
    }
    enum K: String, CodingKey { case anchorType, scaleMode, maxSurfaceCoverage, faceUser }
}
