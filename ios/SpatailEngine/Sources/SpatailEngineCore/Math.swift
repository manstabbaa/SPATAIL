import Foundation

// Pure, platform-agnostic math. We deliberately do NOT use `simd` here so the core
// compiles anywhere; the RealityKit backend bridges Vec3/Quat <-> SIMD3/simd_quatf.

public struct Vec3: Codable, Equatable {
    public var x: Float
    public var y: Float
    public var z: Float

    public init(_ x: Float = 0, _ y: Float = 0, _ z: Float = 0) { self.x = x; self.y = y; self.z = z }

    public static let zero = Vec3(0, 0, 0)
    public static let one  = Vec3(1, 1, 1)

    public static func + (a: Vec3, b: Vec3) -> Vec3 { Vec3(a.x + b.x, a.y + b.y, a.z + b.z) }
    public static func - (a: Vec3, b: Vec3) -> Vec3 { Vec3(a.x - b.x, a.y - b.y, a.z - b.z) }
    public static func * (a: Vec3, s: Float) -> Vec3 { Vec3(a.x * s, a.y * s, a.z * s) }

    public var length: Float { (x * x + y * y + z * z).squareRoot() }
    public var normalized: Vec3 { let l = length; return l > 1e-6 ? self * (1 / l) : .zero }
    public func dot(_ b: Vec3) -> Float { x * b.x + y * b.y + z * b.z }

    // Tolerant JSON: accepts either [x, y, z] or {"x":..,"y":..,"z":..}; encodes as an array.
    public init(from decoder: Decoder) throws {
        if var u = try? decoder.unkeyedContainer() {
            let xx = (try? u.decode(Float.self)) ?? 0
            let yy = (try? u.decode(Float.self)) ?? 0
            let zz = (try? u.decode(Float.self)) ?? 0
            self = Vec3(xx, yy, zz); return
        }
        let c = try decoder.container(keyedBy: K.self)
        x = (try? c.decode(Float.self, forKey: .x)) ?? 0
        y = (try? c.decode(Float.self, forKey: .y)) ?? 0
        z = (try? c.decode(Float.self, forKey: .z)) ?? 0
    }
    public func encode(to encoder: Encoder) throws {
        var u = encoder.unkeyedContainer()
        try u.encode(x); try u.encode(y); try u.encode(z)
    }
    enum K: String, CodingKey { case x, y, z }
}

public struct Quat: Codable, Equatable {
    public var x: Float, y: Float, z: Float, w: Float
    public init(x: Float = 0, y: Float = 0, z: Float = 0, w: Float = 1) { self.x = x; self.y = y; self.z = z; self.w = w }
    public static let identity = Quat(x: 0, y: 0, z: 0, w: 1)

    /// Axis-angle (radians). Bridges cleanly to simd_quatf(angle:axis:) in the backend.
    public init(angle: Float, axis: Vec3) {
        let a = axis.normalized
        let h = angle * 0.5
        let s = sin(h)
        self.init(x: a.x * s, y: a.y * s, z: a.z * s, w: cos(h))
    }
}

public struct Transform: Codable, Equatable {
    public var position: Vec3
    public var rotation: Quat
    public var scale: Vec3
    public init(position: Vec3 = .zero, rotation: Quat = .identity, scale: Vec3 = .one) {
        self.position = position; self.rotation = rotation; self.scale = scale
    }

    public init(from decoder: Decoder) throws {
        // Tolerant: any/all of position|rotation|scale may be absent.
        let c = try decoder.container(keyedBy: K.self)
        position = (try? c.decode(Vec3.self, forKey: .position)) ?? .zero
        rotation = (try? c.decode(Quat.self, forKey: .rotation)) ?? .identity
        scale = (try? c.decode(Vec3.self, forKey: .scale)) ?? .one
    }
    enum K: String, CodingKey { case position, rotation, scale }
}
