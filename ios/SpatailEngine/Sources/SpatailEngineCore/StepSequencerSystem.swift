import Foundation

/// One step of an explainer: narration + a clip + an effect on a target part/region.
/// This is today's `Step` (ModularContract.Step) lifted into the engine as data.
public struct StepSpec: Decodable {
    public var id: String
    public var title: String
    public var narration: String
    public var focus: String       // entity (logical id) to act on; "" -> first "hero"-tagged
    public var clip: String
    public var advance: String     // "tap" | "auto" | "trigger"
    public var loop: Bool
    public var target: String
    public var effect: String      // "highlight" | "emissive" | "ghost" | "tint:#hex" | ""
    public var region: String      // baked overlay region id

    public init(id: String = "", title: String = "", narration: String = "", focus: String = "",
                clip: String = "", advance: String = "tap", loop: Bool = true,
                target: String = "", effect: String = "", region: String = "") {
        self.id = id; self.title = title; self.narration = narration; self.focus = focus
        self.clip = clip; self.advance = advance; self.loop = loop
        self.target = target; self.effect = effect; self.region = region
    }
    public init(from d: Decoder) throws {
        let c = try d.container(keyedBy: K.self)
        id = (try? c.decode(String.self, forKey: .id)) ?? UUID().uuidString
        title = (try? c.decode(String.self, forKey: .title)) ?? ""
        narration = (try? c.decode(String.self, forKey: .narration)) ?? ""
        focus = (try? c.decode(String.self, forKey: .focus)) ?? ""
        clip = (try? c.decode(String.self, forKey: .clip)) ?? ""
        advance = (try? c.decode(String.self, forKey: .advance)) ?? "tap"
        loop = (try? c.decode(Bool.self, forKey: .loop)) ?? true
        target = (try? c.decode(String.self, forKey: .target)) ?? ""
        effect = (try? c.decode(String.self, forKey: .effect)) ?? ""
        region = (try? c.decode(String.self, forKey: .region)) ?? ""
    }
    enum K: String, CodingKey { case id, title, narration, focus, clip, advance, loop, target, effect, region }
}

/// Drives a linear explainer: applies a step (clip + effect + narration), advances on
/// tap (or `step.advance` / `step.set` events), and emits `sequence.complete` at the
/// end so an objective/rule can react. The same world the shooter uses — the explainer
/// is just a different set of Systems and data.
public final class StepSequencerSystem: System {
    public let id = "step"
    public var steps: [StepSpec]
    private var index = 0

    public init(steps: [StepSpec] = []) { self.steps = steps }

    public func setup(_ ctx: EngineContext) {
        ctx.world.set(BB.stepCount, .number(Double(steps.count)))
        if !steps.isEmpty { apply(0, ctx) }
    }

    public func handle(_ event: EngineEvent, _ ctx: EngineContext) {
        switch event.name {
        case Ev.tap:
            if steps.indices.contains(index), steps[index].advance == "tap" { advance(ctx) }
        case Ev.stepAdvance:
            advance(ctx)
        case "step.set":
            if let i = event.payload["index"]?.asDouble {
                apply(min(max(0, Int(i)), max(0, steps.count - 1)), ctx)
            }
        default:
            break
        }
    }

    private func advance(_ ctx: EngineContext) {
        if index >= steps.count - 1 {
            ctx.world.set("sequence.atEnd", .bool(true))
            ctx.world.emit(EngineEvent(Ev.sequenceComplete))
            return
        }
        apply(index + 1, ctx)
    }

    private func apply(_ i: Int, _ ctx: EngineContext) {
        guard steps.indices.contains(i) else { return }
        index = i
        let w = ctx.world
        let s = steps[i]
        w.set(BB.stepIndex, .number(Double(i)))
        w.set("step.title", .text(s.title))

        let focus = (s.focus.isEmpty ? nil : w.find(name: s.focus)) ?? w.query(tag: "hero").first
        if let focus = focus {
            if !s.clip.isEmpty { w.command(.playClip(focus.id, s.clip, s.loop)) }
            if !s.effect.isEmpty || !s.target.isEmpty || !s.region.isEmpty {
                let fx = VisualEffect.from(s.effect)
                if !s.region.isEmpty { w.command(.applyEffect(.region(focus.id, s.region), fx)) }
                else { w.command(.applyEffect(.entity(focus.id), fx)) }
            }
        }
        if !s.narration.isEmpty { w.command(.speak(s.narration)) }
        w.emit(EngineEvent(Ev.stepChanged, payload: ["index": .number(Double(i)), "title": .text(s.title)]))
        if i == steps.count - 1 { w.set("sequence.atEnd", .bool(true)) }
    }
}
