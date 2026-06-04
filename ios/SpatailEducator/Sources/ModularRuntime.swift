import SwiftUI
#if os(iOS)
import RealityKit
import ARKit
import UIKit
import Combine
import simd
import AVFoundation

// ModularRuntime — the GENERIC interpreter for the SPATAIL v0.5 modular contract.
// The agent composed `beats`, each applying `mechanics` to assets; this runtime
// executes ANY mechanic by switching on its id, so adding a mechanic to the catalog
// only needs an implementation here once. Unknown mechanics degrade to a no-op.
//
// Placement uses StationLayout (plane-relative comfort arc). Each asset is a holder
// (placed on the surface) containing either a primitive box or the streamed library
// USDZ. Continuous mechanics (spin/oscillate/orbit/…) run off a per-frame tick;
// one-shot mechanics (explode/isolate/zoom/label/…) apply when the beat shows.
// Tap advances beats. Designed to be platform-agnostic in spirit (web mirrors it).

@MainActor
final class ModularRuntime: NSObject {
    private weak var view: ARView?
    private var anchor: AnchorEntity?
    private var holders: [String: Entity] = [:]            // assetId -> holder on the arc
    private var nodes: [String: Entity] = [:]              // assetId -> visible entity (box or model)
    private var baseXform: [ObjectIdentifier: Transform] = [:]
    private var exp: ModularExperience?
    private var beatIndex = 0
    private var animators: [Animator] = []
    private var labels: [Entity] = []
    private var caption: ModelEntity?
    private var updateSub: Cancellable?
    private var startTime: TimeInterval = 0
    private let synth = AVSpeechSynthesizer()
    private let onStatus: (String) -> Void

    struct Animator {
        weak var target: Entity?
        let kind: String
        let p: [String: AnyParam]
        let base: Transform
        let t0: TimeInterval
        var center: SIMD3<Float>? = nil
    }

    init(view: ARView, onStatus: @escaping (String) -> Void = { _ in }) {
        self.view = view; self.onStatus = onStatus; super.init()
    }

    // MARK: - present
    func present(_ e: ModularExperience) {
        clear()
        guard let view else { return }
        exp = e
        let a = AnchorEntity(plane: .horizontal, minimumBounds: [0.15, 0.15])
        anchor = a; view.scene.addAnchor(a)

        let widths = e.assets.map { Float(max($0.scaleMeters.first ?? 0.2, 0.05)) }
        let dist: Float = (e.stage.anchor == "floor") ? 1.2 : 0.74
        let poses = StationLayout.poses(count: e.assets.count, footprintWidths: widths,
                                        comfortRadiusM: dist, spacingMinM: 0.12,
                                        layout: e.stage.layout)
        for (i, asset) in e.assets.enumerated() {
            let holder = Entity()
            holder.position = poses[i].position
            holder.orientation = simd_quatf(angle: poses[i].yawRadians, axis: SIMD3(0, 1, 0))
            a.addChild(holder)
            holders[asset.id] = holder
            baseXform[ObjectIdentifier(holder)] = holder.transform
            let box = makeBox(asset)
            holder.addChild(box)
            nodes[asset.id] = box
            if !asset.usdzUrl.isEmpty {
                let id = asset.id, url = asset.usdzUrl
                let fw = Float(max(asset.scaleMeters.first ?? 0.2, 0.05))
                Task { await self.loadModel(id, url, footprintW: fw) }
            }
        }
        startTime = CACurrentMediaTime()
        updateSub = view.scene.subscribe(to: SceneEvents.Update.self) { [weak self] _ in self?.tick() }
        installTap()
        beatIndex = 0
        applyBeat(0)
        print("SPATAIL modular: '\(e.title)' — \(e.assets.count) assets, \(e.beats.count) beats, "
              + "composer=\(e.composer), mechanics=\(e.mechanicsUsed.count)")
    }

    // MARK: - beats
    func next() { applyBeat(beatIndex + 1) }

    private func applyBeat(_ i: Int) {
        guard let e = exp, !e.beats.isEmpty else { return }
        beatIndex = ((i % e.beats.count) + e.beats.count) % e.beats.count
        let beat = e.beats[beatIndex]
        resetTransient()
        setCaption(beat.title.isEmpty ? beat.narration : "\(beat.title)\n\(beat.narration)")
        for mc in beat.mechanics { applyMechanic(mc) }
        if !beat.narration.isEmpty { speak(beat.narration) }
        onStatus("beat \(beatIndex + 1)/\(e.beats.count): \(beat.title)")
    }

    private func resetTransient() {
        animators.removeAll()
        for (_, h) in holders {
            if let b = baseXform[ObjectIdentifier(h)] { h.transform = b }
            h.isEnabled = true
            setOpacity(h, 1.0)
        }
        for l in labels { l.removeFromParent() }
        labels.removeAll()
    }

    // MARK: - mechanic dispatch
    private func applyMechanic(_ mc: ModularExperience.MechanicUse) {
        let p = mc.params
        let tgt = holders[mc.target]
        switch mc.mechanic {
        // continuous motion -> animators
        case "spin", "oscillate", "bob", "pulse", "grow_shrink", "reciprocate", "wave":
            if let h = tgt { addAnimator(h, mc.mechanic, p) }
            else { holders.values.forEach { addAnimator($0, mc.mechanic, p) } }
        case "orbit":
            if let h = tgt {
                let c = holders[p.s("center", "")]?.position
                addAnimator(h, "orbit", p, center: c)
            }
        case "play_clip":
            if let h = tgt { playClip(h, loop: p.b("loop", true), speed: p.f("speed", 1)) }
        case "highlight", "pulse_focus":
            if let h = tgt { addAnimator(h, "pulse", ["amount": p["amount"] ?? num(0.1),
                                                      "period_s": p["period_s"] ?? num(1.1)]) }
        // one-shot reveal / focus
        case "explode":
            explode(distance: p.f("distance_m", 0.25))
        case "assemble":
            assemble(from: p.f("from_m", 0.4), duration: p.d("duration_s", 2))
        case "isolate":
            for (id, h) in holders { h.isEnabled = (id == mc.target) }
        case "ghost_others":
            let op = p.f("opacity", 0.18)
            for (id, h) in holders where id != mc.target { setOpacity(h, op) }
        case "zoom_to":
            if let h = tgt { let s = p.f("scale", 1.8); h.move(to: scaled(h, s), relativeTo: h.parent, duration: 0.4) }
        case "fade_in":
            for (_, h) in holders { popIn(h) }
        case "dissolve":
            if let h = tgt { setOpacity(h, 0.0) }
        // annotate
        case "label", "callout", "arrow_point", "dimension_line":
            if let h = tgt { addLabel(p.s("text", exp?.assets.first(where: { $0.id == mc.target })?.name ?? ""), near: h) }
        case "caption", "speak":
            let t = p.s("text", "")
            if !t.isEmpty && mc.mechanic == "caption" { setCaption(t) }
            if !t.isEmpty && mc.mechanic == "speak" { speak(t) }
        case "quiz":
            let q = p.s("question", ""); let opts = p.arr("options")
            if !q.isEmpty { setCaption("❓ \(q)\n" + opts.enumerated().map { "\($0.0 + 1). \($0.1)" }.joined(separator: "   ")) }
        // interaction
        case "rotate_handle":
            if let h = tgt { installRotate(h) }
        case "tap_step":
            break                                  // global tap advances beats
        default:
            print("SPATAIL modular: mechanic '\(mc.mechanic)' not yet implemented (no-op)")
        }
    }

    // MARK: - per-frame tick (continuous animators)
    private func tick() {
        let now = CACurrentMediaTime()
        for a in animators {
            guard let e = a.target else { continue }
            let t = Float(now - a.t0)
            switch a.kind {
            case "spin":
                let ang = Float(a.p.d("rpm", 12) / 60 * 2 * .pi) * t * a.p.f("direction", 1)
                e.orientation = a.base.rotation * simd_quatf(angle: ang, axis: axisVec(a.p.s("axis", "y")))
            case "oscillate":
                let amp = a.p.f("amplitude_deg", 25) * .pi / 180
                let ang = amp * sin(2 * .pi * t / max(0.1, a.p.f("period_s", 2)))
                e.orientation = a.base.rotation * simd_quatf(angle: ang, axis: axisVec(a.p.s("axis", "x")))
            case "bob":
                var pos = a.base.translation
                pos.y += a.p.f("amplitude_m", 0.02) * sin(2 * .pi * t / max(0.1, a.p.f("period_s", 3)))
                e.position = pos
            case "wave":
                var pos = a.base.translation
                pos.y += a.p.f("amplitude_m", 0.05) * sin(2 * .pi * t / max(0.1, a.p.f("speed", 0.4) + 1))
                e.position = pos
            case "pulse":
                let s = 1 + a.p.f("amount", 0.08) * sin(2 * .pi * t / max(0.1, a.p.f("period_s", 1.2)))
                e.scale = a.base.scale * s
            case "grow_shrink":
                let u = (sin(2 * .pi * t / max(0.1, a.p.f("period_s", 2.5))) + 1) / 2
                let s = a.p.f("from", 1) + (a.p.f("to", 1.4) - a.p.f("from", 1)) * u
                e.scale = a.base.scale * s
            case "reciprocate":
                var pos = a.base.translation
                let off = a.p.f("distance_m", 0.1) * sin(2 * .pi * t / max(0.1, a.p.f("period_s", 1)))
                switch a.p.s("axis", "z") { case "x": pos.x += off; case "y": pos.y += off; default: pos.z += off }
                e.position = pos
            case "orbit":
                let c = a.center ?? a.base.translation
                let th = 2 * .pi * t / max(0.1, a.p.f("period_s", 6))
                let r = a.p.f("radius_m", 0.3)
                e.position = SIMD3(c.x + r * cos(th), a.base.translation.y, c.z + r * sin(th))
            default: break
            }
        }
    }

    private func addAnimator(_ e: Entity, _ kind: String, _ p: [String: AnyParam], center: SIMD3<Float>? = nil) {
        animators.append(Animator(target: e, kind: kind, p: p,
                                  base: baseXform[ObjectIdentifier(e)] ?? e.transform,
                                  t0: CACurrentMediaTime(), center: center))
    }

    // MARK: - helpers
    private func explode(distance: Float) {
        let hs = Array(holders.values)
        guard hs.count > 1 else { return }
        var centroid = SIMD3<Float>(repeating: 0)
        for h in hs { centroid += (baseXform[ObjectIdentifier(h)]?.translation ?? h.position) }
        centroid /= Float(hs.count)
        for h in hs {
            let base = baseXform[ObjectIdentifier(h)]?.translation ?? h.position
            var dir = base - centroid; dir.y = 0
            if simd_length(dir) < 1e-4 { dir = SIMD3(1, 0, 0) }
            var t = baseXform[ObjectIdentifier(h)] ?? h.transform
            t.translation = base + simd_normalize(dir) * distance
            h.move(to: t, relativeTo: h.parent, duration: 0.5, timingFunction: .easeOut)
        }
    }

    private func assemble(from: Float, duration: Double) {
        for h in holders.values {
            let base = baseXform[ObjectIdentifier(h)] ?? h.transform
            var start = base; start.translation.y += from
            h.transform = start
            h.move(to: base, relativeTo: h.parent, duration: duration, timingFunction: .easeInOut)
        }
    }

    private func popIn(_ h: Entity) {
        let base = baseXform[ObjectIdentifier(h)] ?? h.transform
        var small = base; small.scale = base.scale * 0.82
        h.transform = small
        h.move(to: base, relativeTo: h.parent, duration: 0.28, timingFunction: .easeOut)
    }

    private func scaled(_ h: Entity, _ s: Float) -> Transform {
        var t = baseXform[ObjectIdentifier(h)] ?? h.transform; t.scale = t.scale * s; return t
    }

    private func setOpacity(_ e: Entity, _ o: Float) {
        if #available(iOS 18.0, *) {
            if o >= 0.999 { e.components.remove(OpacityComponent.self) }
            else { e.components.set(OpacityComponent(opacity: o)) }
        } else if o < 0.05 { e.isEnabled = false }
    }

    private func playClip(_ h: Entity, loop: Bool, speed: Float) {
        guard let model = h.children.first ?? nodes.values.first(where: { $0.parent == h }) else { return }
        if let clip = model.availableAnimations.first {
            let res = loop ? clip.repeat() : clip
            model.playAnimation(res, transitionDuration: 0.2, startsPaused: false)
        }
    }

    private func addLabel(_ text: String, near h: Entity) {
        guard !text.isEmpty else { return }
        let mesh = MeshResource.generateText(text, extrusionDepth: 0.0008,
            font: .systemFont(ofSize: 0.024), containerFrame: CGRect(x: 0, y: 0, width: 0.28, height: 0.08),
            alignment: .center, lineBreakMode: .byWordWrapping)
        let e = ModelEntity(mesh: mesh, materials: [UnlitMaterial(color: .white)])
        e.position = SIMD3(-0.12, 0.16, 0)
        h.addChild(e); labels.append(e)
    }

    private func setCaption(_ text: String) {
        caption?.removeFromParent()
        guard let anchor, !text.isEmpty else { return }
        let mesh = MeshResource.generateText(text, extrusionDepth: 0.001,
            font: .systemFont(ofSize: 0.03, weight: .medium),
            containerFrame: CGRect(x: 0, y: 0, width: 0.6, height: 0.2),
            alignment: .center, lineBreakMode: .byWordWrapping)
        let m = ModelEntity(mesh: mesh, materials: [UnlitMaterial(color: .white)])
        m.position = SIMD3(-0.3, 0.42, 0.05)
        anchor.addChild(m); caption = m
    }

    private func speak(_ text: String) {
        let u = AVSpeechUtterance(string: text)
        u.rate = 0.5
        synth.speak(u)
    }

    private func makeBox(_ asset: ModularExperience.Asset) -> ModelEntity {
        let dims = asset.scaleMeters.count == 3 ? asset.scaleMeters : [0.18, 0.18, 0.18]
        let longest = max(dims.max() ?? 0.18, 0.02)
        let k = Float(min(0.3, longest) / longest)               // cap longest at 30cm for tabletop
        let size = SIMD3(Float(dims[0]) * k, Float(dims[1]) * k, Float(dims[2]) * k)
        let mesh = MeshResource.generateBox(size: size, cornerRadius: 0.006)
        let color: UIColor = asset.role == "primary_object" ? .systemTeal : .systemGray
        let m = ModelEntity(mesh: mesh, materials: [SimpleMaterial(color: color, roughness: 0.6, isMetallic: false)])
        m.generateCollisionShapes(recursive: false)
        m.position.y = size.y / 2
        return m
    }

    private func loadModel(_ assetId: String, _ urlString: String, footprintW: Float) async {
        let baseStr = GenerativeClient.baseURL.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !baseStr.isEmpty, let base = URL(string: baseStr),
              let url = URL(string: urlString, relativeTo: base) else { return }
        do {
            let (tmp, resp) = try await URLSession.shared.download(from: url)
            if let http = resp as? HTTPURLResponse, !(200..<300).contains(http.statusCode) { return }
            let dst = FileManager.default.temporaryDirectory.appendingPathComponent("mod_\(assetId).usdz")
            try? FileManager.default.removeItem(at: dst)
            try FileManager.default.moveItem(at: tmp, to: dst)
            let entity: Entity
            if #available(iOS 18.0, *) { entity = try await Entity(contentsOf: dst) }
            else { entity = try Entity.load(contentsOf: dst) }
            guard let holder = holders[assetId] else { return }
            // fit to footprint width, seat on surface
            let bounds = entity.visualBounds(relativeTo: nil)
            let ext = bounds.extents
            let longest = max(ext.x, max(ext.y, ext.z), 0.001)
            let target = max(min(footprintW, 0.3), 0.05)
            entity.scale = SIMD3(repeating: target / longest)
            let b2 = entity.visualBounds(relativeTo: nil)
            entity.position.y = -b2.min.y * 0 + (b2.extents.y / 2)
            nodes[assetId]?.isEnabled = false
            holder.addChild(entity)
            nodes[assetId] = entity
        } catch { /* keep the box */ }
    }

    private func installTap() {
        guard let view else { return }
        let g = UITapGestureRecognizer(target: self, action: #selector(onTap))
        view.addGestureRecognizer(g)
    }
    @objc private func onTap() { next() }

    private func installRotate(_ h: Entity) {
        if let me = nodes.values.first(where: { $0.parent == h }) as? ModelEntity {
            me.generateCollisionShapes(recursive: true)
            view?.installGestures([.rotation], for: me)
        }
    }

    private func axisVec(_ s: String) -> SIMD3<Float> {
        switch s { case "x": return SIMD3(1, 0, 0); case "z": return SIMD3(0, 0, 1); default: return SIMD3(0, 1, 0) }
    }
    private func num(_ d: Double) -> AnyParam {
        (try? JSONDecoder().decode(AnyParam.self, from: Data("\(d)".utf8))) ?? AnyParam(from: d)
    }

    func clear() {
        updateSub = nil
        animators.removeAll(); labels.removeAll()
        caption = nil
        if let anchor, let view { view.scene.removeAnchor(anchor) }
        anchor = nil; holders.removeAll(); nodes.removeAll(); baseXform.removeAll()
        exp = nil; beatIndex = 0
    }
}

private extension AnyParam {
    init(from d: Double) { double = d; string = nil; bool = nil; strings = nil }
}
#endif
