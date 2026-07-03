// TriggerVM.swift — the contract's `triggers` (event → action bindings) executed
// genre-agnostically. Port of the legacy ModularRuntime trigger dispatcher
// (fireTriggers / executeActions / checkSpatialTriggers) lifted into its own VM
// so ExperienceRuntime stays the scene owner and this stays pure event logic.
//
// Events (normalized, "on" prefix optional, case-insensitive):
//   tap        — tap on an element (target = asset id / part name) or "scene"
//   stepEnter  — a sequence step became active (targets: step id, "step<N>", index, "scene")
//   timer      — params: delay_s (once after present) or every_s (repeating)
//   approach   — camera within params.radius_m of the target element (re-arms at 1.3×)
//   gaze       — camera looked at the target for params.dwell_s seconds
//   quizCorrect— a quiz panel answered correctly (targets: step id, "scene")
//   grab       — mapped to tap (no grab gesture on the phone lens yet)
//
// Actions (contract vocabulary from studio/director/logic.py):
//   advance / advanceTrack / advanceObjective → step forward
//   playClip / playClipIfAny                  → named clip on a target element
//   label                                     → floating 3-D label near the target
//   playSound                                 → params.cue ("tap"/"correct"/"pickup")
//   mechanic                                  → named affordance ("highlight"/"label"/…)
//   highlight / emissive / ghost / tint:#hex  → direct effect on the target
//
// All scene touches route through the actuator closures (the StepDirector's
// transient, restorable touch discipline) — the VM never mutates entities.

import Foundation
import simd
import QuartzCore

@MainActor
final class TriggerVM {

    /// The VM's hands — every action lands through these (wired by the runtime).
    struct Actuator {
        var advance: () -> Void
        var playClip: (_ target: String?, _ clip: String?) -> Void
        var showLabel: (_ target: String) -> Void
        var playSound: (_ cue: String) -> Void
        var applyEffect: (_ target: String, _ effect: String) -> Void
    }

    private let triggers: [ModularContract.Trigger]
    private let actuator: Actuator
    private var firedOnce: Set<String> = []
    private var gazeStart: [String: TimeInterval] = [:]
    /// Per-trigger timer state (index into `triggers`): last fire time.
    private var timerLastFired: [Int: TimeInterval] = [:]
    private var timerDone: Set<Int> = []
    private let startedAt: TimeInterval

    init(triggers: [ModularContract.Trigger], actuator: Actuator) {
        self.triggers = triggers
        self.actuator = actuator
        self.startedAt = CACurrentMediaTime()
    }

    var isEmpty: Bool { triggers.isEmpty }

    // MARK: event entry points

    /// Fire an event against any of `targets` (checked in order: most specific
    /// first, "scene" last). Returns true when at least one trigger executed —
    /// the caller then does NOT fall through to the default behavior.
    @discardableResult
    func fire(event: String, targets: [String]) -> Bool {
        let ev = Self.normalize(event)
        var fired = false
        for target in targets {
            let matches = triggers.filter {
                Self.normalize($0.when.event) == ev
                    && Self.targetMatches($0.when.target, target)
            }
            for t in matches {
                execute(t.doActions, defaultTarget: target)
                fired = true
            }
            if fired { break }   // most-specific target wins; don't double-fire scene
        }
        return fired
    }

    /// Step entry: onStepEnter triggers match the step id, "step<N>", the
    /// 1-based index, or "scene".
    func stepEntered(index: Int, step: ModularContract.Step) {
        _ = fire(event: "stepEnter",
                 targets: [step.id, "step\(index + 1)", "\(index + 1)", "scene"])
    }

    // MARK: per-frame (timers + approach + gaze — trivial math only)

    /// `elementWorld` resolves an element id to its world position; `elementIds`
    /// enumerates the placed elements. Camera may be nil before the first frame.
    func tick(now: TimeInterval,
              camera: (position: SIMD3<Float>, forward: SIMD3<Float>)?,
              elementIds: [String],
              elementWorld: (String) -> SIMD3<Float>?) {
        // timers
        for (i, t) in triggers.enumerated() where Self.normalize(t.when.event) == "timer" {
            let every = t.when.params.d("every_s", 0)
            let delay = t.when.params.d("delay_s", 0)
            if every > 0 {
                let last = timerLastFired[i] ?? startedAt
                if now - last >= every {
                    timerLastFired[i] = now
                    execute(t.doActions, defaultTarget: t.when.target)
                }
            } else if delay > 0, !timerDone.contains(i), now - startedAt >= delay {
                timerDone.insert(i)
                execute(t.doActions, defaultTarget: t.when.target)
            }
        }

        // approach / gaze (ported from the legacy checkSpatialTriggers)
        guard let camera else { return }
        let camFwdFlat3 = SIMD3(camera.forward.x, 0, camera.forward.z)
        let camFwd = simd_length(camFwdFlat3) > 1e-4
            ? simd_normalize(camFwdFlat3) : SIMD3<Float>(0, 0, -1)

        for id in elementIds {
            guard let world = elementWorld(id) else { continue }
            let to = world - camera.position
            let dist = simd_length(to)

            for t in triggers where Self.normalize(t.when.event) == "approach"
                && Self.targetMatches(t.when.target, id) {
                let r = t.when.params.f("radius_m", 0.7)
                let key = "near:" + id
                if dist <= r, !firedOnce.contains(key) {
                    firedOnce.insert(key)
                    execute(t.doActions, defaultTarget: id)
                } else if dist > r * 1.3 {
                    firedOnce.remove(key)
                }
            }

            for t in triggers where Self.normalize(t.when.event) == "gaze"
                && Self.targetMatches(t.when.target, id) {
                let flat3 = SIMD3(to.x, 0, to.z)
                let flat = simd_length(flat3) > 1e-4 ? simd_normalize(flat3) : camFwd
                let dwell = t.when.params.d("dwell_s", 1.0)
                let key = "gaze:" + id
                if simd_dot(flat, camFwd) > 0.96 {
                    let start = gazeStart[id] ?? now
                    gazeStart[id] = start
                    if now - start >= dwell, !firedOnce.contains(key) {
                        firedOnce.insert(key)
                        execute(t.doActions, defaultTarget: id)
                    }
                } else {
                    gazeStart[id] = nil
                    firedOnce.remove(key)
                }
            }
        }
    }

    // MARK: action execution

    private func execute(_ actions: [ModularContract.Action], defaultTarget: String) {
        for a in actions {
            let tgt = a.target ?? defaultTarget
            switch a.action {
            case "advance", "advanceTrack", "advanceObjective":
                actuator.advance()
            case "playClip", "playClipIfAny":
                actuator.playClip(tgt, a.clip)
            case "label":
                actuator.showLabel(tgt)
            case "playSound":
                actuator.playSound(a.params.s("cue", "tap"))
            case "mechanic":
                switch (a.mechanic ?? "highlight").lowercased() {
                case "label":         actuator.showLabel(tgt)
                case "sound":         actuator.playSound(a.params.s("cue", "tap"))
                case let m:           actuator.applyEffect(tgt, m)   // highlight/emissive/…
                }
            case "highlight", "emissive", "ghost":
                actuator.applyEffect(tgt, a.action)
            case let t where t.hasPrefix("tint:"):
                actuator.applyEffect(tgt, t)
            case "cycleState":
                // No state machine on-device yet — read as an emphasis pulse so
                // the world still visibly reacts (the brain's intent: "it responds").
                actuator.applyEffect(tgt, "highlight")
            default:
                break   // unknown actions are tolerated, never fatal
            }
        }
    }

    // MARK: matching

    /// "onTap" == "tap" == "TAP"; "onStepEnter" == "step_enter" == "stepenter".
    private static func normalize(_ event: String) -> String {
        var e = event.lowercased()
            .replacingOccurrences(of: "_", with: "")
            .replacingOccurrences(of: ".", with: "")
        if e.hasPrefix("on") { e = String(e.dropFirst(2)) }
        if e == "grab" { e = "tap" }            // no grab gesture on the phone lens
        return e
    }

    private static func targetMatches(_ pattern: String, _ target: String) -> Bool {
        if pattern.isEmpty || pattern == "*" || pattern == "any" { return true }
        if pattern == target { return true }
        // tolerate the brain naming a part/asset loosely ("cap" vs "bottle_cap")
        let p = pattern.lowercased(), t = target.lowercased()
        return p == t || (t.count > 2 && p.contains(t)) || (p.count > 2 && t.contains(p))
    }
}
