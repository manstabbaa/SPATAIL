import Foundation

// The ONE thing the core emits OUT to the platform. The core is a pure simulation;
// it never touches RealityKit/Metal/ARKit. Instead it produces `BackendCommand`s
// describing what must be rendered/simulated, and a `SceneBackend` (RealityKit on
// iOS, something else elsewhere) applies them. This command/event seam IS the
// platform-agnostic boundary.

/// How a visual should be created. The backend resolves `assetUSDZ` (download/load)
/// or falls back to `primitive`.
public struct VisualSpec {
    public var assetUSDZ: String?
    public var primitive: String?       // "box" | "sphere" | "cylinder" | "plane"
    public var size: Vec3               // metres (primitive size, or fit target)
    public var realScaleBaked: Bool     // true -> render at 1.0, do not refit
    public var realSizeMeters: Vec3?    // fit longest dim to this real size if set
    public var hittable: Bool           // generate collision shapes for tap/hit-test
    public var color: String?           // hex tint for primitives
    public init(assetUSDZ: String? = nil, primitive: String? = "box", size: Vec3 = Vec3(0.2, 0.2, 0.2),
                realScaleBaked: Bool = false, realSizeMeters: Vec3? = nil, hittable: Bool = true, color: String? = nil) {
        self.assetUSDZ = assetUSDZ; self.primitive = primitive; self.size = size
        self.realScaleBaked = realScaleBaked; self.realSizeMeters = realSizeMeters
        self.hittable = hittable; self.color = color
    }
}

/// Physics request. Maps to RealityKit PhysicsBody/Collision in the backend (the
/// pattern already proven in ExperienceRuntime.applyPhysics).
public struct PhysicsSpec {
    public var mode: String      // "dynamic" | "kinematic" | "static"
    public var body: String      // "rigid" | "bouncy" | "heavy" | "soft"
    public var mass: Float
    public var collider: String  // "box" | "sphere" | "convex" | "mesh"
    public init(mode: String = "dynamic", body: String = "rigid", mass: Float = 1, collider: String = "box") {
        self.mode = mode; self.body = body; self.mass = mass; self.collider = collider
    }
}

/// A non-destructive visual effect (maps to the app's SpatailMaterials approach:
/// emissive/opacity/tint mutation, originals restored on clear).
public struct VisualEffect: Equatable {
    public var name: String      // "highlight" | "emissive" | "ghost" | "tint" | "none"
    public var hex: String?      // for tint
    public var intensity: Float
    public init(name: String = "highlight", hex: String? = nil, intensity: Float = 1) {
        self.name = name; self.hex = hex; self.intensity = intensity
    }
    /// Parse the contract's compact effect string: "highlight" | "tint:#RRGGBB" | "none".
    public static func from(_ s: String) -> VisualEffect {
        if s.isEmpty { return VisualEffect(name: "highlight") }
        if s.hasPrefix("tint:") { return VisualEffect(name: "tint", hex: String(s.dropFirst(5))) }
        return VisualEffect(name: s)
    }
}

public enum EffectTarget: Equatable {
    case entity(EntityID)
    case region(EntityID, String)   // entity + baked overlay-region id (the lion's eye)
}

public struct AnchorSpec {
    public var mode: String            // "object" | "world" | "table" | "floor"
    public var referenceObjectURL: String?
    public var entity: EntityID?
    public init(mode: String = "world", referenceObjectURL: String? = nil, entity: EntityID? = nil) {
        self.mode = mode; self.referenceObjectURL = referenceObjectURL; self.entity = entity
    }
}

/// Generic spatial-HUD update (score, objective text, health). Kept data-driven so a
/// kit can surface whatever it wants without a bespoke command per genre.
public struct HudSpec {
    public var fields: [String: Value]
    public init(_ fields: [String: Value]) { self.fields = fields }
}

public enum BackendCommand {
    case createVisual(EntityID, VisualSpec)
    case updateTransform(EntityID, Transform)
    case removeVisual(EntityID)
    case playClip(EntityID, String, Bool)        // entity, clip name, loop
    case stopClip(EntityID)
    case applyEffect(EffectTarget, VisualEffect)
    case clearEffect(EffectTarget)
    case addPhysics(EntityID, PhysicsSpec)
    case applyImpulse(EntityID, Vec3)
    case raycast(String, Vec3, Vec3, [String])   // id, origin, direction, tag mask
    case anchor(AnchorSpec)
    case speak(String)
    case playSound(String)
    case hud(HudSpec)
}

/// Implement this once per platform. The core hands you commands; you render them and
/// push `EngineEvent`s back via `SpatailEngine.submit(_:)`.
public protocol SceneBackend: AnyObject {
    func apply(_ command: BackendCommand)
}
