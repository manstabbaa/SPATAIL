import SwiftUI
#if os(iOS)
import RealityKit
import ARKit
import UIKit
import Combine
import simd
import AVFoundation
import AudioToolbox

// ModularRuntime — SPATAIL the GAME MANAGER (v0.6). Motion is BAKED into each asset
// as clips; this runtime PLAYS and SEQUENCES those clips and layers interaction on
// top. No procedural mechanics: it plays the asset's baked animation, walks the
// experience's `sequence` of steps (narration/panels), and runs `triggers`
// (tap / walk-up / gaze / tap-a-part). Placement is live + world-anchored.

@MainActor
final class ModularRuntime: NSObject {
    private weak var view: ARView?
    private var anchor: AnchorEntity?
    private var holders: [String: Entity] = [:]          // assetId -> holder
    private var nodes: [String: Entity] = [:]            // assetId -> visible model (box or usdz)
    private var exp: ModularExperience?
    private var stepIndex = 0
    private var triggers: [ModularExperience.Trigger] = []
    private var firedOnce: Set<String> = []
    private var gazeStart: [String: TimeInterval] = [:]
    private var placedTransform: simd_float4x4?
    private var tapRecognizer: UITapGestureRecognizer?
    private var updateSub: Cancellable?
    private var labels: [Entity] = []
    // spatial step UI (anchored callout + highlight marker + side panel)
    private var stepUI: [Entity] = []
    private var pulseMarkers: [Entity] = []
    private var tintedParts: [(ModelEntity, [any RealityKit.Material])] = []
    private var ghostedParts: [Entity] = []   // parts the step's `ghost` effect made translucent
    private let synth = AVSpeechSynthesizer()
    private let onStatus: (String) -> Void

    /// Live RoomModel pushed from ARKit (~1 Hz) by ModularEntryView's coordinator.
    /// Input for PlacementSolver; held here for placement once wired.
    var roomModel: RoomModel?

    /// TRACKED stream (anchoring.mode == "object"): iOS 27 object tracking session
    /// layer. The entry view's coordinator forwards ARObjectAnchor events into it.
    let objectAnchoring = ObjectAnchoringController()

    init(view: ARView, onStatus: @escaping (String) -> Void = { _ in }) {
        self.view = view; self.onStatus = onStatus; super.init()
        // .playback so narration survives the ring/silent switch (the default
        // .soloAmbient is muted by it, which reads as "narration is broken")
        try? AVAudioSession.sharedInstance().setCategory(.playback, options: [.mixWithOthers])
        try? AVAudioSession.sharedInstance().setActive(true)
    }

    // MARK: - present
    func present(_ e: ModularExperience) {
        clear()
        guard let view else { return }
        // The stream split (design system anchoring): object-TRACKED overlay when the
        // brain asked for it and the device can; otherwise the world-PLACED stream.
        if e.anchoring.mode == "object" {
            onStatus("tracked mode: looking for your object…")
            objectAnchoring.onStatus = { [weak self] s in self?.onStatus(s) }
            objectAnchoring.onMountReady = { [weak self] mount in
                guard let self else { return }
                self.present(e, on: mount)                  // experience root ON the object
            }
            objectAnchoring.onTrackingChanged = { [weak self] tracked in
                guard let self, let a = self.anchor else { return }
                // §3.4: pause when tracking is lost — ghost the content, keep state.
                self.setOpacity(a, tracked ? 1.0 : 0.15)
            }
            objectAnchoring.onFallback = { [weak self] reason in
                guard let self, let view = self.view else { return }
                self.onStatus(reason)
                self.presentWorldPlaced(e, in: view)        // anchoring.fallback == "world"
            }
            objectAnchoring.begin(anchoring: e.anchoring, view: view)
            return
        }
        presentWorldPlaced(e, in: view)
    }

    /// PLACED stream: anchor in the world per the Placement Design System policy.
    /// Preferred path = the PlacementSolver against the live RoomModel (the REAL
    /// table/floor: extent-aware packing, comfort distance, facing the user).
    /// Fallback = the original raycast placement when the room isn't known yet.
    private func presentWorldPlaced(_ e: ModularExperience, in view: ARView) {
        objectAnchoring.stop()
        if placedTransform == nil, let solved = solvedPlacement(e) {
            let a = AnchorEntity(world: solved.root)
            view.scene.addAnchor(a)
            anchor = a
            placedTransform = solved.root
            present(e, on: a, layout: solved.locals, layoutScale: solved.scale)
            onStatus("placed on the \(solved.anchor) (design system)")
            return
        }
        let t = placedTransform ?? Self.placementTransform(in: view,
                                                           anchorType: e.placement.anchorType)
        placedTransform = t
        let a = AnchorEntity(world: t)
        view.scene.addAnchor(a)
        present(e, on: a)
    }

    /// Resolve the design-system policy against the live RoomModel: pick the real
    /// surface, pack assets to its extent × maxSurfaceCoverage, keep the comfort
    /// distance, face the user. Returns nil whenever the room can't answer yet —
    /// the caller falls back to raycast placement (§10: always provide a fallback).
    private func solvedPlacement(_ e: ModularExperience)
        -> (root: simd_float4x4, locals: [String: SIMD3<Float>], scale: Float, anchor: String)? {
        guard let rm = roomModel, !rm.surfaces.isEmpty, !e.assets.isEmpty else { return nil }
        let pref = e.placement.anchorType
        guard pref == "table" || pref == "floor" || pref == "room" else { return nil } // wall → raycast path
        let reqs = e.assets.map { a -> PlacementSolver.AssetReq in
            let d = a.scaleMeters
            let fp = SIMD3<Float>(Float(d.count > 0 ? d[0] : 0.2),
                                  Float(d.count > 1 ? d[1] : 0.2),
                                  Float(d.count > 2 ? d[2] : 0.2))
            return PlacementSolver.AssetReq(id: a.id, footprint: fp, role: a.role)
        }
        let primary = e.assets.first(where: { $0.role == "primary_object" })?.id ?? e.assets[0].id
        let plan = PlacementSolver.solve(
            room: rm, assets: reqs,
            anchorPreference: pref == "room" ? "floor" : pref,
            scaleMode: e.placement.scaleMode == "true_scale" ? "real" : "dynamic",
            primary: primary,
            coverage: Float(e.placement.maxSurfaceCoverage))
        // Centre on the detected surface even when the fit is TIGHT — a tight/!fits
        // result (or a spurious obstacle flag) must not drop the demo to a raycast-at-aim
        // placement. The solver already scaled the set to the surface; centring on the
        // real table beats landing wherever the camera points.
        guard let hero = plan.placements.first else { return nil }

        // solver frame (user at origin, looking -Z) → world
        let f = rm.user.forward
        let theta = atan2(-f.x, -f.z)
        let mapRot = simd_quatf(angle: theta, axis: SIMD3(0, 1, 0))
        func toWorld(_ s: SIMD3<Float>) -> SIMD3<Float> {
            let r = mapRot.act(SIMD3(s.x, 0, s.z))
            return SIMD3(rm.user.position.x + r.x, s.y, rm.user.position.z + r.z)
        }
        // The solver assumes the spine frame contract (room_model.py): user at the
        // origin, surface dead ahead, floor at y 0. ARKit feeds world-frame surfaces
        // and a live pose instead, so pin the solved arc onto the REAL surface:
        // the table's detected centre (§6: centre on the detected table), or the
        // floor's detected world height (ARKit world y=0 is the session-start pose,
        // ~1.4 m above the real floor — never a place to stand content).
        let heroArc = toWorld(hero.position)
        let pin: SIMD3<Float>
        if plan.anchor == "table", let t = rm.bestTable() {
            pin = SIMD3(t.center.x - heroArc.x, t.height - heroArc.y, t.center.z - heroArc.z)
        } else if plan.anchor == "floor", let fl = rm.floor() {
            pin = SIMD3(0, fl.height - heroArc.y, 0)   // ahead of the user, on the real floor
        } else {
            return nil                                  // no real surface → raycast fallback
        }
        print("SPATAIL placement: anchor=\(plan.anchor) hero=\(heroArc + pin) "
              + "surfaces=\(rm.surfaces.map { "\($0.cls)@\(String(format: "%.2f", $0.height))" })")
        // root sits at the hero slot, rotated to face the user (matches the
        // raycast path's convention so captions/labels hang the same way)
        let heroW = heroArc + pin
        let toUser = rm.user.position - heroW
        let rootYaw = atan2(toUser.x, toUser.z)
        let rootRot = simd_quatf(angle: rootYaw, axis: SIMD3(0, 1, 0))
        let root = Transform(scale: .one, rotation: rootRot, translation: heroW).matrix
        let inv = rootRot.inverse
        var locals: [String: SIMD3<Float>] = [:]
        for p in plan.placements {
            locals[p.assetId] = inv.act(toWorld(p.position) + pin - heroW)
        }
        return (root, locals, hero.scale, plan.anchor)
    }

    /// Present onto a caller-owned anchor (Live Explainer overlay).
    func present(_ e: ModularExperience, into host: AnchorEntity) {
        clear(); guard view != nil else { return }; present(e, on: host)
    }

    /// §7 comfort: RE-RUN the SPATAIL spatial design for the current experience —
    /// re-centre on the table you're at (or the floor in front of you) and re-face the
    /// user, against the FRESHEST RoomModel. Keeps the built scene (loaded models, clips,
    /// step state); only the anchor + layout move. This is a "redo the intelligent
    /// placement" action, NOT a drag-to-where-you-aim: standing still re-solves to the
    /// SAME spot (no jitter); walking around re-centres/re-faces the content.
    func reposition() {
        guard let e = exp, let view, anchor != nil, e.anchoring.mode != "object" else { return }
        defer { refreshStepUI() }    // re-anchor the step's callout/panel to the moved scene
        if let solved = solvedPlacement(e) {
            let newPos = SIMD3(solved.root.columns.3.x, solved.root.columns.3.y, solved.root.columns.3.z)
            let newFwd = SIMD3(solved.root.columns.2.x, solved.root.columns.2.y, solved.root.columns.2.z)
            let oldPos = placedTransform.map { SIMD3($0.columns.3.x, $0.columns.3.y, $0.columns.3.z) }
            let oldFwd = placedTransform.map { SIMD3($0.columns.2.x, $0.columns.2.y, $0.columns.2.z) }
            placedTransform = solved.root
            relocateAnchor(to: solved.root)
            for (id, h) in holders {
                if let l = solved.locals[id] { h.position = l }
                h.scale = SIMD3(repeating: max(solved.scale, 0.05))
            }
            // unchanged = same spot AND same facing (walking around the table keeps
            // the table centre but re-faces the content — that IS a reposition)
            if let op = oldPos, simd_length(op - newPos) < 0.05,
               let of = oldFwd, simd_dot(simd_normalize(of), simd_normalize(newFwd)) > 0.985 {
                onStatus("placement unchanged — still the best \(solved.anchor) spot")
            } else {
                onStatus("repositioned onto the \(solved.anchor) (design system)")
            }
            return
        }
        let t = Self.placementTransform(in: view, anchorType: e.placement.anchorType)
        placedTransform = t
        relocateAnchor(to: t)
        let widths = e.assets.map { Float(max($0.scaleMeters.first ?? 0.2, 0.05)) }
        let poses = Self.ringPoses(count: e.assets.count, footprintWidths: widths)
        for (i, a) in e.assets.enumerated() {
            holders[a.id]?.position = poses[i]
            holders[a.id]?.scale = .one
        }
        onStatus("repositioned (raycast)")
    }

    /// Move the whole scene to a new world transform by re-parenting the holders
    /// onto a fresh world anchor (AnchorEntity(world:) transforms are immutable).
    private func relocateAnchor(to t: simd_float4x4) {
        guard let view else { return }
        let a = AnchorEntity(world: t)
        view.scene.addAnchor(a)
        for (_, h) in holders { a.addChild(h) }       // keeps local transforms
        if let old = anchor { view.scene.removeAnchor(old) }
        anchor = a
    }

    private func present(_ e: ModularExperience, on a: AnchorEntity,
                         layout: [String: SIMD3<Float>]? = nil, layoutScale: Float = 1.0) {
        guard let view else { return }
        exp = e; anchor = a
        triggers = e.triggers
        firedOnce.removeAll(); gazeStart.removeAll()

        let widths = e.assets.map { Float(max($0.scaleMeters.first ?? 0.2, 0.05)) }
        let poses = Self.ringPoses(count: e.assets.count, footprintWidths: widths)
        for (i, asset) in e.assets.enumerated() {
            let holder = Entity()
            // solver-resolved pose (the design system against the real room) when
            // available; the generic ring otherwise.
            holder.position = layout?[asset.id] ?? poses[i]
            if layoutScale != 1.0 {
                holder.scale = SIMD3(repeating: max(layoutScale, 0.05))
            }
            a.addChild(holder)
            holders[asset.id] = holder
            holder.addChild(makeContactShadow(radius: max(widths[i], 0.06) * 0.75))
            let box = makeBox(asset)
            holder.addChild(box)
            nodes[asset.id] = box
            if !asset.usdzUrl.isEmpty {
                let id = asset.id, url = asset.usdzUrl, fw = widths[i]
                Task { await self.loadModel(id, url, footprintW: fw) }
            }
        }
        updateSub = view.scene.subscribe(to: SceneEvents.Update.self) { [weak self] _ in self?.tick() }
        installTap()
        stepIndex = 0
        applyStep(0)
        // §5 scale rule: ALWAYS communicate a changed scale ("Scaled to about 1:8 …",
        // "Enlarged for inspection …"). The note rides under the primary asset.
        if !e.placement.scaleNote.isEmpty,
           let primary = holders[e.assets.first(where: { $0.role == "primary_object" })?.id
                                 ?? e.assets.first?.id ?? ""] {
            addLabel(e.placement.scaleNote, near: primary)
        }
        print("SPATAIL v0.7: '\(e.title)' — \(e.assets.count) assets, \(e.sequence.count) steps, "
              + "\(e.clips.count) clips, composer=\(e.composer), "
              + "preset=\(e.placement.preset.isEmpty ? "n/a" : e.placement.preset), "
              + "anchoring=\(e.anchoring.mode)")
    }

    // MARK: - sequence (the game manager plays steps that play clips)
    func next() { applyStep(stepIndex + 1) }

    private func applyStep(_ i: Int) {
        guard let e = exp, !e.sequence.isEmpty else { return }
        stepIndex = ((i % e.sequence.count) + e.sequence.count) % e.sequence.count
        let step = e.sequence[stepIndex]
        resetTransient()
        // focus: play the focus asset's clip; gently ghost the others
        for (id, h) in holders { setOpacity(h, id == step.focus ? 1.0 : 0.35) }
        playClipOn(step.focus)
        buildStepUI(step)        // anchored callout + part highlight + side panel
        synth.stopSpeaking(at: .immediate)   // narration follows the visible step, not a queue
        if !step.narration.isEmpty { speak(step.narration) }
        onStatus("step \(stepIndex + 1)/\(e.sequence.count): \(step.title)")
    }

    /// The step's reading content (everything except the title, which lives in the
    /// anchored callout): narration + fact panels + quiz.
    private func sidePanelText(_ step: ModularExperience.Step) -> String {
        var lines = [step.narration].filter { !$0.isEmpty }
        for p in step.panels {
            if p.kind == "quiz", !p.question.isEmpty {
                lines.append("? " + p.question)
                lines.append(p.options.enumerated().map { "\($0.0 + 1). \($0.1)" }.joined(separator: "   "))
            } else if !p.body.isEmpty {
                lines.append((p.title.isEmpty ? "" : p.title + " — ") + p.body)
            }
        }
        return lines.joined(separator: "\n\n")
    }

    /// Step advance: labels AND step UI go.
    private func resetTransient() {
        for l in labels { l.removeFromParent() }
        labels.removeAll()
        clearStepUI()
    }

    /// In-place UI refresh (reposition / model swap): step UI only — the §5 scale
    /// note and trigger-spawned labels must survive until the next step.
    private func clearStepUI() {
        for e in stepUI { e.removeFromParent() }
        stepUI.removeAll()
        pulseMarkers.removeAll()
        for (m, mats) in tintedParts { m.model?.materials = mats }   // un-highlight
        tintedParts.removeAll()
        for e in ghostedParts { setOpacity(e, 1.0) }                 // un-ghost
        ghostedParts.removeAll()
    }

    // MARK: - spatial step UI (the immersive layer: the explanation happens ON the model)
    /// Design system §9: title rides in a callout anchored to the part being explained
    /// (leader line + pulsing marker + material highlight when the part is found);
    /// the reading content sits on a flat panel BESIDE the object, facing the user.
    private func buildStepUI(_ step: ModularExperience.Step) {
        guard let a = anchor, let e = exp else { return }
        let focusId = holders[step.focus] != nil
            ? step.focus
            : (e.assets.first(where: { $0.role == "primary_object" })?.id ?? e.assets.first?.id ?? "")
        guard let holder = holders[focusId] else { return }
        let node = nodes[focusId] ?? holder
        let b = node.visualBounds(relativeTo: a)
        let (point, part) = resolveStepAnchor(step, node: node, bounds: b, in: a)
        if !step.title.isEmpty { addCallout(step.title, at: point, in: a) }
        addMarker(at: point, in: a)
        if let part { applyEffect(step.effect, to: part) }
        let body = sidePanelText(step)
        if !body.isEmpty { addSidePanel(body, bounds: b, in: a) }
    }

    /// Rebuild the current step's UI in place (after reposition or a model swap) —
    /// no narration restart, no step change, labels untouched.
    private func refreshStepUI() {
        guard let e = exp, !e.sequence.isEmpty else { return }
        clearStepUI()
        buildStepUI(e.sequence[stepIndex])
    }

    /// Where the explanation POINTS. Priority is the §2 hierarchy: the contract's
    /// explicit anchor (normalized bbox offset from the director, or a named part),
    /// then a fuzzy name match from the step title, then deterministic bbox points
    /// so consecutive steps land on different regions of the model.
    private func resolveStepAnchor(_ step: ModularExperience.Step, node: Entity,
                                   bounds: BoundingBox, in root: Entity) -> (SIMD3<Float>, Entity?) {
        if step.anchorOffset.count == 3 {
            let o = SIMD3(Float(step.anchorOffset[0]), Float(step.anchorOffset[1]), Float(step.anchorOffset[2]))
            return (bounds.min + o * bounds.extents, nil)
        }
        var needles: [String] = step.target.isEmpty ? [] : [step.target]
        needles += Self.keywords(step.title)
        for n in needles {
            if let hit = Self.findEntity(in: node, fuzzy: n), hit !== node {
                // geometry-free nodes (rig locators, lights) have EMPTY bounds —
                // min=(inf), max=(-inf) — which would place the callout at y=-inf
                let hb = hit.visualBounds(relativeTo: root)
                guard hb.max.y.isFinite, hb.max.y >= hb.min.y else { continue }
                return (SIMD3(hb.center.x, hb.max.y, hb.center.z), hit)
            }
        }
        let pts: [SIMD3<Float>] = [
            SIMD3(bounds.center.x, bounds.max.y, bounds.center.z),                                  // top
            SIMD3(bounds.max.x, bounds.center.y + bounds.extents.y * 0.2, bounds.center.z),         // right
            SIMD3(bounds.center.x, bounds.center.y + bounds.extents.y * 0.3, bounds.max.z),         // front
            SIMD3(bounds.min.x, bounds.center.y + bounds.extents.y * 0.2, bounds.center.z),         // left
        ]
        return (pts[stepIndex % pts.count], nil)
    }

    private static func keywords(_ s: String) -> [String] {
        let stop: Set<String> = ["the", "a", "an", "of", "and", "how", "what", "meet", "its",
                                 "this", "that", "with", "your", "world", "amazing", "inside"]
        var out: [String] = []
        for raw in s.lowercased().split(whereSeparator: { !$0.isLetter }) {
            let t = String(raw)
            guard t.count > 2, !stop.contains(t) else { continue }
            out.append(t)
            if t.hasSuffix("s") { out.append(String(t.dropLast())) }
        }
        return out
    }

    private static func findEntity(in root: Entity, fuzzy needle: String) -> Entity? {
        guard needle.count > 2 else { return nil }
        let n = needle.lowercased()
        var stack: [Entity] = [root]
        while let e = stack.popLast() {
            if e.name.lowercased().contains(n) { return e }
            stack.append(contentsOf: e.children)
        }
        return nil
    }

    /// Apply the step's named visual EFFECT to its target part (the brain's per-beat
    /// emphasis choice, contract `step.effect`). All effects are NON-DESTRUCTIVE: the
    /// part's authored look (clay finish) is kept and restored on the next step
    /// (clearStepUI). Empty/unknown → highlight (the default). USDZ only carries
    /// baseColor/emissive/opacity, so every effect is expressible on-device.
    private func applyEffect(_ effect: String, to part: Entity) {
        let e = effect.isEmpty ? "highlight" : effect.lowercased()
        switch e {
        case "none":
            return
        case "ghost":
            setOpacity(part, 0.3); ghostedParts.append(part)
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

    /// Walk a part's subtree, remap every material slot through `transform`, and stash
    /// the originals so multi-material parts keep their regions and can be restored.
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

    /// Pulsing dot at the anchored point — "something has to happen" on the model.
    private func addMarker(at p: SIMD3<Float>, in root: Entity) {
        let m = ModelEntity(mesh: .generateSphere(radius: 0.009),
                            materials: [UnlitMaterial(color: .systemTeal)])
        m.position = p
        root.addChild(m)
        stepUI.append(m); pulseMarkers.append(m)
    }

    /// Title bubble above the anchor point, tied down with a leader line.
    private func addCallout(_ text: String, at p: SIMD3<Float>, in root: Entity) {
        let lift: Float = 0.10
        let bubble = makeTextBubble(text, width: 0.22, fontSize: 0.018)
        bubble.position = SIMD3(p.x, p.y + lift, p.z)
        if #available(iOS 18.0, *) { bubble.components.set(BillboardComponent()) }
        root.addChild(bubble)
        let line = ModelEntity(mesh: .generateBox(size: SIMD3(0.0015, lift - 0.02, 0.0015)),
                               materials: [UnlitMaterial(color: .white)])
        line.position = SIMD3(p.x, p.y + (lift - 0.02) / 2, p.z)
        root.addChild(line)
        stepUI.append(bubble); stepUI.append(line)
    }

    /// §9: the reading content goes BESIDE the object, flat and user-facing —
    /// never stacked on top of it.
    private func addSidePanel(_ text: String, bounds: BoundingBox, in root: Entity) {
        let panel = makeTextBubble(text, width: 0.26, fontSize: 0.013)
        panel.position = SIMD3(bounds.max.x + 0.18,
                               max(bounds.center.y + 0.06, 0.10),
                               bounds.center.z)
        if #available(iOS 18.0, *) { panel.components.set(BillboardComponent()) }
        root.addChild(panel)
        stepUI.append(panel)
    }

    /// Plain text on a dark backing — graphics pass comes later, structure first.
    private func makeTextBubble(_ text: String, width: CGFloat, fontSize: CGFloat) -> Entity {
        let mesh = MeshResource.generateText(text, extrusionDepth: 0.0008,
            font: .systemFont(ofSize: fontSize, weight: .medium),
            containerFrame: CGRect(x: 0, y: 0, width: width, height: width),
            alignment: .left, lineBreakMode: .byWordWrapping)
        let label = ModelEntity(mesh: mesh, materials: [UnlitMaterial(color: .white)])
        let tb = label.visualBounds(relativeTo: nil)
        let pad: Float = 0.012
        let backing = ModelEntity(
            mesh: .generatePlane(width: tb.extents.x + pad * 2, depth: tb.extents.y + pad * 2, cornerRadius: 0.012),
            materials: [UnlitMaterial(color: .black)])
        backing.orientation = simd_quatf(angle: .pi / 2, axis: SIMD3(1, 0, 0))   // face +z
        if #available(iOS 18.0, *) { backing.components.set(OpacityComponent(opacity: 0.6)) }
        let group = Entity()
        // centre the text block on the group origin; backing just behind it
        label.position = SIMD3(-tb.center.x, -tb.center.y, 0.001)
        backing.position = SIMD3(0, 0, 0)
        group.addChild(backing)
        group.addChild(label)
        return group
    }

    // MARK: - clip playback (play the asset's baked animation)
    private func playClipOn(_ id: String) {
        guard let model = nodes[id] else { return }
        for anim in model.availableAnimations {
            model.playAnimation(anim.repeat(), transitionDuration: 0.2, startsPaused: false)
        }
    }

    // MARK: - per-frame tick (spatial triggers + marker pulse — no procedural motion)
    private func tick() {
        let now = CACurrentMediaTime()
        if !pulseMarkers.isEmpty {
            let s = 1 + 0.3 * Float(sin(now * 5))
            for m in pulseMarkers { m.scale = SIMD3(repeating: s) }
        }
        checkSpatialTriggers(now)
    }

    // MARK: - interaction (triggers)
    private func installTap() {
        // once per view: present() runs per experience, and stacked identical
        // recognizers all fire — every tap would advance one extra step per re-present
        guard let view, tapRecognizer == nil else { return }
        let tap = UITapGestureRecognizer(target: self, action: #selector(onTap(_:)))
        view.addGestureRecognizer(tap)
        tapRecognizer = tap
    }
    @objc private func onTap(_ g: UITapGestureRecognizer) {
        guard let view else { return }
        let p = g.location(in: view)
        let hit = view.entity(at: p).flatMap { holderId(for: $0) ?? partName(for: $0) }
        if fireTriggers(event: "onTap", target: hit ?? "scene") { return }
        next()
    }

    @discardableResult
    private func fireTriggers(event: String, target: String) -> Bool {
        let m = triggers.filter { $0.when.event == event && $0.when.target == target }
        guard !m.isEmpty else { return false }
        for t in m { executeActions(t.doActions, defaultTarget: target) }
        return true
    }

    private func executeActions(_ actions: [ModularExperience.Action], defaultTarget: String) {
        for a in actions {
            let tgt = a.target ?? defaultTarget
            switch a.action {
            case "advance":   next()
            case "advanceTrack": next()
            case "playClip":  playClipOn(holders[tgt] != nil ? tgt : (exp?.sequence[safe: stepIndex]?.focus ?? tgt))
            case "label":     if let e = resolveTarget(tgt) { addLabel(labelText(tgt), near: e) }
            case "playSound": playCue(a.params.s("cue", "tap"))
            default: break
            }
        }
    }

    private func checkSpatialTriggers(_ now: TimeInterval) {
        guard let view, !triggers.isEmpty else { return }
        let cam = view.cameraTransform.translation
        let f = -view.cameraTransform.matrix.columns.2
        let camFwd = simd_normalize(SIMD3(f.x, 0, f.z))
        for (id, h) in holders {
            let to = h.position(relativeTo: nil) - cam
            let dist = simd_length(to)
            for t in triggers where t.when.event == "onApproach" && t.when.target == id {
                let r = t.when.params.f("radius_m", 0.7); let key = "near:" + id
                if dist <= r && !firedOnce.contains(key) { firedOnce.insert(key); executeActions(t.doActions, defaultTarget: id) }
                else if dist > r * 1.3 { firedOnce.remove(key) }
            }
            for t in triggers where t.when.event == "onGaze" && t.when.target == id {
                let flat = simd_length(SIMD3(to.x, 0, to.z)) > 1e-4 ? simd_normalize(SIMD3(to.x, 0, to.z)) : camFwd
                let dwell = t.when.params.d("dwell_s", 1.0); let key = "gaze:" + id
                if simd_dot(flat, camFwd) > 0.96 {
                    let start = gazeStart[id] ?? now; gazeStart[id] = start
                    if now - start >= dwell && !firedOnce.contains(key) { firedOnce.insert(key); executeActions(t.doActions, defaultTarget: id) }
                } else { gazeStart[id] = nil; firedOnce.remove(key) }
            }
        }
    }

    // MARK: - target resolution
    private func holderId(for e: Entity) -> String? {
        var cur: Entity? = e
        while let c = cur { if let k = holders.first(where: { $0.value === c })?.key { return k }; cur = c.parent }
        return nil
    }
    private func partName(for e: Entity) -> String? {
        let n = e.name
        return n.isEmpty ? nil : n
    }
    private func resolveTarget(_ id: String) -> Entity? {
        if let h = holders[id] { return h }
        for (_, n) in nodes { if let e = n.findEntity(named: id) { return e } }
        return nil
    }
    private func labelText(_ id: String) -> String {
        exp?.assets.first(where: { $0.id == id })?.name ?? id.replacingOccurrences(of: "_", with: " ")
    }

    // MARK: - placement (live + world-anchored; honours the design-system anchor type)
    private static func placementTransform(in view: ARView,
                                           anchorType: String = "table") -> simd_float4x4 {
        let cam = view.cameraTransform
        var origin: SIMD3<Float>
        let pt = CGPoint(x: view.bounds.midX, y: view.bounds.midY)
        // Wall-board experiences raycast vertical surfaces; everything else horizontal.
        // (Table-vs-floor refinement belongs to the PlacementSolver/RoomModel wiring.)
        let alignment: ARRaycastQuery.TargetAlignment = (anchorType == "wall") ? .vertical : .horizontal
        if let hit = view.raycast(from: pt, allowing: .estimatedPlane, alignment: alignment).first {
            let c = hit.worldTransform.columns.3; origin = SIMD3(c.x, c.y, c.z)
        } else {
            let f = -SIMD3(cam.matrix.columns.2.x, 0, cam.matrix.columns.2.z)
            let fwd = simd_length(f) > 1e-4 ? simd_normalize(f) : SIMD3(0, 0, -1)
            origin = cam.translation + fwd * 0.8
            // §7 comfort: walls hang at eye level; surface content drops to ~table height.
            origin.y = cam.translation.y - (anchorType == "wall" ? 0.0 : 0.6)
        }
        let toCam = cam.translation - origin
        return Transform(scale: .one, rotation: simd_quatf(angle: atan2(toCam.x, toCam.z), axis: SIMD3(0, 1, 0)),
                         translation: origin).matrix
    }

    private static func ringPoses(count: Int, footprintWidths: [Float]) -> [SIMD3<Float>] {
        guard count > 0 else { return [] }
        var out: [SIMD3<Float>] = [SIMD3(0, 0, 0)]                     // primary at the anchor
        let ring = max(footprintWidths.max() ?? 0.15, 0.1) * 1.4
        for i in 1..<max(count, 1) {
            let ang = 2 * Float.pi * Float(i - 1) / Float(max(count - 1, 1))
            out.append(SIMD3(ring * cos(ang), 0, ring * sin(ang)))
        }
        return out
    }

    // MARK: - assets
    private func makeBox(_ asset: ModularExperience.Asset) -> ModelEntity {
        let dims = asset.scaleMeters.count == 3 ? asset.scaleMeters : [0.18, 0.18, 0.18]
        let longest = max(dims.max() ?? 0.18, 0.02)
        let k = Float(min(0.3, longest) / longest)
        let size = SIMD3(Float(dims[0]) * k, Float(dims[1]) * k, Float(dims[2]) * k)
        let m = ModelEntity(mesh: .generateBox(size: size, cornerRadius: 0.006),
                            materials: [SimpleMaterial(color: asset.role == "primary_object" ? .systemTeal : .systemGray,
                                                       roughness: 0.6, isMetallic: false)])
        m.generateCollisionShapes(recursive: false)
        m.position.y = size.y / 2
        return m
    }

    private func loadModel(_ assetId: String, _ urlString: String, footprintW: Float) async {
        let baseStr = GenerativeClient.baseURL.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !baseStr.isEmpty, let base = URL(string: baseStr),
              let url = URL(string: urlString, relativeTo: base) else { return }
        do {
            print("SPATAIL: loadModel \(assetId) ← \(url.absoluteString)")
            let (tmp, resp) = try await URLSession.shared.download(from: url)
            if let http = resp as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
                print("SPATAIL: loadModel \(assetId) HTTP \(http.statusCode) — keeping box"); return
            }
            let dst = FileManager.default.temporaryDirectory.appendingPathComponent("mod_\(assetId).usdz")
            try? FileManager.default.removeItem(at: dst); try FileManager.default.moveItem(at: tmp, to: dst)
            let entity: Entity
            if #available(iOS 18.0, *) { entity = try await Entity(contentsOf: dst) } else { entity = try Entity.load(contentsOf: dst) }
            guard let holder = holders[assetId] else { return }
            // §5: true-scale experiences keep real-world dimensions (capped for safety);
            // fit-to-surface/miniature use the tabletop budget.
            let trueScale = (exp?.placement.scaleMode == "true_scale")
            fit(entity, to: trueScale ? max(min(footprintW, 1.2), 0.05)
                                      : max(min(footprintW, 0.3), 0.05))
            nodes[assetId]?.isEnabled = false
            holder.addChild(entity); nodes[assetId] = entity
            let clips = entity.availableAnimations
            print("SPATAIL: loadModel \(assetId) — \(clips.count) animation clip(s)")
            if clips.isEmpty { print("SPATAIL: WARNING no animations on \(assetId); model is static.") }
            for anim in clips { entity.playAnimation(anim.repeat(), transitionDuration: 0.2) }
            refreshStepUI()       // re-anchor callout/panel to the real geometry
        } catch { print("SPATAIL: loadModel \(assetId) FAILED: \(error) — keeping box") }
    }

    /// Stream a Blender-generated USDZ into an asset's holder and play its baked clips.
    func streamLocalModel(_ fileURL: URL, into assetId: String) {
        Task { @MainActor in
            guard let holder = self.holders[assetId] ?? self.holders.values.first else { return }
            do {
                let entity: Entity
                if #available(iOS 18.0, *) { entity = try await Entity(contentsOf: fileURL) } else { entity = try Entity.load(contentsOf: fileURL) }
                self.fit(entity, to: 0.3)
                self.nodes[assetId]?.removeFromParent()
                holder.addChild(entity); self.nodes[assetId] = entity
                for (id, h) in self.holders where id != assetId {
                    let placeholder = (self.exp?.assets.first(where: { $0.id == id })?.usdzUrl.isEmpty) ?? true
                    if placeholder { h.isEnabled = false }
                }
                let clips = entity.availableAnimations
                print("SPATAIL: streamLocalModel \(assetId) ← \(fileURL.lastPathComponent) — \(clips.count) animation clip(s)")
                if clips.isEmpty { print("SPATAIL: WARNING no animations on \(assetId); model is static.") }
                for anim in clips { entity.playAnimation(anim.repeat(), transitionDuration: 0.3) }
                self.refreshStepUI()   // re-anchor callout/panel to the real geometry
                self.onStatus("real model ready")
            } catch {
                print("SPATAIL: streamLocalModel \(assetId) FAILED: \(error)")
                self.onStatus("model load failed")
            }
        }
    }

    private func fit(_ entity: Entity, to target: Float) {
        let b = entity.visualBounds(relativeTo: nil)
        let longest = max(b.extents.x, max(b.extents.y, b.extents.z), 0.001)
        entity.scale = SIMD3(repeating: target / longest)
        let b2 = entity.visualBounds(relativeTo: nil)
        // Rest the model's ACTUAL bottom on the holder origin (the surface). Using
        // extents.y/2 alone only works when the mesh origin is at its vertical centre;
        // Meshy/animated USDZs carry an offset root, so subtract the bounds centre too —
        // otherwise the asset floats above (or sinks into) the table.
        entity.position.y = b2.extents.y / 2 - b2.center.y
        entity.position.x -= b2.center.x                  // centre the footprint on the holder
        entity.position.z -= b2.center.z
    }

    private func makeContactShadow(radius: Float) -> ModelEntity {
        let r = max(radius, 0.04)
        let e = ModelEntity(mesh: .generatePlane(width: r * 2, depth: r * 2, cornerRadius: r),
                            materials: [UnlitMaterial(color: .black)])
        e.position.y = 0.002
        if #available(iOS 18.0, *) { e.components.set(OpacityComponent(opacity: 0.22)) }
        return e
    }

    private func setOpacity(_ e: Entity, _ o: Float) {
        if #available(iOS 18.0, *) {
            if o >= 0.999 { e.components.remove(OpacityComponent.self) } else { e.components.set(OpacityComponent(opacity: o)) }
        }
    }

    private func addLabel(_ text: String, near h: Entity) {
        guard !text.isEmpty else { return }
        let mesh = MeshResource.generateText(text, extrusionDepth: 0.0008, font: .systemFont(ofSize: 0.024),
            containerFrame: CGRect(x: 0, y: 0, width: 0.28, height: 0.08), alignment: .center, lineBreakMode: .byWordWrapping)
        let e = ModelEntity(mesh: mesh, materials: [UnlitMaterial(color: .white)])
        e.position = SIMD3(-0.12, 0.16, 0); h.addChild(e); labels.append(e)
    }

    private func speak(_ text: String) { let u = AVSpeechUtterance(string: text); u.rate = 0.5; synth.speak(u) }
    private func playCue(_ cue: String) { AudioServicesPlaySystemSound(cue == "correct" ? 1054 : (cue == "pickup" ? 1104 : 1103)) }

    func clear() {
        objectAnchoring.stop()
        synth.stopSpeaking(at: .immediate)
        updateSub = nil; labels.removeAll()
        stepUI.removeAll(); pulseMarkers.removeAll(); tintedParts.removeAll(); ghostedParts.removeAll()
        if let anchor, let view { view.scene.removeAnchor(anchor) }
        anchor = nil; holders.removeAll(); nodes.removeAll(); exp = nil; stepIndex = 0
        // re-solve placement per experience: a raycast placement taken while the
        // room was still unknown must not lock later prompts out of the solver path
        placedTransform = nil
    }
}

private extension Array {
    subscript(safe i: Int) -> Element? { indices.contains(i) ? self[i] : nil }
}
#endif
