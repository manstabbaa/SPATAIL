// StepDirector.swift — the GAME MANAGER half of the runtime (v0.7 sequencing).
// Port of the proven ios/_legacy_Spatail/Sources/ModularRuntime.swift step logic,
// adapted to the 2026 diff-based scene discipline:
//
//   • The legacy runtime OWNED the scene (anchors, holders, rebuild-on-present).
//     Here the scene belongs to ExperienceRuntime and is diff-only by asset id;
//     the director only touches the entities a step names — clip playback,
//     focus/isolate opacity, part effects, markers, transient labels — and every
//     one of those touches is stashed and RESTORED on the next step (never
//     removeAll + rebuild).
//   • Motion is BAKED into each asset as named clips; the director PLAYS and
//     SEQUENCES those clips per step (started/stopped per step — not
//     auto-play-all-looped).
//   • Narration/panels moved OFF the 3D text bubbles onto the design-system
//     glass panel (StepPanelView) — the director publishes a StepHUD snapshot
//     (title, narration, panels, the 3D anchor point the panel pins beside).
//
// The §2 anchor hierarchy is preserved verbatim from the legacy runtime:
// STORY-baked region overlay → contract anchorOffset → target/keyword fuzzy
// name match → deterministic bbox points.

import Foundation
import RealityKit
import UIKit
import AVFoundation
import AudioToolbox
import simd

// MARK: - Narration (shared voice: modular steps + engine `speak` commands)

@MainActor
final class SpeechNarrator: NSObject, AVSpeechSynthesizerDelegate {
    private let synth = AVSpeechSynthesizer()
    /// Fires (on main) when the CURRENT utterance finishes — auto-advance hook.
    var onFinished: (() -> Void)?

    override init() {
        super.init()
        synth.delegate = self
        // .playback so narration survives the ring/silent switch (the default
        // .soloAmbient is muted by it, which reads as "narration is broken").
        try? AVAudioSession.sharedInstance().setCategory(.playback, options: [.mixWithOthers])
        try? AVAudioSession.sharedInstance().setActive(true)
    }

    func speak(_ text: String) {
        guard !text.isEmpty else { return }
        stop()
        let u = AVSpeechUtterance(string: text)
        u.rate = 0.5
        synth.speak(u)
    }

    func stop() { synth.stopSpeaking(at: .immediate) }

    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer,
                                       didFinish utterance: AVSpeechUtterance) {
        Task { @MainActor in self.onFinished?() }
    }
}

// MARK: - StepHUD (what the glass panel renders)

/// A published snapshot of the active step for the 2-D design-system layer.
struct StepHUD: Equatable {
    struct Panel: Equatable {
        var kind: String        // "fact" | "quiz" | "title" | …
        var title: String
        var body: String
        var question: String
        var options: [String]
        var answer: Int
    }
    var index: Int
    var count: Int
    var title: String
    var narration: String
    var panels: [Panel]
    var advance: String                 // "tap" | "auto" | "trigger"
    /// World-space point the step explains (region/part/bbox anchor) — the glass
    /// panel pins beside its screen projection, per the contract's locus.
    var anchorWorld: SIMD3<Float>?
    /// The contract's panel locus (placement.ui.panelPosition): "beside_object"…
    var locus: String
    var experienceTitle: String
}

// MARK: - StepDirector

@MainActor
final class StepDirector {

    // Wiring (closures — the director never owns the scene or the report path)
    private let root: Entity                                   // modularRoot
    private let node: (String) -> Entity?                      // assetId → container
    private let assets: [ModularContract.Asset]
    private let steps: [ModularContract.Step]
    private let policy: ModularContract.PlacementPolicy
    private let experienceTitle: String
    private let narrator: SpeechNarrator
    private let onHUD: (StepHUD?) -> Void
    /// Fired on step entry so the trigger VM can run onStepEnter bindings.
    var onStepEnter: ((Int, ModularContract.Step) -> Void)?
    /// Fired when a step-driven change MOVED content (focus lift/restore) so the
    /// runtime re-emits the placement report ("step 3 reposition" corrections).
    var onContentMoved: ((String) -> Void)?

    // Step state
    private(set) var stepIndex = 0
    private var autoAdvanceAt: TimeInterval?          // advance == "auto" deadline

    // Transient scene touches (stashed → restored on the next step; diff-only)
    private var stepUI: [Entity] = []
    private var pulseMarkers: [Entity] = []
    private var tintedParts: [(ModelEntity, [any RealityKit.Material])] = []
    private var ghostedParts: [Entity] = []
    private var enabledOverlays: [Entity] = []
    private var dimmedContainers: [Entity] = []
    private var labels: [Entity] = []                 // trigger-spawned, live until next step
    /// The step's focus-emphasis lift: (container, original local position).
    private var liftedNode: (entity: Entity, original: SIMD3<Float>)?

    init(root: Entity,
         node: @escaping (String) -> Entity?,
         assets: [ModularContract.Asset],
         steps: [ModularContract.Step],
         policy: ModularContract.PlacementPolicy,
         experienceTitle: String,
         narrator: SpeechNarrator,
         onHUD: @escaping (StepHUD?) -> Void) {
        self.root = root
        self.node = node
        self.assets = assets
        self.steps = steps
        self.policy = policy
        self.experienceTitle = experienceTitle
        self.narrator = narrator
        self.onHUD = onHUD
    }

    var hasSteps: Bool { !steps.isEmpty }
    var currentStep: ModularContract.Step? {
        steps.indices.contains(stepIndex) ? steps[stepIndex] : nil
    }

    // MARK: lifecycle

    func start() {
        guard hasSteps else { onHUD(nil); return }
        apply(0)
    }

    func next() { apply(stepIndex + 1) }
    func previous() { apply(stepIndex - 1) }

    /// Screen tap that no trigger consumed. Advances when the step asks for tap.
    func advanceViaTap() {
        guard let step = currentStep else { return }
        if step.advance == "tap" || step.advance.isEmpty { next() }
    }

    /// Tear down every transient touch (materials, opacity, overlays, lift, UI)
    /// and go silent. Called from ExperienceRuntime.clear()/re-apply.
    func teardown() {
        narrator.stop()
        narrator.onFinished = nil
        resetTransient(restoreLift: true)
        onHUD(nil)
    }

    // MARK: sequence (the game manager plays steps that play clips)

    private func apply(_ i: Int) {
        guard !steps.isEmpty else { return }
        stepIndex = ((i % steps.count) + steps.count) % steps.count
        let step = steps[stepIndex]
        var moved = resetTransient(restoreLift: true)

        // focus: full presence for the focus asset, isolate (ghost) the others.
        let focusId = resolveFocusId(step)
        for asset in assets {
            guard let container = node(asset.id) else { continue }
            if asset.id == focusId {
                Self.setOpacity(container, 1.0)
            } else {
                Self.setOpacity(container, 0.35)
                dimmedContainers.append(container)
            }
        }

        // clips: stop everything, then start the step's NAMED clip on the focus.
        for asset in assets { node(asset.id).map(Self.stopClips) }
        if let container = node(focusId) {
            Self.playClip(named: step.clip, on: modelEntity(in: container),
                          loop: step.loop)
        }

        // focus emphasis lift (multi-asset scenes): the isolated asset rises
        // 2 cm off its solved slot for the duration of the step — a real
        // step-driven content move, so placement honesty must re-report it.
        if assets.count > 1, let container = node(focusId) {
            liftedNode = (container, container.position)
            container.position.y += 0.02
            moved = true
        }

        // spatial step UI: pulsing marker at the anchored point + the part effect.
        let anchor = buildStepUI(step, focusId: focusId)

        // narration + auto-advance
        narrator.stop()
        narrator.onFinished = nil
        autoAdvanceAt = nil
        if step.advance == "auto" {
            if step.narration.isEmpty {
                autoAdvanceAt = CACurrentMediaTime() + 6.0
            } else {
                narrator.onFinished = { [weak self] in
                    guard let self, self.currentStep?.advance == "auto" else { return }
                    self.autoAdvanceAt = CACurrentMediaTime() + 0.8
                }
            }
        }
        if !step.narration.isEmpty { narrator.speak(step.narration) }

        // HUD snapshot for the glass panel
        onHUD(StepHUD(
            index: stepIndex, count: steps.count,
            title: step.title, narration: step.narration,
            panels: step.panels.map {
                StepHUD.Panel(kind: $0.kind, title: $0.title, body: $0.body,
                              question: $0.question, options: $0.options,
                              answer: $0.answer)
            },
            advance: step.advance,
            anchorWorld: anchor,
            locus: policy.panelPosition,
            experienceTitle: experienceTitle))

        onStepEnter?(stepIndex, step)
        if moved { onContentMoved?("step \(stepIndex + 1) reposition") }
        print("SPATAIL step \(stepIndex + 1)/\(steps.count): \(step.title)")
    }

    /// step.focus is an asset id ("scene" or unknown → the hero/first asset).
    private func resolveFocusId(_ step: ModularContract.Step) -> String {
        if node(step.focus) != nil { return step.focus }
        return assets.first(where: {
            $0.role == "hero" || $0.role == "primary" || $0.role == "subject"
                || $0.role == "primary_object"
        })?.id ?? assets.first?.id ?? ""
    }

    /// Rebuild the current step's scene touches in place after a model swap —
    /// no narration restart, no step change, no HUD churn beyond the anchor.
    func modelDidSwap(assetId: String) {
        guard let step = currentStep else { return }
        let focusId = resolveFocusId(step)
        // Restore + rebuild only the transient visuals (keep lift + dimming as-is
        // for non-focus swaps; the swapped model inherits its container's state).
        restoreMaterialTouches()
        let anchor = buildStepUI(step, focusId: focusId)
        if assetId == focusId, let container = node(focusId) {
            Self.stopClips(container)
            Self.playClip(named: step.clip, on: modelEntity(in: container),
                          loop: step.loop)
        }
        onHUD(StepHUD(
            index: stepIndex, count: steps.count,
            title: step.title, narration: step.narration,
            panels: step.panels.map {
                StepHUD.Panel(kind: $0.kind, title: $0.title, body: $0.body,
                              question: $0.question, options: $0.options,
                              answer: $0.answer)
            },
            advance: step.advance,
            anchorWorld: anchor,
            locus: policy.panelPosition,
            experienceTitle: experienceTitle))
    }

    // MARK: per-frame (marker pulse + auto-advance — trivial math only)

    func tick(now: TimeInterval) {
        if !pulseMarkers.isEmpty {
            let s = 1 + 0.3 * Float(sin(now * 5))
            for m in pulseMarkers { m.scale = SIMD3(repeating: s) }
        }
        if let deadline = autoAdvanceAt, now >= deadline {
            autoAdvanceAt = nil
            next()
        }
    }

    // MARK: quiz (glass panel option tapped)

    /// Returns true when the answer was correct (the runtime then routes
    /// onQuizCorrect triggers / advances).
    func quizAnswered(correct: Bool) {
        playCue(correct ? "correct" : "tap")
    }

    // MARK: trigger actuators (the VM's hands — same transient discipline)

    func playClipForTrigger(target: String?, clip: String?) {
        let id = target.flatMap { node($0) != nil ? $0 : nil }
            ?? currentStep.map(resolveFocusId)
            ?? assets.first?.id ?? ""
        guard let container = node(id) else { return }
        Self.playClip(named: clip ?? "", on: modelEntity(in: container), loop: true)
    }

    func addTriggerLabel(target: String) {
        let host: Entity?
        if let container = node(target) {
            host = container
        } else {
            host = assets.compactMap { node($0.id) }.first {
                Self.findEntity(in: $0, fuzzy: target) != nil
            }
        }
        guard let host else { return }
        let text = assets.first(where: { $0.id == target })?.name
            ?? target.replacingOccurrences(of: "_", with: " ")
        addLabel(text, near: host)
    }

    func applyTriggerEffect(target: String, effect: String) {
        let part: Entity?
        if let container = node(target) {
            part = modelEntity(in: container)
        } else {
            part = assets.compactMap { node($0.id) }
                .compactMap { Self.findEntity(in: $0, fuzzy: target) }
                .first
        }
        guard let part else { return }
        applyEffect(effect, to: part)
    }

    func playCue(_ cue: String) { Self.cue(cue) }

    static func cue(_ cue: String) {
        AudioServicesPlaySystemSound(cue == "correct" ? 1054
                                     : (cue == "pickup" ? 1104 : 1103))
    }

    // MARK: transient reset (INVARIANT: restore materials BEFORE disabling overlays)

    /// Undo every touch of the previous step. Returns true when content MOVED
    /// (the lift restore) so the caller can fold it into the placement report.
    @discardableResult
    private func resetTransient(restoreLift: Bool) -> Bool {
        for l in labels { l.removeFromParent() }
        labels.removeAll()
        for e in stepUI { e.removeFromParent() }
        stepUI.removeAll()
        pulseMarkers.removeAll()
        restoreMaterialTouches()
        for e in dimmedContainers { Self.setOpacity(e, 1.0) }
        dimmedContainers.removeAll()
        var moved = false
        if restoreLift, let lifted = liftedNode {
            if simd_length(lifted.entity.position - lifted.original) > 0.005 { moved = true }
            lifted.entity.position = lifted.original
            liftedNode = nil
        }
        return moved
    }

    /// Materials/ghost/overlay restoration alone (model-swap refresh path).
    /// INVARIANT (from the legacy runtime): restore materials BEFORE disabling
    /// overlays — a summoned region overlay's ModelEntity is captured in
    /// tintedParts by applyEffect→mutateMaterials, so the material reset must run
    /// first; only then hide the overlay. Reordering strands the mutated material
    /// on a re-summoned overlay.
    private func restoreMaterialTouches() {
        for (m, mats) in tintedParts { m.model?.materials = mats }
        tintedParts.removeAll()
        for e in ghostedParts { Self.setOpacity(e, 1.0) }
        ghostedParts.removeAll()
        for e in enabledOverlays { e.isEnabled = false }
        enabledOverlays.removeAll()
    }

    // MARK: spatial step UI (marker + part effect; text lives on the glass panel)

    /// Returns the WORLD-space anchor point the step explains (for the panel pin).
    private func buildStepUI(_ step: ModularContract.Step, focusId: String) -> SIMD3<Float>? {
        guard let container = node(focusId) else { return nil }
        let body = modelEntity(in: container) ?? container
        let bounds = body.visualBounds(relativeTo: root)
        guard bounds.max.y.isFinite, bounds.max.y >= bounds.min.y else { return nil }
        let asset = assets.first(where: { $0.id == focusId })
        let (point, part) = resolveStepAnchor(step, node: body, bounds: bounds, asset: asset)
        addMarker(at: point)
        if let part {
            applyEffect(step.effect, to: part)
        } else if !step.effect.isEmpty, step.effect.lowercased() != "none" {
            // No addressable part — the named effect lands on the whole focus
            // model (parity with the engine's StepSequencerSystem).
            applyEffect(step.effect, to: body)
        }
        return root.convert(position: point, to: nil)
    }

    /// Where the explanation POINTS (§2 hierarchy, ported verbatim):
    /// STORY-baked region overlay → contract anchorOffset → target/keyword fuzzy
    /// name match → deterministic bbox points (so consecutive steps land on
    /// different regions). Points are in the ROOT frame.
    private func resolveStepAnchor(_ step: ModularContract.Step, node body: Entity,
                                   bounds: BoundingBox,
                                   asset: ModularContract.Asset?) -> (SIMD3<Float>, Entity?) {
        // §2.0 — a STORY-baked REGION wins: enable its overlay mesh and return it
        // as the effect target, so the glow lands on exactly that patch (the
        // lion's eye) instead of the whole head.
        if !step.region.isEmpty,
           let region = asset?.regions.first(where: { $0.id == step.region || $0.role == step.region }) {
            if let overlay = Self.findRegionOverlay(in: body, node: region.overlayNode,
                                                    id: region.id) {
                overlay.isEnabled = true
                enabledOverlays.append(overlay)
                let hb = overlay.visualBounds(relativeTo: root)
                if hb.max.y.isFinite, hb.max.y >= hb.min.y {
                    return (SIMD3(hb.center.x, hb.max.y, hb.center.z), overlay)
                }
            }
            if region.offset.count == 3 {       // overlay missing → still point at the part
                let o = SIMD3(Float(region.offset[0]), Float(region.offset[1]),
                              Float(region.offset[2]))
                return (bounds.min + o * bounds.extents, nil)
            }
        }
        if step.anchorOffset.count == 3 {
            let o = SIMD3(Float(step.anchorOffset[0]), Float(step.anchorOffset[1]),
                          Float(step.anchorOffset[2]))
            return (bounds.min + o * bounds.extents, nil)
        }
        var needles: [String] = step.target.isEmpty ? [] : [step.target]
        needles += Self.keywords(step.title)
        for n in needles {
            if let hit = Self.findEntity(in: body, fuzzy: n), hit !== body {
                // geometry-free nodes (rig locators, lights) have EMPTY bounds —
                // min=(inf), max=(-inf) — which would place the marker at y=-inf.
                let hb = hit.visualBounds(relativeTo: root)
                guard hb.max.y.isFinite, hb.max.y >= hb.min.y else { continue }
                return (SIMD3(hb.center.x, hb.max.y, hb.center.z), hit)
            }
        }
        let pts: [SIMD3<Float>] = [
            SIMD3(bounds.center.x, bounds.max.y, bounds.center.z),                              // top
            SIMD3(bounds.max.x, bounds.center.y + bounds.extents.y * 0.2, bounds.center.z),     // right
            SIMD3(bounds.center.x, bounds.center.y + bounds.extents.y * 0.3, bounds.max.z),     // front
            SIMD3(bounds.min.x, bounds.center.y + bounds.extents.y * 0.2, bounds.center.z),     // left
        ]
        return (pts[stepIndex % pts.count], nil)
    }

    /// Apply the step's named visual EFFECT to its target part (contract
    /// `step.effect`). NON-DESTRUCTIVE: originals stashed, restored next step.
    /// Empty/unknown → highlight. USDZ only carries baseColor/emissive/opacity,
    /// so every effect is expressible on-device.
    private func applyEffect(_ effect: String, to part: Entity) {
        let e = effect.isEmpty ? "highlight" : effect.lowercased()
        switch e {
        case "none":
            return
        case "ghost":
            Self.setOpacity(part, 0.3); ghostedParts.append(part)
        case "emissive":
            mutateMaterials(of: part) { SpatailMaterials.emissive($0) }
        case let t where t.hasPrefix("tint:"):
            if let col = SpatailMaterials.color(fromHex: String(t.dropFirst(5))) {
                mutateMaterials(of: part) { SpatailMaterials.tinted($0, color: col) }
            } else {
                mutateMaterials(of: part) { SpatailMaterials.highlighted($0) }
            }
        default:   // "highlight" + anything unrecognised
            mutateMaterials(of: part) { SpatailMaterials.highlighted($0) }
        }
    }

    /// Walk a part's subtree, remap every material slot, stash the originals so
    /// multi-material parts keep their regions and can be restored.
    private func mutateMaterials(of part: Entity,
                                 _ transform: (any RealityKit.Material) -> any RealityKit.Material) {
        var stack: [Entity] = [part]
        while let e = stack.popLast() {
            if let m = e as? ModelEntity, let model = m.model {
                tintedParts.append((m, model.materials))
                m.model?.materials = model.materials.map(transform)
            }
            stack.append(contentsOf: e.children)
        }
    }

    /// Pulsing dot at the anchored point — "look here" on the model itself.
    private func addMarker(at p: SIMD3<Float>) {
        let m = ModelEntity(mesh: .generateSphere(radius: 0.009),
                            materials: [UnlitMaterial(color: .systemTeal)])
        m.position = p
        root.addChild(m)
        stepUI.append(m); pulseMarkers.append(m)
    }

    /// Small floating 3-D label for trigger `label` actions (lives until the
    /// next step, exactly like the legacy runtime's trigger labels).
    private func addLabel(_ text: String, near host: Entity) {
        guard !text.isEmpty else { return }
        let mesh = MeshResource.generateText(
            text, extrusionDepth: 0.0008, font: .systemFont(ofSize: 0.024),
            containerFrame: CGRect(x: 0, y: 0, width: 0.28, height: 0.08),
            alignment: .center, lineBreakMode: .byWordWrapping)
        let e = ModelEntity(mesh: mesh,
                            materials: [UnlitMaterial(color: .white)])
        e.position = SIMD3(-0.12, 0.16, 0)
        host.addChild(e)
        labels.append(e)
    }

    // MARK: helpers

    /// The visible model inside a container: the swapped-in USDZ, else the
    /// placeholder primitive, else the container itself.
    private func modelEntity(in container: Entity) -> Entity? {
        container.findEntity(named: "model")
            ?? container.findEntity(named: "placeholder")
            ?? container
    }

    private static func keywords(_ s: String) -> [String] {
        let stop: Set<String> = ["the", "a", "an", "of", "and", "how", "what", "meet",
                                 "its", "this", "that", "with", "your", "world",
                                 "amazing", "inside"]
        var out: [String] = []
        for raw in s.lowercased().split(whereSeparator: { !$0.isLetter }) {
            let t = String(raw)
            guard t.count > 2, !stop.contains(t) else { continue }
            out.append(t)
            if t.hasSuffix("s") { out.append(String(t.dropLast())) }
        }
        return out
    }

    static func findEntity(in root: Entity, fuzzy needle: String) -> Entity? {
        guard needle.count > 2 else { return nil }
        let n = needle.lowercased()
        var stack: [Entity] = [root]
        while let e = stack.popLast() {
            if e.name.lowercased().contains(n) { return e }
            stack.append(contentsOf: e.children)
        }
        return nil
    }

    /// Find a baked region overlay by its exported node name (preferred), else
    /// any `spatail_region…__<id>` entity — robust to USD prim renaming.
    private static func findRegionOverlay(in root: Entity, node: String, id: String) -> Entity? {
        let want = node.lowercased(), suffix = "__" + id.lowercased()
        var stack: [Entity] = [root]
        var fallback: Entity? = nil
        while let e = stack.popLast() {
            let nm = e.name.lowercased()
            if !want.isEmpty, nm == want || nm.contains(want) { return e }
            if nm.contains("spatail_region"), nm.hasSuffix(suffix) { fallback = e }
            stack.append(contentsOf: e.children)
        }
        return fallback
    }

    /// Region overlays ship visible (so they survive GLB/USDZ export) but must
    /// stay OFF until a step summons them — hide every `spatail_region…` entity.
    @discardableResult
    static func disableRegionOverlays(in root: Entity) -> Int {
        var stack: [Entity] = [root], n = 0
        while let e = stack.popLast() {
            if e.name.lowercased().contains("spatail_region") { e.isEnabled = false; n += 1 }
            stack.append(contentsOf: e.children)
        }
        return n
    }

    /// Named-clip playback: exact name match first, then contains, then all
    /// clips (USDZ exporters frequently rename to "default").
    static func playClip(named name: String, on entity: Entity?, loop: Bool) {
        guard let entity else { return }
        var exact: [(Entity, AnimationResource)] = []
        var partial: [(Entity, AnimationResource)] = []
        var all: [(Entity, AnimationResource)] = []
        let wanted = name.lowercased()
        var stack: [Entity] = [entity]
        while let e = stack.popLast() {
            for anim in e.availableAnimations {
                all.append((e, anim))
                let n = (anim.name ?? "").lowercased()
                if !wanted.isEmpty, n == wanted { exact.append((e, anim)) }
                else if !wanted.isEmpty, !n.isEmpty,
                        n.contains(wanted) || wanted.contains(n) { partial.append((e, anim)) }
            }
            stack.append(contentsOf: e.children)
        }
        let chosen = !exact.isEmpty ? exact : (!partial.isEmpty ? partial : all)
        for (e, anim) in chosen {
            e.playAnimation(loop ? anim.repeat() : anim,
                            transitionDuration: 0.2, startsPaused: false)
        }
    }

    static func stopClips(_ entity: Entity) {
        var stack: [Entity] = [entity]
        while let e = stack.popLast() {
            e.stopAllAnimations()
            stack.append(contentsOf: e.children)
        }
    }

    static func setOpacity(_ e: Entity, _ o: Float) {
        if #available(iOS 18.0, *) {
            if o >= 0.999 { e.components.remove(OpacityComponent.self) }
            else { e.components.set(OpacityComponent(opacity: o)) }
        }
    }
}
