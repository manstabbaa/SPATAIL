import Foundation

// ─────────────────────────────────────────────────────────────────────────────
// The rules engine: the "runtime creation of rules / laws" the brain authors as
// data. A rule is event → (conditions) → actions. The vocabulary is CLOSED and
// typed — there is deliberately NO expression language, no loops, no parser. A
// condition is a path/op/value triple; an action is a named, argumented effect.
// This is rich enough to express explainer gating AND shooter combat, and small
// enough for one person to keep correct.
// ─────────────────────────────────────────────────────────────────────────────

public enum Op: String, Codable {
    case eq, neq, gt, gte, lt, lte, exists, notExists, contains
}

/// Shared comparison so RuleSystem and ObjectiveSystem agree on semantics.
public enum Predicate {
    public static func compare(_ lhs: Value?, _ op: Op, _ rhs: Value?) -> Bool {
        switch op {
        case .exists:    return lhs != nil
        case .notExists: return lhs == nil
        case .eq:        return same(lhs, rhs)
        case .neq:       return !same(lhs, rhs)
        case .gt, .gte, .lt, .lte:
            guard let a = lhs?.asDouble, let b = rhs?.asDouble else { return false }
            switch op {
            case .gt:  return a > b
            case .gte: return a >= b
            case .lt:  return a < b
            default:   return a <= b
            }
        case .contains:
            if let list = lhs?.asList, let needle = rhs { return list.contains(needle) }
            if let s = lhs?.asString, let n = rhs?.asString { return s.contains(n) }
            return false
        }
    }
    private static func same(_ a: Value?, _ b: Value?) -> Bool {
        if let a = a?.asDouble, let b = b?.asDouble { return a == b }
        if let a = a?.asString, let b = b?.asString { return a == b }
        if let a = a?.asBool, let b = b?.asBool { return a == b }
        return a == nil && b == nil
    }
}

public struct Condition: Decodable {
    public var lhs: String       // a path resolved against world/event/entities
    public var op: Op
    public var rhs: Value?       // a literal, or a "$path" reference

    public init(_ lhs: String, _ op: Op, _ rhs: Value?) { self.lhs = lhs; self.op = op; self.rhs = rhs }
    public init(from d: Decoder) throws {
        let c = try d.container(keyedBy: K.self)
        lhs = (try? c.decode(String.self, forKey: .lhs)) ?? (try? c.decode(String.self, forKey: .path)) ?? ""
        op  = (try? c.decode(Op.self, forKey: .op)) ?? .eq
        rhs = try? c.decode(Value.self, forKey: .rhs)
    }
    enum K: String, CodingKey { case lhs, path, op, rhs }
}

public struct EventMatch: Decodable {
    public var event: String
    public var target: String?   // require this tag/name/component on event.entity
    public var once: Bool
    public var cooldown: Double

    public init(event: String, target: String? = nil, once: Bool = false, cooldown: Double = 0) {
        self.event = event; self.target = target; self.once = once; self.cooldown = cooldown
    }
    public init(from d: Decoder) throws {
        let c = try d.container(keyedBy: K.self)
        event = (try? c.decode(String.self, forKey: .event)) ?? ""
        target = try? c.decode(String.self, forKey: .target)
        once = (try? c.decode(Bool.self, forKey: .once)) ?? false
        cooldown = (try? c.decode(Double.self, forKey: .cooldown)) ?? 0
    }
    enum K: String, CodingKey { case event, target, once, cooldown }
}

public struct ActionSpec: Decodable {
    public var kind: String              // ActionKind raw — String for forward-compat tolerance
    public var args: [String: Value]

    public init(_ kind: String, _ args: [String: Value] = [:]) { self.kind = kind; self.args = args }
    public init(from d: Decoder) throws {
        let c = try d.container(keyedBy: K.self)
        kind = (try? c.decode(String.self, forKey: .kind))
            ?? (try? c.decode(String.self, forKey: .action)) ?? ""
        args = (try? c.decode([String: Value].self, forKey: .args)) ?? [:]
    }
    enum K: String, CodingKey { case kind, action, args }
}

public struct Rule: Decodable, Identifiable {
    public var id: String
    public var when: EventMatch
    public var conditions: [Condition]
    public var actions: [ActionSpec]

    public init(id: String, when: EventMatch, conditions: [Condition] = [], actions: [ActionSpec]) {
        self.id = id; self.when = when; self.conditions = conditions; self.actions = actions
    }
    public init(from d: Decoder) throws {
        let c = try d.container(keyedBy: K.self)
        id = (try? c.decode(String.self, forKey: .id)) ?? UUID().uuidString
        when = (try? c.decode(EventMatch.self, forKey: .when)) ?? EventMatch(event: "")
        conditions = (try? c.decode([Condition].self, forKey: .conditions))
                  ?? (try? c.decode([Condition].self, forKey: .ifConds)) ?? []
        actions = (try? c.decode([ActionSpec].self, forKey: .actions))
               ?? (try? c.decode([ActionSpec].self, forKey: .doActs)) ?? []
    }
    enum K: String, CodingKey {
        case id, when, conditions, actions
        case ifConds = "if"
        case doActs = "do"
    }
}

// ─────────────────────────────────────────────────────────────────────────────

public final class RuleSystem: System {
    public let id = "rule"
    public private(set) var rules: [Rule]
    private var lastFired: [String: Double] = [:]
    private var firedOnce: Set<String> = []

    public init(rules: [Rule] = []) { self.rules = rules }
    public func add(_ rs: [Rule]) { rules.append(contentsOf: rs) }

    public func handle(_ event: EngineEvent, _ ctx: EngineContext) {
        let w = ctx.world
        for rule in rules where matches(rule, event, w) {
            guard passConditions(rule, event, w) else { continue }
            if rule.when.once {
                if firedOnce.contains(rule.id) { continue }
                firedOnce.insert(rule.id)
            }
            if rule.when.cooldown > 0 {
                if let last = lastFired[rule.id], w.time - last < rule.when.cooldown { continue }
                lastFired[rule.id] = w.time
            }
            run(rule.actions, event, ctx)
        }
    }

    private func matches(_ r: Rule, _ e: EngineEvent, _ w: World) -> Bool {
        let want = r.when.event
        if !want.isEmpty && want != "*" && want != e.name { return false }
        if let t = r.when.target, !t.isEmpty, t != "any", t != "scene" {
            guard let eid = e.entity, let ent = w.entities[eid] else { return false }
            if ent.name != t && !ent.tags.contains(t) && !ent.has(t) { return false }
        }
        return true
    }

    private func passConditions(_ r: Rule, _ e: EngineEvent, _ w: World) -> Bool {
        for cnd in r.conditions {
            let lhs = w.resolve(cnd.lhs, event: e, selfEntity: e.entity, target: e.entity)
            let rhs = resolveArg(cnd.rhs, w, e)
            if !Predicate.compare(lhs, cnd.op, rhs) { return false }
        }
        return true
    }

    /// An arg may be a literal Value or a "$path" reference resolved against the world.
    private func resolveArg(_ v: Value?, _ w: World, _ e: EngineEvent) -> Value? {
        if case .text(let s)? = v, s.hasPrefix("$") {
            return w.resolve(String(s.dropFirst()), event: e, selfEntity: e.entity, target: e.entity)
        }
        return v
    }

    private func run(_ actions: [ActionSpec], _ e: EngineEvent, _ ctx: EngineContext) {
        let w = ctx.world
        for a in actions {
            let A = a.args
            func arg(_ k: String) -> Value? { resolveArg(A[k], w, e) }

            switch a.kind {
            case "setState", "set":
                if let key = A["key"]?.asString { w.set(key, arg("value") ?? .null) }

            case "addState", "add", "addScore":
                let key = (a.kind == "addScore") ? A.str("key", BB.score) : A.str("key", BB.score)
                w.add(key, arg("amount")?.asDouble ?? arg("value")?.asDouble ?? 1)

            case "damage":
                if let tid = entityArg(A["target"], e, w) ?? e.other ?? e.entity, let ent = w.entities[tid] {
                    let dmg = arg("amount")?.asDouble ?? 1
                    let hp = (ent.field(C.health, "hp")?.asDouble ?? 0) - dmg
                    ent.setField(C.health, "hp", .number(hp))
                }

            case "heal":
                if let tid = entityArg(A["target"], e, w) ?? e.entity, let ent = w.entities[tid] {
                    let amt = arg("amount")?.asDouble ?? 1
                    let maxHp = ent.field(C.health, "max")?.asDouble ?? .greatestFiniteMagnitude
                    let hp = min(maxHp, (ent.field(C.health, "hp")?.asDouble ?? 0) + amt)
                    ent.setField(C.health, "hp", .number(hp))
                }

            case "spawn":
                let tmpl = A.str("template", "")
                let t = transformArg(A, e, w)
                if let spawned = w.spawn(fromTemplate: tmpl, at: t) {
                    if let imp = vecArg(A["impulse"], e, w) { w.command(.applyImpulse(spawned.id, imp)) }
                }

            case "despawn":
                if let tid = entityArg(A["target"], e, w) ?? e.entity { w.despawn(tid) }

            case "setComponentField":
                if let tid = entityArg(A["target"], e, w) ?? e.entity, let ent = w.entities[tid],
                   let type = A["component"]?.asString, let key = A["field"]?.asString {
                    ent.setField(type, key, arg("value") ?? .null)
                }

            case "playClip":
                if let tid = entityArg(A["target"], e, w) ?? e.entity {
                    w.command(.playClip(tid, A.str("clip", "demo"), A.bln("loop", true)))
                }

            case "applyEffect":
                let fx = VisualEffect(name: A.str("effect", "highlight"),
                                      hex: A["color"]?.asString,
                                      intensity: Float(arg("intensity")?.asDouble ?? 1))
                if let tid = entityArg(A["target"], e, w) ?? e.entity {
                    if let region = A["region"]?.asString, !region.isEmpty {
                        w.command(.applyEffect(.region(tid, region), fx))
                    } else {
                        w.command(.applyEffect(.entity(tid), fx))
                    }
                }

            case "advanceStep":
                w.emit(EngineEvent(Ev.stepAdvance))

            case "setStep":
                w.emit(EngineEvent("step.set", payload: ["index": arg("index") ?? .number(0)]))

            case "completeObjective":
                w.emit(EngineEvent("objective.force.complete", payload: ["id": A["id"] ?? .null]))
            case "failObjective":
                w.emit(EngineEvent("objective.force.fail", payload: ["id": A["id"] ?? .null]))

            case "emitSignal", "signal":
                if let name = A["name"]?.asString {
                    w.emit(EngineEvent(name, entity: e.entity, other: e.other, payload: A))
                }

            case "speak":
                if let t = A["text"]?.asString { w.command(.speak(t)) }
            case "playSound":
                w.command(.playSound(A.str("sound", "blip")))

            case "raycast":
                let origin = vecArg(A["origin"], e, w) ?? .zero
                let dir = vecArg(A["direction"], e, w) ?? Vec3(0, 0, -1)
                w.command(.raycast(A.str("id", "ray"), origin, dir, A.lst("mask").compactMap { $0.asString }))

            case "win":
                w.set(BB.outcome, .text("win")); w.emit(EngineEvent(Ev.win))
            case "lose":
                w.set(BB.outcome, .text("lose")); w.emit(EngineEvent(Ev.lose))

            case "hud":
                w.command(.hud(HudSpec(A)))

            default:
                break   // unknown action kind: ignore (forward-compat)
            }
        }
    }

    // MARK: arg helpers

    private func entityArg(_ v: Value?, _ e: EngineEvent, _ w: World) -> EntityID? {
        guard let v = v else { return nil }
        if case .text(let s) = v {
            switch s {
            case "self", "event", "$event.entity": return e.entity
            case "other", "$event.other":          return e.other
            case "target":                         return e.entity
            default:
                if s.hasPrefix("$"),
                   let d = w.resolve(String(s.dropFirst()), event: e, selfEntity: e.entity, target: e.entity)?.asDouble {
                    return EntityID(Int(d))
                }
                return w.find(name: s)?.id
            }
        }
        if let d = v.asDouble { return EntityID(Int(d)) }
        return nil
    }

    private func vecArg(_ v: Value?, _ e: EngineEvent, _ w: World) -> Vec3? {
        let rv = resolveArg(v, w, e)
        if let list = rv?.asList, list.count >= 3 {
            return Vec3(Float(list[0].asDouble ?? 0), Float(list[1].asDouble ?? 0), Float(list[2].asDouble ?? 0))
        }
        return nil
    }

    private func transformArg(_ A: [String: Value], _ e: EngineEvent, _ w: World) -> Transform? {
        if let p = vecArg(A["at"], e, w) ?? vecArg(A["position"], e, w) {
            return Transform(position: p)
        }
        if let s = A["atEntity"]?.asString, let ent = w.find(name: s) { return ent.transform }
        return nil
    }
}
