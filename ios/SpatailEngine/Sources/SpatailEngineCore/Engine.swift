import Foundation

/// The facade the host drives. Lifecycle:
///   1. `load(contract)`  — resolve the kit, build Systems, seed the world, spawn the
///                          initial scene (queues createVisual commands), install
///                          rules/objectives, run setup, flush commands to the backend.
///   2. `submit(event)`   — push a backend/host event (tap, fire, collision, …). The
///                          relevant Systems react; resulting commands flush out.
///   3. `tick(dt)`        — per frame: run System.update, emit the `tick` event (drives
///                          time-based rules + objective evaluation + health), flush.
///
/// The engine never imports a rendering framework. `backend` is the only platform code.
public final class SpatailEngine {
    public let world = World()
    public private(set) var systems: [System] = []
    public weak var backend: SceneBackend?
    public private(set) var ctx: EngineContext
    public private(set) var contract: ExperienceContract?

    private var ruleSystem: RuleSystem?
    private var objectiveSystem: ObjectiveSystem?

    public init(backend: SceneBackend? = nil) {
        self.backend = backend
        self.ctx = EngineContext(world: world, params: [:])
    }

    // MARK: load

    public func load(_ c: ExperienceContract) {
        contract = c

        // 1. assets + templates + initial blackboard
        for a in c.assets { world.assets[a.id] = a }
        for t in c.templates { world.templates[t.id] = t }
        world.blackboard = c.blackboard

        // 2. kit + params (params mirrored into blackboard under "param.*" for rules)
        let kit = KitRegistry.shared.kit(c.kit.isEmpty ? c.genre : c.kit) ?? ExplainerKit()
        let params = kit.normalize(params: c.params)
        ctx.params = params
        for (k, v) in params { world.set("param." + k, v) }

        // 3. systems (explicit override, else the kit decides)
        if let ids = c.systemsOverride {
            systems = ids.compactMap { Self.system(named: $0, contract: c) }
        } else {
            systems = kit.systems(for: c)
        }
        ruleSystem = systems.compactMap { $0 as? RuleSystem }.first
        objectiveSystem = systems.compactMap { $0 as? ObjectiveSystem }.first

        // 4. laws + goals: kit defaults first, then contract additions
        ruleSystem?.add(kit.defaultRules())
        ruleSystem?.add(c.rules)
        objectiveSystem?.add(kit.defaultObjectives(params: params))
        objectiveSystem?.add(c.objectives)

        // 5. spawn the initial scene (queue visuals + physics)
        for spec in c.entities {
            let e = spec.instantiate(in: world)
            if let vs = spec.visualSpec(assets: world.assets) { world.command(.createVisual(e.id, vs)) }
            if let ps = spec.physicsSpec() { world.command(.addPhysics(e.id, ps)) }
        }

        // 6. anchor the experience (object-tracked or world-placed)
        world.command(.anchor(AnchorSpec(mode: c.anchoring.mode,
                                         referenceObjectURL: c.anchoring.referenceObjectUrl.isEmpty ? nil : c.anchoring.referenceObjectUrl)))

        // 7. setup + first flush
        for s in systems { s.setup(ctx) }
        drain()
        flush()
    }

    // MARK: drive

    /// Push an event from the host/backend (input.tap, input.fire, collision, …).
    public func submit(_ event: EngineEvent) {
        world.emit(event)
        drain()
        flush()
    }

    /// Advance one frame.
    public func tick(_ dt: Double) {
        world.advanceTime(dt)
        for s in systems { s.update(ctx, dt: dt) }
        world.emit(EngineEvent(Ev.tick, payload: ["dt": .number(dt)]))
        drain()
        flush()
    }

    // MARK: internals

    /// Deliver queued events to every System until the queue is empty (bounded, so a
    /// rule cascade can't hang the frame).
    private func drain() {
        var guardN = 0
        while !world.events.isEmpty && guardN < 20_000 {
            let batch = world.events
            world.events.removeAll(keepingCapacity: true)
            for e in batch {
                for s in systems { s.handle(e, ctx) }
                guardN += 1
            }
        }
    }

    /// Hand the frame's commands to the backend (no-op drain if headless).
    private func flush() {
        let cmds = world.commands
        world.commands.removeAll(keepingCapacity: true)
        guard let backend = backend else { return }
        for c in cmds { backend.apply(c) }
    }

    static func system(named id: String, contract: ExperienceContract) -> System? {
        switch id {
        case "step":      return StepSequencerSystem(steps: contract.steps)
        case "rule":      return RuleSystem()
        case "objective": return ObjectiveSystem()
        case "health":    return HealthSystem()
        case "lifetime":  return LifetimeSystem()
        case "spawner":   return SpawnerSystem()
        case "mover":     return MoverSystem()
        default:          return nil
        }
    }
}
