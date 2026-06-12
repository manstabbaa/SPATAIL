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
    private var caption: ModelEntity?
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
        guard let hero = plan.placements.first, hero.fits else { return nil }

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
        setCaption(stepCaption(step))
        synth.stopSpeaking(at: .immediate)   // narration follows the visible step, not a queue
        if !step.narration.isEmpty { speak(step.narration) }
        onStatus("step \(stepIndex + 1)/\(e.sequence.count): \(step.title)")
    }

    private func stepCaption(_ step: ModularExperience.Step) -> String {
        var lines = [step.title, step.narration].filter { !$0.isEmpty }
        for p in step.panels {
            if p.kind == "quiz", !p.question.isEmpty {
                lines.append("❓ " + p.question)
                lines.append(p.options.enumerated().map { "\($0.0 + 1). \($0.1)" }.joined(separator: "   "))
            } else if !p.body.isEmpty {
                lines.append(p.body)
            }
        }
        return lines.joined(separator: "\n")
    }

    private func resetTransient() {
        for l in labels { l.removeFromParent() }
        labels.removeAll()
    }

    // MARK: - clip playback (play the asset's baked animation)
    private func playClipOn(_ id: String) {
        guard let model = nodes[id] else { return }
        for anim in model.availableAnimations {
            model.playAnimation(anim.repeat(), transitionDuration: 0.2, startsPaused: false)
        }
    }

    // MARK: - per-frame tick (spatial triggers only — no procedural motion)
    private func tick() { checkSpatialTriggers(CACurrentMediaTime()) }

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
        entity.position.y = b2.extents.y / 2
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

    private func setCaption(_ text: String) {
        caption?.removeFromParent()
        guard let anchor, !text.isEmpty else { return }
        let mesh = MeshResource.generateText(text, extrusionDepth: 0.001, font: .systemFont(ofSize: 0.03, weight: .medium),
            containerFrame: CGRect(x: 0, y: 0, width: 0.6, height: 0.24), alignment: .center, lineBreakMode: .byWordWrapping)
        let m = ModelEntity(mesh: mesh, materials: [UnlitMaterial(color: .white)])
        m.position = SIMD3(-0.3, 0.42, 0.05); anchor.addChild(m); caption = m
    }

    private func speak(_ text: String) { let u = AVSpeechUtterance(string: text); u.rate = 0.5; synth.speak(u) }
    private func playCue(_ cue: String) { AudioServicesPlaySystemSound(cue == "correct" ? 1054 : (cue == "pickup" ? 1104 : 1103)) }

    func clear() {
        objectAnchoring.stop()
        synth.stopSpeaking(at: .immediate)
        updateSub = nil; labels.removeAll(); caption = nil
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
