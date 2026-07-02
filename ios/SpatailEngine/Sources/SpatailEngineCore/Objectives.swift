import Foundation

// Objectives / goals: the "what was asked for" expressed as success/failure
// predicates over world state. The ObjectiveSystem evaluates them every tick and
// publishes progress to the blackboard so rules (e.g. "all objectives complete -> win")
// and the HUD can react. Goals are data, authored at runtime by the brain.

public struct Objective: Decodable, Identifiable {
    public var id: String
    public var title: String
    public var success: [Condition]        // ALL true -> complete
    public var failure: [Condition]        // ALL true -> failed
    public var optional: Bool              // optional objectives don't gate "allComplete"

    // Rewards on completion are authored as a rule:  when "objective.completed"
    // (id == "<this>") -> do [...].  Keeping objectives pure predicates means there is
    // exactly one place actions run (the RuleSystem).

    public init(id: String, title: String = "",
                success: [Condition] = [], failure: [Condition] = [],
                optional: Bool = false) {
        self.id = id; self.title = title; self.success = success
        self.failure = failure; self.optional = optional
    }
    public init(from d: Decoder) throws {
        let c = try d.container(keyedBy: K.self)
        id = (try? c.decode(String.self, forKey: .id)) ?? UUID().uuidString
        title = (try? c.decode(String.self, forKey: .title)) ?? ""
        success = (try? c.decode([Condition].self, forKey: .success))
               ?? (try? c.decode([Condition].self, forKey: .when)) ?? []
        failure = (try? c.decode([Condition].self, forKey: .failure)) ?? []
        optional = (try? c.decode(Bool.self, forKey: .optional)) ?? false
    }
    enum K: String, CodingKey { case id, title, success, when, failure, optional }
}

public enum ObjectiveStatus: String { case active, complete, failed }

public final class ObjectiveSystem: System {
    public let id = "objective"
    public private(set) var objectives: [Objective]
    public private(set) var status: [String: ObjectiveStatus] = [:]

    public init(objectives: [Objective] = []) { self.objectives = objectives }
    public func add(_ o: [Objective]) { objectives.append(contentsOf: o) }

    public func setup(_ ctx: EngineContext) {
        for o in objectives where status[o.id] == nil { status[o.id] = .active }
        publish(ctx)
    }

    public func handle(_ event: EngineEvent, _ ctx: EngineContext) {
        switch event.name {
        case "objective.force.complete":
            if let id = event.payload["id"]?.asString { complete(id, ctx) }
        case "objective.force.fail":
            if let id = event.payload["id"]?.asString { fail(id, ctx) }
        case Ev.tick:
            evaluate(ctx)
        default:
            break
        }
    }

    private func evaluate(_ ctx: EngineContext) {
        let w = ctx.world
        for o in objectives where status[o.id] == .active {
            if !o.failure.isEmpty, allPass(o.failure, w) { fail(o.id, ctx); continue }
            if !o.success.isEmpty, allPass(o.success, w) { complete(o.id, ctx) }
        }
        publish(ctx)
    }

    private func allPass(_ cs: [Condition], _ w: World) -> Bool {
        for c in cs {
            let lhs = w.resolve(c.lhs)
            if !Predicate.compare(lhs, c.op, c.rhs) { return false }
        }
        return true
    }

    private func complete(_ id: String, _ ctx: EngineContext) {
        guard status[id] == .active else { return }
        status[id] = .complete
        publish(ctx)   // publish BEFORE the event so "objectives.allComplete" is current when rules react
        ctx.world.emit(EngineEvent(Ev.objectiveCompleted, payload: ["id": .text(id)]))
    }

    private func fail(_ id: String, _ ctx: EngineContext) {
        guard status[id] == .active else { return }
        status[id] = .failed
        ctx.world.emit(EngineEvent(Ev.objectiveFailed, payload: ["id": .text(id)]))
        publish(ctx)
    }

    private func publish(_ ctx: EngineContext) {
        let required = objectives.filter { !$0.optional }
        let done = required.filter { status[$0.id] == .complete }.count
        let failedAny = objectives.contains { status[$0.id] == .failed && !$0.optional }
        let w = ctx.world
        w.set(BB.objectivesTotal, .number(Double(required.count)))
        w.set(BB.objectivesComplete, .number(Double(done)))
        w.set(BB.objectivesAllDone, .bool(!required.isEmpty && done == required.count))
        w.set(BB.objectivesFailed, .bool(failedAny))
    }
}
