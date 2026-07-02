import Foundation

/// Stable runtime handle for an entity. Not the same as the contract's logical id
/// (a name) — `World` mints these as entities spawn/despawn.
public struct EntityID: Hashable, Codable, CustomStringConvertible {
    public let raw: Int
    public init(_ raw: Int) { self.raw = raw }
    public var description: String { "e\(raw)" }
}

/// A data-driven component: a typed bag of fields the rules engine can address by
/// path ("target.health.hp"). Keeping components as data (not Swift structs) is what
/// lets the brain author NEW component types at runtime without an app rebuild — the
/// native Systems read the fields they understand and ignore the rest.
public struct Component: Codable, Equatable {
    public var type: String
    public var fields: [String: Value]

    public init(_ type: String, _ fields: [String: Value] = [:]) {
        self.type = type
        self.fields = fields
    }

    public func num(_ k: String, _ d: Double = 0) -> Double { fields[k]?.asDouble ?? d }
    public func str(_ k: String, _ d: String = "") -> String { fields[k]?.asString ?? d }
    public func bool(_ k: String, _ d: Bool = false) -> Bool { fields[k]?.asBool ?? d }
    public mutating func set(_ k: String, _ v: Value) { fields[k] = v }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: K.self)
        type = (try? c.decode(String.self, forKey: .type)) ?? ""
        fields = (try? c.decode([String: Value].self, forKey: .fields)) ?? [:]
    }
    enum K: String, CodingKey { case type, fields }
}

/// A live entity. Reference type so Systems can mutate it in place cheaply.
public final class Entity {
    public let id: EntityID
    public var name: String                  // logical id from the contract (may repeat across spawns)
    public var tags: Set<String>
    public var transform: Transform
    public var components: [String: Component]   // one component per type
    public var alive: Bool = true
    public var template: String?             // origin template id, if spawned from one

    public init(id: EntityID,
                name: String = "",
                tags: Set<String> = [],
                transform: Transform = .init(),
                components: [String: Component] = [:],
                template: String? = nil) {
        self.id = id
        self.name = name
        self.tags = tags
        self.transform = transform
        self.components = components
        self.template = template
    }

    public func has(_ type: String) -> Bool { components[type] != nil }
    public func comp(_ type: String) -> Component? { components[type] }
    public func set(_ c: Component) { components[c.type] = c }
    public func field(_ type: String, _ key: String) -> Value? { components[type]?.fields[key] }

    public func setField(_ type: String, _ key: String, _ v: Value) {
        if var c = components[type] { c.set(key, v); components[type] = c }
        else { var c = Component(type); c.set(key, v); components[type] = c }
    }
}
