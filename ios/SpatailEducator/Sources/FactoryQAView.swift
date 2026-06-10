import SwiftUI
#if os(iOS)
import RealityKit
import ARKit
import UIKit
import Combine
import simd

// FactoryQAView — the on-device QA inspector for a normalized AI Asset Factory
// asset. Unlike the other runtimes (which refit unknown-size models to a target),
// this places the USDZ at its EXACT normalized scale, because the factory already
// guaranteed the size. It draws the configured TARGET-bounds wireframe cage + a
// ground grid so you can see at a glance: did it import, is it scaled, is it
// centered, does it fit the bounds — and runs SPATAIL Analysis for room-fit
// variants (real size vs tabletop diorama).

struct FactoryQAView: View {
    let asset: FactoryAsset
    @Environment(\.dismiss) private var dismiss

    @State private var localURL: URL?
    @State private var status = "Downloading normalized USDZ…"
    @State private var failed: String?
    @State private var room = RoomProfile()
    @State private var scaleName = "real"     // "real" | "tabletop"
    @State private var turntable = true
    private let client = FactoryClient()

    private var variants: [ScaleVariant] {
        SpatailAnalysis.variants(footprintW: asset.finalBounds.x,
                                 depth: asset.finalBounds.y,
                                 height: asset.finalBounds.z, room: room)
    }
    private var activeScale: Float {
        Float(variants.first(where: { $0.name == scaleName })?.scale ?? 1.0)
    }

    var body: some View {
        ZStack(alignment: .bottom) {
            FactoryARView(asset: asset, localURL: localURL, variantScale: activeScale,
                          turntable: turntable, onRoom: { room = $0 },
                          onStatus: { status = $0 })
                .ignoresSafeArea()

            VStack(spacing: 10) {
                if failed == nil && localURL == nil { downloading }
                readout
                controls
            }
            .padding()
        }
        .overlay(alignment: .topLeading) {
            Button { dismiss() } label: { Image(systemName: "xmark.circle.fill").font(.title) }
                .tint(.white).padding()
        }
        .task { await download() }
    }

    private var downloading: some View {
        HStack(spacing: 8) { ProgressView().tint(.white); Text(status).font(.caption).foregroundStyle(.white) }
            .padding(8).background(.ultraThinMaterial, in: Capsule())
    }

    private var readout: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(asset.title).font(.headline)
                Spacer()
                Image(systemName: asset.fitsTarget ? "checkmark.seal.fill" : "exclamationmark.triangle.fill")
                    .foregroundStyle(asset.fitsTarget ? .green : .orange)
            }
            Text(String(format: "Normalized  W %.2f · D %.2f · H %.2f m  (longest %.2f)",
                        asset.finalBounds.x, asset.finalBounds.y, asset.finalBounds.z,
                        asset.finalBounds.longest))
                .font(.caption2)
            Text(String(format: "Target volume  %.2f × %.2f × %.2f m   ·   %@",
                        asset.targetBounds.x, asset.targetBounds.y, asset.targetBounds.z,
                        originLabel))
                .font(.caption2).foregroundStyle(.secondary)
            Text("\(asset.triangleCount) triangles · \(asset.boundsMode ?? "—") · unit \(asset.unitSystem)")
                .font(.caption2).foregroundStyle(.secondary)
            if let f = failed { Text(f).font(.caption2).foregroundStyle(.red) }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(10).background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 12))
    }

    private var controls: some View {
        HStack(spacing: 10) {
            Picker("Scale", selection: $scaleName) {
                ForEach(variants) { v in Text(scaleLabel(v)).tag(v.name) }
            }.pickerStyle(.segmented)
            Button { turntable.toggle() } label: {
                Image(systemName: turntable ? "rotate.3d" : "pause.circle")
            }.buttonStyle(.bordered).tint(.white)
        }
        .padding(10).background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 12))
    }

    private var originLabel: String {
        switch asset.originMode {
        case "bottom_center": return "bottom-center"
        case "center": return "centered"
        default: return asset.originMode ?? "—"
        }
    }
    private func scaleLabel(_ v: ScaleVariant) -> String {
        v.name == "real" ? "Real size" : String(format: "Tabletop ×%.2f", v.scale)
    }

    private func download() async {
        guard asset.hasUsdz else { failed = "This asset has no USDZ — re-run the factory with --export-usdz."; return }
        do { localURL = try await client.downloadUSDZ(asset); status = "tap a surface to move it" }
        catch { failed = error.localizedDescription }
    }
}

// MARK: - AR host

struct FactoryARView: UIViewRepresentable {
    let asset: FactoryAsset
    let localURL: URL?
    let variantScale: Float
    let turntable: Bool
    let onRoom: (RoomProfile) -> Void
    let onStatus: (String) -> Void

    func makeCoordinator() -> Coordinator { Coordinator(onRoom: onRoom) }

    func makeUIView(context: Context) -> ARView {
        let v = ARView(frame: .zero)
        let cfg = ARWorldTrackingConfiguration()
        cfg.planeDetection = [.horizontal]
        cfg.environmentTexturing = .automatic
        v.session.run(cfg)
        v.session.delegate = context.coordinator
        let rt = FactoryRuntime(view: v, onStatus: onStatus)
        context.coordinator.runtime = rt
        let tap = UITapGestureRecognizer(target: context.coordinator,
                                         action: #selector(Coordinator.onTap(_:)))
        v.addGestureRecognizer(tap)
        return v
    }

    func updateUIView(_ v: ARView, context: Context) {
        let coord = context.coordinator
        let key = "\(asset.id)|\(variantScale)"
        if let url = localURL, coord.placedKey != key {
            coord.placedKey = key
            coord.runtime?.present(asset: asset, url: url, scale: variantScale)
        }
        if coord.turntable != turntable {
            coord.turntable = turntable
            coord.runtime?.setTurntable(turntable)
        }
    }

    static func dismantleUIView(_ v: ARView, coordinator: Coordinator) {
        coordinator.runtime?.clear()
        v.session.pause()
    }

    @MainActor final class Coordinator: NSObject, ARSessionDelegate {
        var runtime: FactoryRuntime?
        var placedKey: String?
        var turntable = true
        private let onRoom: (RoomProfile) -> Void
        private var maxFloorArea: Float = 0
        private var tableH: Float?
        private var lastPush: TimeInterval = 0

        init(onRoom: @escaping (RoomProfile) -> Void) { self.onRoom = onRoom }

        @objc func onTap(_ g: UITapGestureRecognizer) {
            guard let v = g.view as? ARView else { return }
            runtime?.reposition(at: g.location(in: v))
        }

        // Light room estimate (mirrors ARContainerView) → feeds SPATAIL Analysis.
        nonisolated func session(_ s: ARSession, didUpdate anchors: [ARAnchor]) {
            var area: Float = 0, w: Float = 0, d: Float = 0
            var foundTable: Float?
            for a in anchors.compactMap({ $0 as? ARPlaneAnchor }) where a.alignment == .horizontal {
                let ext = a.planeExtent
                let pa = ext.width * ext.height
                let y = a.transform.columns.3.y
                if y < 0.3 && pa > area { area = pa; w = ext.width; d = ext.height }
                else if y > 0.5 && y < 1.1 { foundTable = y }
            }
            Task { @MainActor in self.ingest(area: area, w: w, d: d, tableH: foundTable) }
        }

        private func ingest(area: Float, w: Float, d: Float, tableH t: Float?) {
            if area > maxFloorArea { maxFloorArea = area }
            if let t { tableH = t }
            let now = CACurrentMediaTime()
            guard area > 0.3, now - lastPush > 1.0 else { return }
            lastPush = now
            var r = RoomProfile()
            r.floorClearW = Double(max(1.0, w)); r.floorClearD = Double(max(1.0, d))
            r.tablePresent = tableH != nil
            if let h = tableH { r.tableTopH = Double(h) }
            r.source = "arkit"
            onRoom(r)
        }
    }
}

// MARK: - placement runtime (true scale + target cage + grid; never refit)

@MainActor
final class FactoryRuntime {
    private weak var view: ARView?
    private let onStatus: (String) -> Void
    private var anchor: AnchorEntity?
    private var stage: Entity?
    private var modelEntity: Entity?
    private var turntableOn = true
    private var spin: Float = 0
    private var sub: Cancellable?
    private var current: (asset: FactoryAsset, url: URL, scale: Float)?

    init(view: ARView, onStatus: @escaping (String) -> Void = { _ in }) {
        self.view = view; self.onStatus = onStatus
    }

    func present(asset: FactoryAsset, url: URL, scale: Float, at point: CGPoint? = nil) {
        guard let view else { return }
        current = (asset, url, scale)
        clearScene()
        let a = AnchorEntity(world: Self.placement(in: view, at: point))
        anchor = a
        view.scene.addAnchor(a)

        let stage = Entity()
        a.addChild(stage)
        self.stage = stage
        stage.scale = SIMD3(repeating: max(scale, 0.01))

        // ground grid + TARGET-bounds wireframe cage (Blender x,y,z = w,d,h → RK x,y,z = w,h,d)
        let cage = SIMD3(Float(asset.targetBounds.x), Float(asset.targetBounds.z), Float(asset.targetBounds.y))
        stage.addChild(makeGrid(span: max(cage.x, cage.z) * 1.5))
        stage.addChild(makeWireBox(size: cage, color: UIColor.systemTeal.withAlphaComponent(0.9)))

        Task { await self.load(asset: asset, url: url, into: stage) }
        if sub == nil {
            sub = view.scene.subscribe(to: SceneEvents.Update.self) { [weak self] _ in self?.tick() }
        }
        onStatus("tap a surface to move it")
    }

    func reposition(at point: CGPoint) {
        guard let c = current else { return }
        // Rebuild the diorama at the tapped surface (robust vs. mutating a live
        // AnchorEntity's transform; the USDZ is local, so the reload is instant).
        present(asset: c.asset, url: c.url, scale: c.scale, at: point)
    }

    func setTurntable(_ on: Bool) { turntableOn = on }

    private func load(asset: FactoryAsset, url: URL, into stage: Entity) async {
        do {
            let entity: Entity
            if #available(iOS 18.0, *) { entity = try await Entity(contentsOf: url) }
            else { entity = try Entity.load(contentsOf: url) }
            // The asset is ALREADY normalized to real metres — do NOT refit. Just
            // seat it: rest its base on the grid (y=0) and centre it in x/z.
            let b = entity.visualBounds(relativeTo: nil)
            entity.position = SIMD3(-b.center.x, -(b.center.y - b.extents.y / 2), -b.center.z)
            entity.generateCollisionShapes(recursive: true)
            stage.addChild(entity)
            modelEntity = entity
            for anim in entity.availableAnimations {
                entity.playAnimation(anim.repeat(), transitionDuration: 0.2)
            }
            let placed = b.extents
            onStatus(String(format: "placed at real size — %.2f × %.2f × %.2f m",
                            placed.x, placed.y, placed.z))
        } catch {
            onStatus("failed to load model: \(error.localizedDescription)")
        }
    }

    private func tick() {
        guard turntableOn, let m = modelEntity else { return }
        spin += 0.006
        m.orientation = simd_quatf(angle: spin, axis: SIMD3(0, 1, 0))
    }

    // place at the screen-centre (or tapped) horizontal plane; fall back in front of camera
    private static func placement(in view: ARView, at point: CGPoint? = nil) -> simd_float4x4 {
        let cam = view.cameraTransform
        let pt = point ?? CGPoint(x: view.bounds.midX, y: view.bounds.midY)
        var origin: SIMD3<Float>
        if let hit = view.raycast(from: pt, allowing: .estimatedPlane, alignment: .horizontal).first {
            let c = hit.worldTransform.columns.3; origin = SIMD3(c.x, c.y, c.z)
        } else {
            let f = -SIMD3(cam.matrix.columns.2.x, 0, cam.matrix.columns.2.z)
            let fwd = simd_length(f) > 1e-4 ? simd_normalize(f) : SIMD3(0, 0, -1)
            origin = cam.translation + fwd * 0.8; origin.y = cam.translation.y - 0.6
        }
        let toCam = cam.translation - origin
        return Transform(scale: .one,
                         rotation: simd_quatf(angle: atan2(toCam.x, toCam.z), axis: SIMD3(0, 1, 0)),
                         translation: origin).matrix
    }

    private func makeGrid(span: Float) -> ModelEntity {
        let s = max(span, 0.3)
        let e = ModelEntity(mesh: .generatePlane(width: s, depth: s, cornerRadius: 0.01),
                            materials: [UnlitMaterial(color: UIColor.white.withAlphaComponent(0.12))])
        e.position.y = 0.001
        return e
    }

    /// A 1:1 wireframe box of the target volume, sitting on the grid (12 thin bars).
    private func makeWireBox(size: SIMD3<Float>, color: UIColor) -> Entity {
        let t: Float = 0.005
        let (X, Y, Z) = (max(size.x, 0.02), max(size.y, 0.02), max(size.z, 0.02))
        let mat = UnlitMaterial(color: color)
        let box = Entity()
        func bar(_ s: SIMD3<Float>, _ p: SIMD3<Float>) {
            let m = ModelEntity(mesh: .generateBox(size: SIMD3(max(s.x, 0.001), max(s.y, 0.001), max(s.z, 0.001))),
                                materials: [mat])
            m.position = p; box.addChild(m)
        }
        for sx in [-X / 2, X / 2] { for sz in [-Z / 2, Z / 2] { bar(SIMD3(t, Y, t), SIMD3(sx, Y / 2, sz)) } }
        for sz in [-Z / 2, Z / 2] { for yy in [Float(0), Y] { bar(SIMD3(X, t, t), SIMD3(0, yy, sz)) } }
        for sx in [-X / 2, X / 2] { for yy in [Float(0), Y] { bar(SIMD3(t, t, Z), SIMD3(sx, yy, 0)) } }
        return box
    }

    private func clearScene() {
        if let a = anchor, let view { view.scene.removeAnchor(a) }
        anchor = nil; stage = nil; modelEntity = nil; spin = 0
    }

    func clear() {
        sub = nil
        clearScene()
        current = nil
    }
}
#endif
