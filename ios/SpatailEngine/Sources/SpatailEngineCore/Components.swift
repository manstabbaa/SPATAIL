import Foundation

/// Well-known component type names. These are the components the built-in native
/// Systems understand. A contract can carry ANY component type string; unknown ones
/// simply aren't acted on by a System (but rules can still read/write their fields),
/// which is how new behavior is added as data first, native System later.
public enum C {
    public static let health      = "health"      // { hp, max }
    public static let team        = "team"        // { name }
    public static let lifetime    = "lifetime"    // { ttl }  seconds remaining -> auto-despawn
    public static let velocity    = "velocity"    // { x, y, z }  (kinematic mover; physics objects are backend-owned)
    public static let projectile  = "projectile"  // { damage, speed }
    public static let spawner     = "spawner"     // { template, interval, remaining, infinite, jitter, elapsed }
    public static let score       = "score"       // per-entity score holder (rare; usually blackboard "score")
    public static let interactive = "interactive" // { } marks an entity as tap/hit eligible
    public static let label       = "label"       // { text }
    public static let asset       = "asset"       // { ref } -> AssetSpec id (presentation)
}

/// Well-known blackboard keys the systems read/write. The blackboard is the global
/// world state every rule and objective sees.
public enum BB {
    public static let score          = "score"
    public static let outcome        = "outcome"            // "win" | "lose"
    public static let stepIndex      = "step.index"
    public static let stepCount      = "step.count"
    public static let objectivesTotal     = "objectives.total"
    public static let objectivesComplete  = "objectives.completed"
    public static let objectivesAllDone   = "objectives.allComplete"
    public static let objectivesFailed    = "objectives.failed"
}
