import SwiftUI
#if os(iOS)
import RealityKit
import ARKit
import Combine
import simd
import UIKit
import CoreImage

// The on-device twin of webxr/viewer.js. Places a brain-authored experience in AR,
// projects each asset's hitbox to a 2D overlay (Identify), drives object-local
// behaviours (Controls), streams camera frames to the Gemini detector (Live), and
// hot-swaps Meshy-generated USDZs in as they finish (the "spawn in").

struct ViewerARView: UIViewRepresentable {
    @ObservedObject var model: ViewerModel

    func makeUIView(context: Context) -> ARView {
        let view = ARView(frame: .zero)
        let cfg = ARWorldTrackingConfiguration()
        cfg.planeDetection = [.horizontal]
        cfg.environmentTexturing = .automatic
        view.session.run(cfg)
        let coord = context.coordinator
        coord.view = view
        let tap = UITapGestureRecognizer(target: coord, action: #selector(Coordinator.onTap(_:)))
        view.addGestureRecognizer(tap)
        coord.updateSub = view.scene.subscribe(to: SceneEvents.Update.self) { [weak coord] e in
            coord?.tick(deltaTime: e.deltaTime)
        }
        // wire the HUD's commands to this coordinator
        model.onGenerate = { [weak coord] p in coord?.generate(prompt: p) }
        model.onBehavior = { [weak coord] id, verb in coord?.applyBehavior(id, verb) }
        model.onSelect = { [weak coord] id in coord?.select(id) }
        model.onReplaceAnchor = { [weak coord] in coord?.replaceAnchor() }
        return view
    }

    func updateUIView(_ view: ARView, context: Context) {}
    func makeCoordinator() -> Coordinator { Coordinator(vm: model) }

    @MainActor
    final class Coordinator: NSObject {
        let vm: ViewerModel
        weak var view: ARView?
        var net: ViewerNet { ViewerNet(brain: vm.brainURL, detector: vm.detectorURL) }

        // A placed object is a virtual LIVING entity: base transform + vitality (energy,
        // phase) + smoothed awareness (att) + idle motion accumulators. The verbs below
        // only set state; updateLife composes the final transform every frame.
        struct Placed {
            let asset: ModularExperience.Asset
            var holder: Entity
            var node: Entity
            let intent: String
            let affordances: [String]
            let why: String
            var baseScale: SIMD3<Float>
            var basePos: SIMD3<Float>
            var baseYaw: Float
            var archetype: String     // nature-in-context: still|mechanical|vital|pendular|celestial|organic|creature
            var phase: Float
            var placeholder: Bool
            var spinning = false
            var scaleStepF: Float = 1
            var explodeOff: SIMD3<Float> = .zero
            var att: Float = 0
            var idleYaw: Float = 0
            var userYaw: Float = 0
        }

        var anchor: AnchorEntity?
        var placed: [String: Placed] = [:]
        var order: [String] = []
        var exp: ModularExperience?
        var primaryId = ""
        var centroid = SIMD3<Float>(0, 0, 0)
        var isolatedId: String?
        var explodeOn = false
        var updateSub: Cancellable?
        private var lastIdentify = 0.0
        private var lastDetect = 0.0
        private var detecting = false
        private var pollTask: Task<Void, Never>?
        private let ci = CIContext()
        private let startTime = CACurrentMediaTime()
        private var focusedId: String?
        private var lastFocus = 0.0
        private var haloEntity: ModelEntity?

        init(vm: ViewerModel) { self.vm = vm; super.init() }

        // MARK: - generate from the brain (+ Meshy poll + hot-swap)
        func generate(prompt: String) {
            guard !prompt.isEmpty else { return }
            pollTask?.cancel()
            vm.generating = true; vm.progress = nil
            vm.status = "Asking the brain: “\(prompt)”…"
            Task { @MainActor in
                do {
                    let e = try await net.generateModular(text: prompt)
                    place(e)
                    vm.title = e.title
                    vm.subtitle = e.understanding.intent.capitalized + " · " + e.understanding.domain
                    vm.status = e.generationJobId != nil ? "Generating the model with Meshy…" : "Placed."
                    vm.generating = false
                    if let job = e.generationJobId { pollMeshy(job) }
                } catch {
                    vm.generating = false
                    vm.status = "Brain error: \(error.localizedDescription)"
                }
            }
        }

        private func pollMeshy(_ jobId: String) {
            pollTask = Task { @MainActor in
                do {
                    let url = try await net.awaitGeneratedUSDZ(jobId: jobId) { [weak self] s in
                        // net runs off the main actor; hop back to touch @MainActor state.
                        Task { @MainActor in
                            guard let self else { return }
                            if let t = s.stageTotal {
                                self.vm.progress = GenProgress(stage: s.stage ?? s.status,
                                                               index: s.stageIndex ?? 0, total: t, percent: s.percent ?? 0)
                            }
                            self.vm.status = "Meshy: \(s.stage ?? s.status)"
                        }
                    }
                    vm.progress = nil
                    // swap the real Meshy mesh into the primary holder ("spawn in")
                    await swapModel(primaryId, fileURL: url)
                    vm.status = "Meshy asset live."
                } catch {
                    vm.progress = nil
                    if !(error is CancellationError) { vm.status = "Meshy: \(error.localizedDescription)" }
                }
            }
        }

        // MARK: - place an experience
        func place(_ e: ModularExperience) {
            clear()
            guard let view else { return }
            exp = e
            primaryId = LayoutSolver.primaryId(e)
            let a = AnchorEntity(world: Self.placementTransform(in: view, anchorType: e.placement.anchorType))
            view.scene.addAnchor(a)
            anchor = a
            let halo = makeHalo(); a.addChild(halo); haloEntity = halo
            let slots = LayoutSolver.solve(e)
            var sum = SIMD3<Float>(0, 0, 0)
            for (i, slot) in slots.enumerated() {
                guard let asset = e.assets.first(where: { $0.id == slot.id }) else { continue }
                let holder = Entity(); holder.position = slot.local; a.addChild(holder)
                let box = makeBox(footprint: slot.footprint, primary: asset.role == "primary_object")
                holder.addChild(box)
                let intent = LayoutSolver.intent(e, asset: asset, primaryId: primaryId)
                let placed = Placed(
                    asset: asset, holder: holder, node: box,
                    intent: intent,
                    affordances: LayoutSolver.affordances(e, asset: asset),
                    why: asset.description.isEmpty ? e.understanding.summary : asset.description,
                    baseScale: holder.scale, basePos: slot.local, baseYaw: 0,
                    archetype: natureFor(asset, e), phase: Float(i) * 1.7, placeholder: asset.usdzUrl.isEmpty)
                self.placed[slot.id] = placed; order.append(slot.id)
                sum += slot.local
                if !asset.usdzUrl.isEmpty, let urlStr = net.assetURLString(asset.usdzUrl) {
                    Task { @MainActor in await self.loadAsset(slot.id, urlString: urlStr, footprint: slot.footprint) }
                }
            }
            centroid = slots.isEmpty ? .zero : sum / Float(slots.count)
        }

        private func loadAsset(_ id: String, urlString: String, footprint: SIMD3<Float>) async {
            guard let url = URL(string: urlString) else { return }
            do {
                let local = try await net.download(url.path, name: "asset_\(id)")
                await swapModel(id, fileURL: local, footprint: footprint)
            } catch { print("SPATAIL viewer: load \(id) failed: \(error)") }
        }

        /// Load a USDZ file into an asset's holder, fit + seat it, and replace the box.
        private func swapModel(_ id: String, fileURL: URL, footprint: SIMD3<Float>? = nil) async {
            guard var p = placed[id] else { return }
            do {
                let entity: Entity
                if #available(iOS 18.0, *) { entity = try await Entity(contentsOf: fileURL) }
                else { entity = try Entity.load(contentsOf: fileURL) }
                let fp = footprint ?? LayoutSolver.footprint(p.asset)
                fitToReal(entity, asset: p.asset, budget: max(min(fp.x, fp.z, 0.4), 0.06))
                p.node.removeFromParent()
                p.holder.addChild(entity)
                p.node = entity
                p.placeholder = false
                placed[id] = p
                // a thing's baked clip IS its nature (engine running, plant growing) —
                // play it, unless the thing's nature is stillness.
                if p.archetype != "still" {
                    for anim in entity.availableAnimations { entity.playAnimation(anim.repeat(), transitionDuration: 0.3) }
                }
            } catch { print("SPATAIL viewer: swap \(id) failed: \(error)") }
        }

        // MARK: - per-frame tick (identify projection + behaviours + live perception)
        func tick(deltaTime dt: Double) {
            let now = CACurrentMediaTime()
            // objects live: breathe, idle, notice the user, express by intent
            if now - lastFocus > 0.12 { lastFocus = now; updateFocus() }
            updateLife(dt: Float(dt), now: now)
            if vm.identifyOn, now - lastIdentify > 0.08 { lastIdentify = now; updateIdentify() }
            else if !vm.identifyOn && !vm.identified.isEmpty { vm.identified = [] }
            if vm.liveOn, now - lastDetect > 1.6, !detecting { lastDetect = now; runDetection() }
            else if !vm.liveOn && !vm.detections.isEmpty { vm.detections = [] }
        }

        // MARK: - living-entity layer (MF p38–p43): breathe / idle / awareness / presence
        private func updateFocus() {
            guard let view, !placed.isEmpty else { focusedId = nil; return }
            let pt = CGPoint(x: view.bounds.midX, y: view.bounds.midY)
            if let e = view.entity(at: pt), let id = holderId(for: e) { focusedId = id }
            else { focusedId = vm.selectedId }
        }

        private func updateLife(dt: Float, now: Double) {
            guard let view else { return }
            let cam = view.cameraTransform.translation
            let t = Float(now - startTime)
            var topAtt: Float = 0; var topId: String?
            for id in order {
                guard var p = placed[id] else { continue }
                p.att += (((id == focusedId) ? 1 : 0) - p.att) * min(1, dt * 4)
                // behaviour DICTATED BY NATURE — stillness is as alive as motion.
                var scaleMul = p.scaleStepF, bob: Float = 0, rx: Float = 0, rz: Float = 0, face: Float = 0, idle: Float = 0
                switch p.archetype {
                case "still":      face = p.att * 0.5                                                                     // exists by presence
                case "creature":   bob = 0.006 * sin(t * 1.1 + p.phase); scaleMul *= 1 + 0.012 * sin(t * 1.1 + p.phase); face = p.att * 0.85; idle = 0.04
                case "organic":    rz = 0.035 * sin(t * 0.7 + p.phase); scaleMul *= 1 + 0.01 * sin(t * 0.45 + p.phase); idle = 0.05; face = p.att * 0.25
                case "vital":      scaleMul *= 1 + 0.05 * pulse(t * 1.1 + p.phase)                                        // heartbeat
                case "pendular":   rx = 0.5 * sin(t * 1.5 + p.phase)                                                      // swings
                case "celestial":  idle = 0.25                                                                            // majestic turn
                case "mechanical": if p.node.availableAnimations.isEmpty { idle = 0.16 }; face = p.att * 0.35            // runs (clip, else working turn)
                default:           break
                }
                p.idleYaw += idle * dt * (1 - 0.5 * p.att)
                if p.spinning { p.userYaw += 1.1 * dt }
                if p.archetype != "vital" { scaleMul *= 1 + p.att * 0.03 }
                p.holder.scale = p.baseScale * scaleMul
                var pos = p.basePos + p.explodeOff; pos.y += bob
                p.holder.position = pos
                let faceYaw = atan2(cam.x - pos.x, cam.z - pos.z)
                let yaw = simd_quatf(angle: lerpAngle(p.baseYaw + p.idleYaw + p.userYaw, faceYaw, face), axis: SIMD3(0, 1, 0))
                p.holder.orientation = yaw * simd_quatf(angle: rx, axis: SIMD3(1, 0, 0)) * simd_quatf(angle: rz, axis: SIMD3(0, 0, 1))
                if p.att > topAtt { topAtt = p.att; topId = id }
                placed[id] = p
            }
            // presence halo under the thing that's noticing you
            if let topId, let p = placed[topId], topAtt > 0.06, let halo = haloEntity {
                let fp = LayoutSolver.footprint(p.asset)
                halo.isEnabled = true
                halo.position = SIMD3(p.holder.position.x, 0.004, p.holder.position.z)
                let r = max(fp.x, fp.z) * 0.85 + 0.05
                halo.scale = SIMD3(repeating: r)
                setOpacity(halo, topAtt * 0.5)
            } else { haloEntity?.isEnabled = false }
        }

        private func lerpAngle(_ a: Float, _ b: Float, _ t: Float) -> Float {
            var d = (b - a).truncatingRemainder(dividingBy: 2 * .pi)
            if d > .pi { d -= 2 * .pi }; if d < -.pi { d += 2 * .pi }
            return a + d * t
        }
        // nature-in-context → behaviour archetype (on-device stand-in for the brain's
        // understanding; the director should emit this so the runtime just expresses it).
        private func natureFor(_ asset: ModularExperience.Asset, _ e: ModularExperience) -> String {
            let domain = e.understanding.domain.lowercased()
            let text = (asset.name + " " + asset.id + " " + e.understanding.subject).lowercased()
            func has(_ ws: [String]) -> Bool { ws.contains { text.contains($0) } }
            if has(["heart", "lung", "pulse", "cardiac", "beat"]) { return "vital" }
            if has(["pendulum", "metronome", "swing"]) { return "pendular" }
            if domain == "astronomy" || has(["planet", "star", "sun", "moon", "earth", "mars", "jupiter", "saturn", "galaxy", "orbit"]) { return "celestial" }
            if has(["frog", "lion", "dog", "cat", "bird", "fish", "animal", "creature", "dragon", "person", "gnome", "character", "insect", "reptile"]) { return "creature" }
            if domain == "biology" || has(["plant", "tree", "flower", "leaf", "seed", "sprout", "cell", "bacteria", "virus", "grow", "neuron"]) { return "organic" }
            if domain == "mechanical" || has(["engine", "motor", "machine", "gear", "turbine", "pump", "piston", "crank", "cam", "vehicle", "bike"]) { return "mechanical" }
            return "still"   // products, furniture, architecture, things to place/compare → quiet presence
        }
        // a heartbeat: a double-thump per cycle, not a sine.
        private func pulse(_ beat: Float) -> Float {
            let p = beat - floor(beat)
            let t1 = exp(-pow((p - 0.08) / 0.05, 2))
            let t2 = 0.55 * exp(-pow((p - 0.26) / 0.05, 2))
            return min(1, t1 + t2)
        }
        private func makeHalo() -> ModelEntity {
            // flat translucent presence disc (RealityKit has no ring primitive)
            let m = ModelEntity(mesh: .generatePlane(width: 1, depth: 1, cornerRadius: 0.5),
                                materials: [UnlitMaterial(color: .systemTeal)])
            m.isEnabled = false
            return m
        }

        private func updateIdentify() {
            guard let view else { return }
            var items: [IdentifiedItem] = []
            for id in order {
                guard let p = placed[id], let rect = screenRect(of: p.node, in: view) else { continue }
                items.append(IdentifiedItem(
                    id: id, title: p.asset.name.isEmpty ? id : p.asset.name, intent: p.intent,
                    color: vm.intentColor(p.intent), rect: rect, affordances: p.affordances,
                    why: p.why, placeholder: p.placeholder))
            }
            vm.identified = items
        }

        private func screenRect(of e: Entity, in view: ARView) -> CGRect? {
            let b = e.visualBounds(relativeTo: nil)
            guard b.extents.x.isFinite, b.extents.x >= 0 else { return nil }
            let c = b.center, ext = b.extents
            var minX = CGFloat.greatestFiniteMagnitude, minY = CGFloat.greatestFiniteMagnitude
            var maxX = -CGFloat.greatestFiniteMagnitude, maxY = -CGFloat.greatestFiniteMagnitude
            var any = false
            for sx: Float in [-1, 1] { for sy: Float in [-1, 1] { for sz: Float in [-1, 1] {
                let corner = c + SIMD3(ext.x / 2 * sx, ext.y / 2 * sy, ext.z / 2 * sz)
                if let pt = view.project(corner) {
                    any = true
                    minX = min(minX, pt.x); minY = min(minY, pt.y)
                    maxX = max(maxX, pt.x); maxY = max(maxY, pt.y)
                }
            }}}
            guard any, maxX > minX else { return nil }
            return CGRect(x: minX, y: minY, width: maxX - minX, height: maxY - minY)
        }

        // MARK: - live perception (camera frame -> Gemini detector)
        private func runDetection() {
            guard let frame = view?.session.currentFrame else { return }
            detecting = true
            let pixel = frame.capturedImage
            Task { @MainActor in
                defer { detecting = false }
                guard let jpeg = self.jpeg(from: pixel) else { return }
                do { self.vm.detections = try await self.net.detect(jpeg: jpeg) }
                catch { self.vm.status = "Detector: \(error.localizedDescription)" }
            }
        }

        private func jpeg(from pixel: CVPixelBuffer) -> Data? {
            var img = CIImage(cvPixelBuffer: pixel).oriented(.right)   // portrait for the back camera
            let w = img.extent.width
            if w > 720 { let s = 720 / w; img = img.transformed(by: CGAffineTransform(scaleX: s, y: s)) }
            guard let cg = ci.createCGImage(img, from: img.extent) else { return nil }
            return UIImage(cgImage: cg).jpegData(compressionQuality: 0.6)
        }

        // MARK: - object-local controls (MF p35/p43)
        func applyBehavior(_ id: String, _ verb: String) {
            guard var p = placed[id] else { return }
            // verbs only set STATE — updateLife composes the transform each frame on top
            // of the object's ongoing life.
            switch verb {
            case "rotate": p.spinning.toggle(); placed[id] = p
            case "animate":
                for anim in p.node.availableAnimations { p.node.playAnimation(anim.repeat(), transitionDuration: 0.2) }
            case "scale":
                let steps: [Float] = [1, 1.5, 0.6]
                p.scaleStepF = steps[((steps.firstIndex(of: p.scaleStepF) ?? 0) + 1) % 3]; placed[id] = p
            case "isolate":
                if isolatedId == id { isolatedId = nil; for (_, q) in placed { setOpacity(q.holder, 1) } }
                else { isolatedId = id; for (k, q) in placed { setOpacity(q.holder, k == id ? 1 : 0.12) } }
            case "explode":
                explodeOn.toggle()
                for (k, q) in placed { var r = q; r.explodeOff = explodeOn ? (q.basePos - centroid) * 0.7 : .zero; placed[k] = r }
            case "reset":
                isolatedId = nil; explodeOn = false
                for (k, q) in placed {
                    var r = q; r.spinning = false; r.scaleStepF = 1; r.explodeOff = .zero; r.userYaw = 0
                    setOpacity(r.holder, 1); placed[k] = r
                }
            default:
                vm.status = "“\(verb)” is part of \(p.asset.name)'s living repertoire (not nudgeable here)."
            }
        }

        func select(_ id: String) { vm.selectedId = id }
        @objc func onTap(_ g: UITapGestureRecognizer) {
            guard let view else { return }
            let pt = g.location(in: view)
            if let e = view.entity(at: pt), let id = holderId(for: e) { select(id) }
        }
        private func holderId(for e: Entity) -> String? {
            var cur: Entity? = e
            while let c = cur { if let k = placed.first(where: { $0.value.holder === c })?.key { return k }; cur = c.parent }
            return nil
        }

        func replaceAnchor() {
            guard let e = exp else { return }
            place(e)   // re-solve placement where you aim now
        }

        // MARK: - helpers
        private func makeBox(footprint fp: SIMD3<Float>, primary: Bool) -> ModelEntity {
            let longest = max(fp.x, fp.y, fp.z, 0.02)
            let k = min(0.3, longest) / longest
            let size = SIMD3(max(fp.x * k, 0.02), max(fp.y * k, 0.02), max(fp.z * k, 0.02))
            let m = ModelEntity(mesh: .generateBox(size: size, cornerRadius: 0.006),
                                materials: [SimpleMaterial(color: primary ? .systemTeal : .systemGray, roughness: 0.6, isMetallic: false)])
            m.generateCollisionShapes(recursive: false)
            m.position.y = size.y / 2
            return m
        }

        private func fitToReal(_ entity: Entity, asset: ModularExperience.Asset, budget: Float) {
            if asset.realScaleBaked { entity.scale = .one; seat(entity); return }
            if let real = asset.realSizeMeters, let longestReal = real.max(), longestReal > 0.01 {
                let b = entity.visualBounds(relativeTo: nil)
                let longest = max(b.extents.x, b.extents.y, b.extents.z, 0.001)
                entity.scale = SIMD3(repeating: Float(longestReal) / longest); seat(entity); return
            }
            let b = entity.visualBounds(relativeTo: nil)
            let longest = max(b.extents.x, b.extents.y, b.extents.z, 0.001)
            entity.scale = SIMD3(repeating: budget / longest); seat(entity)
        }
        private func seat(_ entity: Entity) {
            let b = entity.visualBounds(relativeTo: nil)
            entity.position.y = b.extents.y / 2 - b.center.y
            entity.position.x -= b.center.x; entity.position.z -= b.center.z
        }
        private func setOpacity(_ e: Entity, _ o: Float) {
            if #available(iOS 18.0, *) {
                if o >= 0.999 { e.components.remove(OpacityComponent.self) }
                else { e.components.set(OpacityComponent(opacity: o)) }
            }
        }

        private static func placementTransform(in view: ARView, anchorType: String) -> simd_float4x4 {
            let cam = view.cameraTransform
            var origin: SIMD3<Float>
            let pt = CGPoint(x: view.bounds.midX, y: view.bounds.midY)
            let alignment: ARRaycastQuery.TargetAlignment = (anchorType == "wall") ? .vertical : .horizontal
            if let hit = view.raycast(from: pt, allowing: .estimatedPlane, alignment: alignment).first {
                let c = hit.worldTransform.columns.3; origin = SIMD3(c.x, c.y, c.z)
            } else {
                let f = -SIMD3(cam.matrix.columns.2.x, 0, cam.matrix.columns.2.z)
                let fwd = simd_length(f) > 1e-4 ? simd_normalize(f) : SIMD3(0, 0, -1)
                origin = cam.translation + fwd * 0.8
                origin.y = cam.translation.y - (anchorType == "wall" ? 0.0 : 0.6)
            }
            let toCam = cam.translation - origin
            return Transform(scale: .one,
                             rotation: simd_quatf(angle: atan2(toCam.x, toCam.z), axis: SIMD3(0, 1, 0)),
                             translation: origin).matrix
        }

        func clear() {
            pollTask?.cancel()
            if let a = anchor, let view { view.scene.removeAnchor(a) }
            anchor = nil; placed.removeAll(); order.removeAll(); exp = nil
            isolatedId = nil; explodeOn = false
            vm.identified = []; vm.detections = []
        }
    }
}

#else
// visionOS placeholder — the RealityView volumetric player lands in the Vision Pro build.
struct ViewerARView: View {
    @ObservedObject var model: ViewerModel
    var body: some View { Color.black.overlay(Text("Vision Pro player — coming in the XR build").foregroundStyle(.white)) }
}
#endif
