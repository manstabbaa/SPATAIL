import SwiftUI
#if os(iOS)
import RealityKit
import ARKit

// ExperienceRuntime — reads an ExperienceSpec and assembles it in the user's room:
// places each station's hero USDZ on a comfort arc (StationLayout), attaches its
// spatial-UI panels, and wires the v0.1 mechanics. iOS implementation; the spec +
// layout math are shared with the future visionOS target.
//
// This is the "post-compile" interpreter: no station, panel, or mechanic is
// hard-coded — they all come from the downloaded JSON.

@MainActor
final class ExperienceRuntime {
    private weak var view: ARView?
    private var rootAnchor: AnchorEntity?
    private var stationEntities: [String: Entity] = [:]
    private var panelEntities: [String: Entity] = [:]      // panelId -> attachment entity
    private var billboardPanels: [Entity] = []             // panels to face camera on iOS 17
    private var focusedStation: Int = 0
    private var spec: ExperienceSpec?
    private let usdzDir: URL                                 // where station USDZs were downloaded

    init(view: ARView, usdzDir: URL) {
        self.view = view
        self.usdzDir = usdzDir
    }

    // MARK: - assemble the whole experience
    func present(_ spec: ExperienceSpec) async {
        guard spec.isCompatible else {
            print("SPATAIL: experience spec_version \(spec.specVersion) incompatible")
            return
        }
        clear()
        self.spec = spec
        guard let view else { return }

        // anchor the experience on a real horizontal surface (table/floor → both horizontal)
        let anchor = AnchorEntity(plane: .horizontal, minimumBounds: [0.15, 0.15])
        rootAnchor = anchor
        view.scene.addAnchor(anchor)

        let stations = spec.orderedStations
        let widths = stations.map { Float($0.hero.footprintM.w) }
        let poses = StationLayout.poses(count: stations.count,
                                        footprintWidths: widths,
                                        comfortRadiusM: spec.placement.comfortRadiusM,
                                        spacingMinM: spec.placement.spacingMinM,
                                        layout: spec.placement.layout)

        for (i, station) in stations.enumerated() {
            let pose = poses[i]
            await placeStation(station, pose: pose, into: anchor,
                               dynamic: station.hero.scaleMode == "dynamic")
        }

        // guided mode: focus the first station, dim the rest
        if spec.placement.guided { focus(0) }
        print("SPATAIL: presented experience '\(spec.title)' — \(stations.count) stations")
    }

    // MARK: - one station: hero + panels + mechanics
    private func placeStation(_ station: ExperienceSpec.Station,
                              pose: StationPose,
                              into anchor: AnchorEntity,
                              dynamic: Bool) async {
        let holder = Entity()
        holder.position = pose.position
        holder.orientation = simd_quatf(angle: pose.yawRadians, axis: SIMD3(0, 1, 0))
        anchor.addChild(holder)
        stationEntities[station.id] = holder

        // --- hero USDZ ---
        let url = usdzDir.appendingPathComponent(station.hero.usdz)
        do {
            let hero: Entity
            if #available(iOS 18.0, *) { hero = try await Entity(contentsOf: url) }
            else { hero = try Entity.load(contentsOf: url) }

            // scale: dynamic → fit largest dim to the footprint width so it reads as UI;
            // fixed → leave authored real-world metres.
            if dynamic {
                let b = hero.visualBounds(relativeTo: nil)
                let maxDim = max(b.extents.x, max(b.extents.y, b.extents.z))
                let target = Float(max(station.hero.footprintM.w, 0.2))
                if maxDim > 0 { hero.scale = SIMD3(repeating: target / maxDim) }
            }
            hero.generateCollisionShapes(recursive: true)  // enable tap/grab mechanics
            hero.components.set(InputTargetComponent())
            hero.name = "hero:\(station.id)"
            holder.addChild(hero)

            // baked animation plays per the play_baked mechanic (default: on focus)
            let autoPlay = station.mechanics.contains {
                $0.type == "play_baked" && ($0.params["trigger"]?.stringValue ?? "on_focus") == "auto"
            }
            if station.hero.animation == "baked" && autoPlay { playBaked(hero) }
        } catch {
            print("SPATAIL: station \(station.id) hero load failed: \(error)")
        }

        // --- panels (spatial UI) ---
        for panel in station.panels {
            let entity = PanelFactory.make(panel)               // 2D card → 3D attachment
            entity.position = panelOffset(panel.anchor, footprint: station.hero.footprintM)
            if panel.billboard {
                // BillboardComponent auto-faces the camera (iOS 18+). On iOS 17 the
                // panel keeps the station's facing (already turned toward the user).
                if #available(iOS 18.0, *) {
                    entity.components.set(BillboardComponent())
                }
                billboardPanels.append(entity)
            }
            entity.isEnabled = (panel.reveal == "always")        // others reveal on focus/tap
            holder.addChild(entity)
            panelEntities[panel.id] = entity
        }
    }

    // MARK: - mechanics dispatch (called by the AR coordinator on tap)
    func handleTap(on entity: Entity) {
        guard let spec, let stationId = stationId(of: entity) else { return }
        guard let station = spec.stations.first(where: { $0.id == stationId }) else { return }
        for m in station.mechanics {
            switch m.type {
            case "play_baked":
                if (m.params["trigger"]?.stringValue ?? "on_focus") == "on_tap",
                   let hero = stationEntities[stationId]?.findEntity(named: "hero:\(stationId)") {
                    playBaked(hero)
                }
            case "tap_reveal":
                if let pid = m.params["reveals"]?.stringValue, let p = panelEntities[pid] {
                    p.isEnabled = true
                }
            case "grab_physics":
                if let hero = stationEntities[stationId]?.findEntity(named: "hero:\(stationId)") {
                    applyPhysics(hero, body: m.params["body"]?.stringValue ?? "rigid")
                }
            default: break   // quiz_panel handled via the panel UI layer
            }
        }
    }

    // MARK: - focus (guided presentation: one active station at a time)
    func focus(_ index: Int) {
        guard let spec else { return }
        focusedStation = index
        let stations = spec.orderedStations
        for (i, station) in stations.enumerated() {
            guard let holder = stationEntities[station.id] else { continue }
            let active = (i == index)
            // reveal on_focus panels for the active station; hide others' on_focus panels
            for panel in station.panels where panel.reveal == "on_focus" {
                panelEntities[panel.id]?.isEnabled = active
            }
            if active, station.hero.animation == "baked",
               let hero = holder.findEntity(named: "hero:\(station.id)") {
                playBaked(hero)
            }
        }
    }

    func next() { if let s = spec { focus(min(focusedStation + 1, s.stations.count - 1)) } }
    func prev() { focus(max(focusedStation - 1, 0)) }

    /// iOS 17 fallback for BillboardComponent: face each billboard panel toward the
    /// camera. Call from the AR session's per-frame update. No-op on iOS 18+ (the
    /// component handles it), and cheap when the list is empty.
    func faceBillboards(toward camera: SIMD3<Float>) {
        if #available(iOS 18.0, *) { return }
        for p in billboardPanels {
            let world = p.position(relativeTo: nil)
            let dir = camera - world
            guard length(dir) > 1e-4 else { continue }
            let yaw = atan2(dir.x, dir.z)
            p.setOrientation(simd_quatf(angle: yaw, axis: SIMD3(0, 1, 0)), relativeTo: nil)
        }
    }

    // MARK: - helpers
    private func playBaked(_ e: Entity) {
        func playAll(_ x: Entity) {
            for a in x.availableAnimations { x.playAnimation(a.repeat(), transitionDuration: 0.2) }
            for c in x.children { playAll(c) }
        }
        playAll(e)
    }

    private func applyPhysics(_ e: Entity, body: String) {
        guard let model = e as? ModelEntity ?? e.children.compactMap({ $0 as? ModelEntity }).first
        else { return }
        // map the spec's body class → PhysicsBody mode + material (captured RealityKit docs)
        let (friction, restitution): (Float, Float)
        switch body {
        case "bouncy": (friction, restitution) = (0.3, 0.8)
        case "heavy":  (friction, restitution) = (0.9, 0.1)
        case "soft":   (friction, restitution) = (0.8, 0.2)
        default:       (friction, restitution) = (0.7, 0.4)   // rigid
        }
        let mat = PhysicsMaterialResource.generate(friction: friction, restitution: restitution)
        model.components.set(PhysicsBodyComponent(massProperties: .default, material: mat, mode: .dynamic))
        model.components.set(CollisionComponent(shapes: [.generateBox(size: model.visualBounds(relativeTo: nil).extents)]))
    }

    private func panelOffset(_ anchor: String, footprint fp: ExperienceSpec.Footprint) -> SIMD3<Float> {
        let h = Float(fp.h), w = Float(fp.w)
        switch anchor {
        case "below_hero":   return SIMD3(0, -0.06, 0)
        case "beside_left":  return SIMD3(-(w/2 + 0.12), h/2, 0)
        case "beside_right": return SIMD3(w/2 + 0.12, h/2, 0)
        default:             return SIMD3(0, h + 0.10, 0)   // above_hero
        }
    }

    private func stationId(of entity: Entity) -> String? {
        var e: Entity? = entity
        while let cur = e {
            if cur.name.hasPrefix("hero:") { return String(cur.name.dropFirst(5)) }
            e = cur.parent
        }
        // fall back: which station holder contains this entity
        return stationEntities.first(where: { $0.value.findEntity(named: entity.name) != nil })?.key
    }

    func clear() {
        if let a = rootAnchor { view?.scene.removeAnchor(a) }
        rootAnchor = nil
        stationEntities.removeAll()
        panelEntities.removeAll()
        billboardPanels.removeAll()
        focusedStation = 0
        spec = nil
    }
}
#endif
