import Foundation

/// A System is a unit of native behavior. It reads the world + events and mutates the
/// world / emits commands. This is the ONLY place per-frame numeric code lives. A kit
/// is just an ordered list of Systems. Adding a genre = adding Systems, never an
/// `if` in the engine core.
public protocol System: AnyObject {
    var id: String { get }
    func setup(_ ctx: EngineContext)
    func update(_ ctx: EngineContext, dt: Double)
    func handle(_ event: EngineEvent, _ ctx: EngineContext)
}

public extension System {
    func setup(_ ctx: EngineContext) {}
    func update(_ ctx: EngineContext, dt: Double) {}
    func handle(_ event: EngineEvent, _ ctx: EngineContext) {}
}

/// Passed to every System call: the world plus the kit's normalized params.
public final class EngineContext {
    public let world: World
    public var params: [String: Value]
    public init(world: World, params: [String: Value]) {
        self.world = world
        self.params = params
    }
    public func param(_ k: String, _ d: Double = 0) -> Double { params[k]?.asDouble ?? d }
    public func paramS(_ k: String, _ d: String = "") -> String { params[k]?.asString ?? d }
}
