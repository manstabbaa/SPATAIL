import SwiftUI
#if os(iOS)
import RealityKit
import ARKit
import UIKit
import simd

// RepresentationRuntime — the post-compile interpreter for the SPATAIL
// Representation Engine's RuntimeSceneContract. Mirrors ExperienceRuntime, but
// instead of station USDZs it renders the brain's plan as labeled primitive
// boxes (the factory currently produces primitive geometry, and RealityKit can't
// load the factory's GLB — real recognizable models are a later USDZ-export
// follow-on). What it proves on-device: the brain drives the scene, PROGRESSIVE
// loading shows an anchor + placeholder within ~1 s, then assets stream/reveal,
// beats narrate, and interactions unlock — all from the downloaded JSON.
//
// Placement uses StationLayout (plane-relative comfort arc), NOT the contract's
// eye-relative position_yup, so boxes sit correctly on the detected surface.

@MainActor
final class RepresentationRuntime {
    private weak var view: ARView?
    private var rootAnchor: AnchorEntity?
    private var holders: [String: Entity] = [:]        // assetId -> holder (placed on arc)
    private var boxes: [String: ModelEntity] = [:]      // assetId -> box
    private var billboardPanels: [Entity] = []
    private var titlePanel: Entity?
    private var narrationPanel: Entity?

    private var bundle: RepresentationBundle?
    private var heroId: String = ""
    private var beatIndex = 0
    private var interactionsEnabled = false
    private var exploded = false
    private var isolatedId: String?

    private let onStatus: (String) -> Void

    init(view: ARView, onStatus: @escaping (String) -> Void = { _ in }) {
        self.view = view
        self.onStatus = onStatus
    }

    // MARK: - present
    func present(_ bundle: RepresentationBundle) {
        clear()
        self.bundle = bundle
        guard let view else { return }
        let c = bundle.contract
        guard c.schemaVersion.hasPrefix("0.") else {
            print("SPATAIL: representation schema \(c.schemaVersion) incompatible"); return
        }

        let anchor = AnchorEntity(plane: .horizontal, minimumBounds: [0.15, 0.15])
        rootAnchor = anchor
        view.scene.addAnchor(anchor)

        heroId = c.assets.first(where: { $0.role == "primary_object" })?.id
            ?? c.assets.first?.id ?? ""

        // plane-relative poses (comfort arc), seeded by the brain's layout intent.
        let widths = c.assets.map { Float($0.footprint.w) }
        let poses = StationLayout.poses(count: c.assets.count,
                                        footprintWidths: widths,
                                        comfortRadiusM: c.staging.distanceM,
                                        spacingMinM: 0.12,
                                        layout: c.placement.layout)
        for (i, asset) in c.assets.enumerated() {
            let holder = Entity()
            holder.position = poses[i].position
            holder.orientation = simd_quatf(angle: poses[i].yawRadians, axis: SIMD3(0, 1, 0))
            anchor.addChild(holder)
            holders[asset.id] = holder

            let box = makeBox(asset, color: Self.placeholderColor)
            box.isEnabled = false
            holder.addChild(box)
            boxes[asset.id] = box
        }

        addScenePanels(c)
        revealPhase0()                                   // <1 s: placeholder + title + first beat
        Task { await self.runPhases() }                  // then stream / reveal / unlock
        print("SPATAIL: presenting representation '\(c.title)' — \(c.assets.count) assets, "
              + "\(c.beats.count) beats [\(c.representation.domain)/\(c.representation.intent)/\(c.representation.strategy)]")
    }

    // MARK: - progressive loading
    private func revealPhase0() {
        guard let b = bundle else { return }
        var ids = Set(b.progressive.loadPlan.phases.first?.assets ?? [])
        if ids.isEmpty { ids = Set(b.progressive.placeholderScene.placeholders.map { $0.assetId }) }
        if ids.isEmpty, !heroId.isEmpty { ids = [heroId] }
        for id in ids { reveal(id, color: Self.placeholderColor, pop: false) }
        showBeat(0)
        onStatus("placeholder ready")
    }

    private func runPhases() async {
        guard let b = bundle else { return }
        for phase in b.progressive.loadPlan.phases.dropFirst() {
            try? await Task.sleep(nanoseconds: 500_000_000)   // ~0.5 s between phases
            onStatus(phase.label)
            if phase.assets.isEmpty {                          // the "unlock" phase
                interactionsEnabled = true
            } else {
                for id in phase.assets { reveal(id, color: color(forAsset: id), pop: true) }
            }
        }
        interactionsEnabled = true                             // safety: always end interactive
        onStatus("ready — tap the model")
    }

    private func reveal(_ id: String, color: UIColor, pop: Bool) {
        guard let box = boxes[id] else { return }
        box.isEnabled = true
        setColor(box, color)
        if pop {
            let final = box.transform
            box.scale = SIMD3(repeating: 0.82)
            box.move(to: final, relativeTo: box.parent, duration: 0.22, timingFunction: .easeOut)
        }
    }

    // MARK: - beats
    func showBeat(_ i: Int) {
        guard let c = bundle?.contract, !c.beats.isEmpty else { return }
        beatIndex = max(0, min(i, c.beats.count - 1))
        let beat = c.beats[beatIndex]
        // refresh the narration panel
        if let old = narrationPanel {
            old.removeFromParent()
            billboardPanels.removeAll { $0 === old }
        }
        let panel = PanelFactory.make(ExperienceSpec.Panel(id: "rep_narration", kind: "caption",
                                                           body: beat.narration))
        attach(panel, to: heroId, offset: SIMD3(0, -0.05, 0))
        narrationPanel = panel
        // a gentle highlight pulse on the hero so beat changes are felt
        if let box = boxes[heroId] { pulse(box) }
    }

    func next() { showBeat(beatIndex + 1) }
    func prev() { showBeat(beatIndex - 1) }

    // MARK: - interactions (tap)
    func handleTap(on entity: Entity) {
        guard interactionsEnabled else { onStatus("still loading…"); return }
        guard let id = assetId(of: entity), let c = bundle?.contract else { return }
        if id == heroId {
            if c.interactions.contains(where: { $0.id.contains("explode") || $0.id.contains("assemble") || $0.id.contains("step") }) {
                toggleExplode()
            } else if c.interactions.contains(where: { $0.type == "play_baked" }) {
                spin(boxes[heroId])
            } else {
                isolate(id)
            }
        } else {
            isolate(id)
        }
    }

    private func toggleExplode() {
        exploded.toggle()
        onStatus(exploded ? "exploded" : "assembled")
        guard let heroHolder = holders[heroId] else { return }
        let center = heroHolder.position
        for (id, holder) in holders where id != heroId {
            let base = holder.position
            var dir = SIMD3(base.x - center.x, 0, base.z - center.z)
            if length(dir) < 1e-4 { dir = SIMD3(0, 0, -1) }
            dir = normalize(dir)
            let target = exploded ? base + dir * 0.16 : restPosition(of: id, fallback: base)
            holder.move(to: Transform(scale: holder.scale, rotation: holder.orientation,
                                      translation: target),
                        relativeTo: holder.parent, duration: 0.4, timingFunction: .easeInOut)
        }
    }

    private func isolate(_ id: String) {
        if isolatedId == id {                              // tap again → restore everyone
            isolatedId = nil
            for (aid, box) in boxes { setColor(box, color(forAsset: aid)) }
            onStatus("ready — tap the model")
        } else {
            isolatedId = id
            for (aid, box) in boxes { setColor(box, aid == id ? color(forAsset: aid) : Self.dimColor) }
            onStatus("isolated \(id)")
        }
    }

    private func spin(_ box: ModelEntity?) {
        guard let box else { return }
        // A half-turn nudge per tap — uses only the well-supported move(to:) API
        // (a baked looping clip arrives when real USDZ assets land, see header).
        var t = box.transform
        t.rotation = simd_quatf(angle: .pi, axis: SIMD3(0, 1, 0)) * t.rotation
        box.move(to: t, relativeTo: box.parent, duration: 0.8, timingFunction: .easeInOut)
        onStatus("playing")
    }

    // MARK: - per-frame billboard (iOS 17 fallback; no-op on iOS 18)
    func faceBillboards(toward camera: SIMD3<Float>) {
        if #available(iOS 18.0, *) { return }
        for p in billboardPanels {
            let world = p.position(relativeTo: nil)
            let dir = camera - world
            guard length(dir) > 1e-4 else { continue }
            p.setOrientation(simd_quatf(angle: atan2(dir.x, dir.z), axis: SIMD3(0, 1, 0)),
                             relativeTo: nil)
        }
    }

    // MARK: - panels + boxes
    private func addScenePanels(_ c: RuntimeSceneContract) {
        let heroH = Float(c.assets.first(where: { $0.id == heroId })?.footprint.h ?? 0.3)
        let title = PanelFactory.make(ExperienceSpec.Panel(
            id: "rep_title", kind: "title",
            title: c.title, body: "\(c.representation.domain) · \(c.representation.intent) · \(c.representation.strategy)"))
        attach(title, to: heroId, offset: SIMD3(0, heroH + 0.14, 0))
        titlePanel = title
    }

    private func attach(_ panel: Entity, to assetId: String, offset: SIMD3<Float>) {
        let holder = holders[assetId] ?? rootAnchor
        panel.position = offset
        if #available(iOS 18.0, *) { panel.components.set(BillboardComponent()) }
        billboardPanels.append(panel)
        holder?.addChild(panel)
    }

    private func makeBox(_ asset: RuntimeSceneContract.Asset, color: UIColor) -> ModelEntity {
        let f = asset.footprint
        let size = SIMD3(max(Float(f.w), 0.02), max(Float(f.h), 0.02), max(Float(f.d), 0.02))
        let mesh = MeshResource.generateBox(size: size, cornerRadius: 0.006)
        let box = ModelEntity(mesh: mesh, materials: [SimpleMaterial(color: color, roughness: 0.55, isMetallic: false)])
        box.position = SIMD3(0, size.y / 2, 0)             // rest on the plane
        box.name = "rbox:\(asset.id)"
        box.generateCollisionShapes(recursive: false)
        if #available(iOS 18.0, *) { box.components.set(InputTargetComponent()) }
        return box
    }

    private func setColor(_ box: ModelEntity, _ color: UIColor) {
        if var m = box.model {
            m.materials = [SimpleMaterial(color: color, roughness: 0.55, isMetallic: false)]
            box.model = m
        }
    }

    private func pulse(_ box: ModelEntity) {
        let up = box.transform
        var big = up; big.scale = up.scale * 1.12
        box.move(to: big, relativeTo: box.parent, duration: 0.16, timingFunction: .easeOut)
        box.move(to: up, relativeTo: box.parent, duration: 0.16, timingFunction: .easeIn)
    }

    // MARK: - helpers
    private func assetId(of entity: Entity) -> String? {
        var e: Entity? = entity
        while let cur = e {
            if cur.name.hasPrefix("rbox:") { return String(cur.name.dropFirst(5)) }
            e = cur.parent
        }
        return nil
    }

    private func restPosition(of id: String, fallback: SIMD3<Float>) -> SIMD3<Float> {
        // recompute the seated arc position so "assemble" returns parts home
        guard let c = bundle?.contract,
              let idx = c.assets.firstIndex(where: { $0.id == id }) else { return fallback }
        let widths = c.assets.map { Float($0.footprint.w) }
        let poses = StationLayout.poses(count: c.assets.count, footprintWidths: widths,
                                        comfortRadiusM: c.staging.distanceM,
                                        spacingMinM: 0.12, layout: c.placement.layout)
        return idx < poses.count ? poses[idx].position : fallback
    }

    private func color(forAsset id: String) -> UIColor {
        let role = bundle?.contract.assets.first(where: { $0.id == id })?.role ?? ""
        switch role {
        case "primary_object": return Self.heroColor
        case "comparison_object": return Self.compareColor
        case "environment": return Self.envColor
        default: return Self.partColor
        }
    }

    static let heroColor = UIColor.systemIndigo
    static let partColor = UIColor.systemTeal
    static let compareColor = UIColor.systemOrange
    static let envColor = UIColor.systemGray
    static let placeholderColor = UIColor.systemGray3
    static let dimColor = UIColor.systemGray4

    func clear() {
        if let a = rootAnchor { view?.scene.removeAnchor(a) }
        rootAnchor = nil
        holders.removeAll(); boxes.removeAll(); billboardPanels.removeAll()
        titlePanel = nil; narrationPanel = nil
        bundle = nil; heroId = ""; beatIndex = 0
        interactionsEnabled = false; exploded = false; isolatedId = nil
    }
}
#endif
