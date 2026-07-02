import Foundation

/// The one thing the platform pushes INTO the core, and the way Systems talk to each
/// other. Backend events (tap, collision, raycast hit, anchored, frame) and internal
/// signals (step.changed, entity.died, objective.completed, custom rule signals) are
/// all `EngineEvent`s. Rules match on `name` (+ optional target).
public struct EngineEvent {
    public var name: String
    public var entity: EntityID?     // primary subject (tapped entity, projectile in a collision, ...)
    public var other: EntityID?      // secondary subject (the thing hit)
    public var payload: [String: Value]

    public init(_ name: String,
                entity: EntityID? = nil,
                other: EntityID? = nil,
                payload: [String: Value] = [:]) {
        self.name = name
        self.entity = entity
        self.other = other
        self.payload = payload
    }
}

/// Canonical event names. Strings are open — a contract may emit/match custom signals
/// — but these are the ones the built-in Systems produce/consume.
public enum Ev {
    // pushed in by the backend / host
    public static let tick      = "tick"          // payload: { dt }
    public static let tap       = "input.tap"     // entity = tapped (nil = empty space)
    public static let fire      = "input.fire"    // payload: { origin:[x,y,z], impulse:[x,y,z] } (camera ray)
    public static let drag      = "input.drag"    // payload: { dx, dy }
    public static let collision = "collision"     // entity = projectile/initiator, other = hit body, payload: { point }
    public static let raycastHit = "raycast.hit"  // payload: { id }, entity = hit, payload.point
    public static let anchored  = "anchored"      // payload: { mode }
    // emitted internally
    public static let spawned   = "entity.spawned"
    public static let despawned = "entity.despawned"
    public static let died      = "entity.died"
    public static let stepAdvance = "step.advance"
    public static let stepChanged = "step.changed"
    public static let sequenceComplete = "sequence.complete"
    public static let objectiveCompleted = "objective.completed"
    public static let objectiveFailed    = "objective.failed"
    public static let win  = "game.win"
    public static let lose = "game.lose"
}
