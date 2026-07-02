import Foundation

// Small, genre-neutral native Systems composed by kits. Each is the kind of
// per-frame / event behavior the litmus says must be compiled code, not data.

/// Emits `entity.died` once when an entity's health reaches zero. It does NOT despawn
/// the entity — the entity must stay alive while the `died` event is delivered so rules
/// can still read its tags/components (score by team, drop loot, etc.). A rule does the
/// despawn (e.g. ShooterKit's "killed" law: `addScore` + `despawn self`). The "dead"
/// flag guards against re-emitting before that despawn lands.
public final class HealthSystem: System {
    public let id = "health"
    public init() {}
    public func handle(_ event: EngineEvent, _ ctx: EngineContext) {
        guard event.name == Ev.tick else { return }
        let w = ctx.world
        for e in w.query(component: C.health) where e.alive {
            let hp = e.field(C.health, "hp")?.asDouble ?? 0
            guard hp <= 0, e.field(C.health, "dead")?.asBool != true else { continue }
            e.setField(C.health, "dead", .bool(true))
            w.emit(EngineEvent(Ev.died, entity: e.id,
                               payload: e.template.map { ["template": .text($0)] } ?? [:]))
        }
    }
}

/// Counts down `lifetime.ttl` and despawns at zero. Projectiles, transient VFX.
public final class LifetimeSystem: System {
    public let id = "lifetime"
    public init() {}
    public func update(_ ctx: EngineContext, dt: Double) {
        let w = ctx.world
        for e in w.query(component: C.lifetime) where e.alive {
            let ttl = (e.field(C.lifetime, "ttl")?.asDouble ?? 0) - dt
            if ttl <= 0 { w.despawn(e.id) }
            else { e.setField(C.lifetime, "ttl", .number(ttl)) }
        }
    }
}

/// Periodically spawns a template from any entity carrying a `spawner` component.
/// Drives waves without bespoke code: { template, interval, remaining|infinite, jitter }.
public final class SpawnerSystem: System {
    public let id = "spawner"
    public init() {}
    public func update(_ ctx: EngineContext, dt: Double) {
        let w = ctx.world
        for e in w.query(component: C.spawner) where e.alive {
            guard var sp = e.comp(C.spawner) else { continue }
            let infinite = sp.num("infinite", 0) != 0
            var remaining = sp.num("remaining", 0)
            if !infinite && remaining <= 0 { continue }

            let interval = max(0.1, sp.num("interval", 2))
            var elapsed = sp.num("elapsed", 0) + dt
            if elapsed >= interval {
                elapsed = 0
                let tmpl = sp.str("template", "")
                if !tmpl.isEmpty {
                    var t = e.transform
                    let jitter = Float(sp.num("jitter", 0))
                    if jitter > 0 {
                        t.position = t.position + Vec3(Float.random(in: -jitter...jitter), 0,
                                                       Float.random(in: -jitter...jitter))
                    }
                    w.spawn(fromTemplate: tmpl, at: t)
                }
                if !infinite { remaining -= 1 }
            }
            sp.set("elapsed", .number(elapsed))
            sp.set("remaining", .number(remaining))
            e.set(sp)
        }
    }
}

/// Optional kinematic mover for entities the core owns (non-physics). Physics objects
/// are integrated by the backend; this is for simple authored drift/rotation.
public final class MoverSystem: System {
    public let id = "mover"
    public init() {}
    public func update(_ ctx: EngineContext, dt: Double) {
        let w = ctx.world
        for e in w.query(component: C.velocity) where e.alive {
            let v = Vec3(Float(e.field(C.velocity, "x")?.asDouble ?? 0),
                         Float(e.field(C.velocity, "y")?.asDouble ?? 0),
                         Float(e.field(C.velocity, "z")?.asDouble ?? 0))
            guard v.length > 1e-6 else { continue }
            e.transform.position = e.transform.position + v * Float(dt)
            w.command(.updateTransform(e.id, e.transform))
        }
    }
}
