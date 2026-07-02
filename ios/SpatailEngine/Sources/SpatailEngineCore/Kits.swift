import Foundation

// A Kit is a genre expressed as: which Systems run (in order), plus baseline rules
// and objectives the genre always provides. The brain picks a kit and fills its
// params; the contract may add more rules/objectives/entities on top. Adding a new
// genre = writing one Kit, never touching the engine core.

public protocol Kit {
    var id: String { get }
    func systems(for contract: ExperienceContract) -> [System]
    func defaultRules() -> [Rule]
    func defaultObjectives(params: [String: Value]) -> [Objective]
    func normalize(params: [String: Value]) -> [String: Value]
}

public extension Kit {
    func defaultRules() -> [Rule] { [] }
    func defaultObjectives(params: [String: Value]) -> [Objective] { [] }
    func normalize(params: [String: Value]) -> [String: Value] { params }
}

public final class KitRegistry {
    public static let shared = KitRegistry()
    private var kits: [String: Kit] = [:]
    public init() {
        register(ExplainerKit())
        register(ShooterKit())
    }
    public func register(_ k: Kit) { kits[k.id] = k }
    public func kit(_ id: String) -> Kit? { kits[id] }
}

// ─────────────────────────────────────────────────────────────────────────────
// Explainer: today's runtime, expressed as Systems. The step sequencer drives it;
// rules/objectives are optional (e.g. a quiz objective).
public struct ExplainerKit: Kit {
    public let id = "explainer"
    public init() {}
    public func systems(for c: ExperienceContract) -> [System] {
        [ StepSequencerSystem(steps: c.steps), RuleSystem(), ObjectiveSystem() ]
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Shooter: same world, different Systems + a handful of baseline laws. Geometry
// (projectile trajectory, collision) is native (backend physics); the kit only
// authors the discrete reactions and the goal.
public struct ShooterKit: Kit {
    public let id = "shooter"
    public init() {}

    public func systems(for c: ExperienceContract) -> [System] {
        [ RuleSystem(), ObjectiveSystem(), HealthSystem(), LifetimeSystem(), SpawnerSystem() ]
    }

    public func normalize(params: [String: Value]) -> [String: Value] {
        var p = params
        if p["targetScore"] == nil { p["targetScore"] = .number(5) }
        if p["points"] == nil { p["points"] = .number(1) }
        return p
    }

    public func defaultRules() -> [Rule] {
        // FIRE: the backend reports the camera ray in the event; spawn a physics
        // projectile and shove it down that ray. Trajectory is integrated by the
        // backend (native); we only authored the cause and the impulse.
        let fire = Rule(
            id: "shooter.fire",
            when: EventMatch(event: Ev.fire),
            actions: [
                ActionSpec("spawn", [
                    "template": .text("projectile"),
                    "at": .text("$event.origin"),
                    "impulse": .text("$event.impulse")
                ])
            ])

        // HIT: a projectile struck a target -> damage it, remove the projectile.
        // Backend convention: in a collision event, `entity` is the projectile.
        let hit = Rule(
            id: "shooter.hit",
            when: EventMatch(event: Ev.collision),
            conditions: [
                Condition("self.tag.projectile", .eq, .bool(true)),
                Condition("other.tag.target", .eq, .bool(true))
            ],
            actions: [
                ActionSpec("damage", ["target": .text("other"), "amount": .text("$self.projectile.damage")]),
                ActionSpec("despawn", ["target": .text("self")])
            ])

        // KILL: a target died -> award points, then remove it. (HealthSystem only
        // flags+announces death; the despawn is a law so the entity is still readable
        // while this rule runs.)
        let killed = Rule(
            id: "shooter.score",
            when: EventMatch(event: Ev.died, target: "target"),
            actions: [
                ActionSpec("addScore", ["key": .text(BB.score), "amount": .text("$param.points")]),
                ActionSpec("despawn", ["target": .text("self")])
            ])

        // WIN: when all objectives are complete, win once.
        let win = Rule(
            id: "shooter.win",
            when: EventMatch(event: Ev.objectiveCompleted, once: true),
            conditions: [ Condition("blackboard.\(BB.objectivesAllDone)", .eq, .bool(true)) ],
            actions: [ ActionSpec("win") ])

        return [fire, hit, killed, win]
    }

    public func defaultObjectives(params: [String: Value]) -> [Objective] {
        let target = params["targetScore"]?.asDouble ?? 5
        return [
            Objective(id: "reach_score",
                      title: "Score \(Int(target))",
                      success: [ Condition("blackboard.\(BB.score)", .gte, .number(target)) ])
        ]
    }
}
