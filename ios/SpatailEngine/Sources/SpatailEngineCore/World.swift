import Foundation

/// The simulation state: entities + a global blackboard + the event queue + the
/// outgoing command buffer. Pure data and pure Swift — no platform types. Everything
/// a rule/objective reads is reachable here by a dotted path (see `resolve`).
public final class World {
    public private(set) var entities: [EntityID: Entity] = [:]
    public var blackboard: [String: Value] = [:]
    public private(set) var time: Double = 0

    /// Spawnable entity templates (from the contract). Rule `spawn` actions look here.
    public var templates: [String: EntitySpec] = [:]
    /// Asset library refs (from the contract), used to build VisualSpecs on spawn.
    public var assets: [String: AssetSpec] = [:]

    public internal(set) var events: [EngineEvent] = []
    public internal(set) var commands: [BackendCommand] = []

    private var nextRaw = 1

    public init() {}

    public func newId() -> EntityID { defer { nextRaw += 1 }; return EntityID(nextRaw) }
    func advanceTime(_ dt: Double) { time += dt }

    // MARK: spawn / despawn

    @discardableResult
    public func spawn(name: String = "",
                      tags: Set<String> = [],
                      transform: Transform = .init(),
                      components: [String: Component] = [:],
                      template: String? = nil) -> Entity {
        let e = Entity(id: newId(), name: name, tags: tags, transform: transform,
                       components: components, template: template)
        entities[e.id] = e
        return e
    }

    /// Instantiate a registered template, queue its visual/physics creation, and emit
    /// `entity.spawned`. Returns nil if the template is unknown.
    @discardableResult
    public func spawn(fromTemplate id: String, at transform: Transform? = nil) -> Entity? {
        guard let spec = templates[id] else { return nil }
        let e = spec.instantiate(in: self)
        if let t = transform { e.transform = t }
        if let vs = spec.visualSpec(assets: assets) { command(.createVisual(e.id, vs)) }
        if let ps = spec.physicsSpec() { command(.addPhysics(e.id, ps)) }
        emit(EngineEvent(Ev.spawned, entity: e.id, payload: ["template": .text(id)]))
        return e
    }

    public func despawn(_ id: EntityID) {
        guard let e = entities[id], e.alive else { return }
        e.alive = false
        entities[id] = nil
        command(.removeVisual(id))
        emit(EngineEvent(Ev.despawned, entity: id, payload: e.template.map { ["template": .text($0)] } ?? [:]))
    }

    // MARK: queries

    public func find(name: String) -> Entity? { entities.values.first { $0.name == name } }
    public func query(component type: String) -> [Entity] { entities.values.filter { $0.has(type) } }
    public func query(tag: String) -> [Entity] { entities.values.filter { $0.tags.contains(tag) } }

    // MARK: bus + command buffer

    public func emit(_ e: EngineEvent) { events.append(e) }
    public func command(_ c: BackendCommand) { commands.append(c) }

    // MARK: blackboard helpers

    public func get(_ key: String) -> Value? { blackboard[key] }
    public func set(_ key: String, _ v: Value) { blackboard[key] = v }
    public func add(_ key: String, _ delta: Double) {
        let cur = blackboard[key]?.asDouble ?? 0
        blackboard[key] = .number(cur + delta)
    }

    // MARK: path resolution (closed + total — NOT an expression language)
    //
    // Supported roots:
    //   blackboard.<key> | state.<key>     a global value
    //   param.<key>                        a kit param (mirrored into blackboard as "param.<key>")
    //   time                               world time (seconds)
    //   event.<payloadKey> | event.entity | event.other
    //   self.<...> | target.<...> | other.<...>   entity lookups:
    //       <type>.<field>                 a component field
    //       transform.position.x|y|z       a transform component
    //       tag.<name>                     bool membership
    //       name | id                      identity
    //   const.<literal>                    a literal number/text
    public func resolve(_ path: String,
                        event: EngineEvent? = nil,
                        selfEntity: EntityID? = nil,
                        target: EntityID? = nil) -> Value? {
        let parts = path.split(separator: ".").map(String.init)
        guard let head = parts.first else { return nil }
        let rest = Array(parts.dropFirst())

        switch head {
        case "blackboard", "state":
            return blackboard[rest.joined(separator: ".")]
        case "param":
            return blackboard["param." + rest.joined(separator: ".")]
        case "time":
            return .number(time)
        case "event":
            guard let ev = event else { return nil }
            let key = rest.joined(separator: ".")
            if key == "entity" { return ev.entity.map { .number(Double($0.raw)) } }
            if key == "other"  { return ev.other.map  { .number(Double($0.raw)) } }
            return ev.payload[key]
        case "self":
            return resolveEntity(selfEntity ?? event?.entity, rest)
        case "target":
            return resolveEntity(target ?? event?.entity, rest)
        case "other":
            return resolveEntity(event?.other, rest)
        case "const":
            let v = rest.joined(separator: ".")
            return Double(v).map(Value.number) ?? .text(v)
        default:
            return blackboard[path]   // bare key -> blackboard
        }
    }

    private func resolveEntity(_ id: EntityID?, _ rest: [String]) -> Value? {
        guard let id = id, let e = entities[id] else { return nil }
        guard let first = rest.first else { return .number(Double(id.raw)) }
        switch first {
        case "transform":
            guard rest.count >= 3, rest[1] == "position" else { return nil }
            switch rest[2] {
            case "x": return .number(Double(e.transform.position.x))
            case "y": return .number(Double(e.transform.position.y))
            case "z": return .number(Double(e.transform.position.z))
            default: return nil
            }
        case "name": return .text(e.name)
        case "id":   return .number(Double(id.raw))
        case "tag":  return rest.count >= 2 ? .bool(e.tags.contains(rest[1])) : nil
        default:
            if rest.count >= 2 { return e.components[first]?.fields[rest[1]] }
            return e.components[first] != nil ? .bool(true) : nil
        }
    }
}
