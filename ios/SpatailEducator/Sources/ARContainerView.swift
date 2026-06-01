import SwiftUI
#if os(iOS)
import RealityKit
import ARKit
import Combine

// RealityKit + ARKit runtime. Scans planes to estimate the room (feeds SPATAIL
// ANALYSIS), then loads the chosen exhibit's USDZ, scales it to the chosen
// variant, anchors it, and plays the baked animation (the studio bakes physics
// into the USDZ timeline, so "play" reproduces correct motion for free).

struct ARContainerView: UIViewRepresentable {
    @ObservedObject var model: SessionModel

    func makeUIView(context: Context) -> ARView {
        let view = ARView(frame: .zero)
        let cfg = ARWorldTrackingConfiguration()
        cfg.planeDetection = [.horizontal]
        cfg.environmentTexturing = .automatic
        view.session.run(cfg)
        view.session.delegate = context.coordinator
        context.coordinator.view = view
        // tap → experience mechanics (tap_reveal / on_tap play / grab)
        let tap = UITapGestureRecognizer(target: context.coordinator,
                                         action: #selector(Coordinator.onTap(_:)))
        view.addGestureRecognizer(tap)
        return view
    }

    func updateUIView(_ view: ARView, context: Context) {
        let coord = context.coordinator
        // place/replace the exhibit when the user has chosen a variant
        if model.stage == .placed, let entry = model.selected, let v = model.chosen,
           coord.placedId != entry.id + v.name {
            coord.place(url: Bundle.main.url(forResource: entry.usdzName,
                                             withExtension: "usdz"),
                        id: entry.id + v.name,
                        scale: Float(v.scale))
        }
        // place a freshly generated USDZ downloaded from the PC
        if model.stage == .placed, let gen = model.generatedURL,
           coord.placedId != "gen:" + gen.lastPathComponent {
            coord.place(url: gen,
                        id: "gen:" + gen.lastPathComponent,
                        scale: 1.0, autoFit: true)
        }
        // present a downloaded multi-station EXPERIENCE (post-compile XR)
        if model.stage == .experiencing, let exp = model.experience,
           coord.presentedEpoch != model.experienceEpoch {
            coord.present(exp, epoch: model.experienceEpoch)
        }
        if model.stage == .choosing || model.stage == .prompting {
            coord.clear()
        }
    }

    func makeCoordinator() -> Coordinator { Coordinator(model: model) }

    @MainActor
    final class Coordinator: NSObject, ARSessionDelegate {
        let model: SessionModel
        weak var view: ARView?
        var placedId: String?
        private var anchor: AnchorEntity?
        private var scanned = false
        private var maxFloorArea: Float = 0
        private var tableH: Float?

        // post-compile XR: the experience interpreter + which epoch is presented
        private var runtime: ExperienceRuntime?
        var presentedEpoch: Int = -1

        init(model: SessionModel) { self.model = model }

        // MARK: - present a downloaded multi-station experience
        func present(_ exp: DownloadedExperience, epoch: Int) {
            guard let view else { return }
            presentedEpoch = epoch
            clear()
            let rt = ExperienceRuntime(view: view, usdzDir: exp.folder)
            runtime = rt
            // let the model drive station focus through the runtime
            model.onFocusStation = { [weak rt] i in rt?.focus(i) }
            Task { @MainActor in await rt.present(exp.spec) }
        }

        // route a tap to the runtime's mechanics
        @MainActor func handleTap(at point: CGPoint) {
            guard let view, let rt = runtime else { return }
            if let tapped = view.entity(at: point) {
                rt.handleTap(on: tapped)
            }
        }

        // per-frame: face iOS-17 billboard panels toward the camera.
        // nonisolated (ARKit calls off the main actor); hop to MainActor for state.
        nonisolated func session(_ s: ARSession, didUpdate frame: ARFrame) {
            let t = frame.camera.transform.columns.3
            let cam = SIMD3<Float>(t.x, t.y, t.z)
            Task { @MainActor in self.runtime?.faceBillboards(toward: cam) }
        }

        // --- room estimation from detected planes ---------------------------
        nonisolated func session(_ s: ARSession, didUpdate anchors: [ARAnchor]) {
            var floorW: Float = 0, floorD: Float = 0, area: Float = 0
            var foundTable: Float?
            for a in anchors.compactMap({ $0 as? ARPlaneAnchor }) {
                let ext = a.planeExtent
                let pa = ext.width * ext.height
                let y = a.transform.columns.3.y
                if a.alignment == .horizontal {
                    if y < 0.3 && pa > area {           // floor: lowest large plane
                        area = pa; floorW = ext.width; floorD = ext.height
                    } else if y > 0.5 && y < 1.1 {       // table: desk-height plane
                        foundTable = y
                    }
                }
            }
            // hand the per-frame reading to the main actor for all state mutation
            Task { @MainActor in self.ingestPlaneReading(area: area, floorW: floorW,
                                                         floorD: floorD, tableH: foundTable) }
        }

        // MainActor-isolated state update from a plane reading
        private func ingestPlaneReading(area: Float, floorW: Float, floorD: Float,
                                        tableH foundTable: Float?) {
            if area > maxFloorArea { maxFloorArea = area }
            if let t = foundTable { tableH = t }
            if area > 0.5 && !scanned {
                scanned = true
                var r = RoomProfile()
                r.floorClearW = Double(max(1.0, floorW))
                r.floorClearD = Double(max(1.0, floorD))
                r.tablePresent = tableH != nil
                if let t = tableH { r.tableTopH = Double(t) }
                r.source = "arkit"
                model.roomScanned(r)
            }
        }

        // --- place a USDZ (bundled exhibit OR downloaded generated file) -----
        func place(url: URL?, id: String, scale: Float, autoFit: Bool = false) {
            guard let view, let url else { return }
            clear()
            let anchor = AnchorEntity(plane: .horizontal, minimumBounds: [0.1, 0.1])
            self.anchor = anchor
            view.scene.addAnchor(anchor)
            placedId = id

            Task { @MainActor in
                do {
                    let model: Entity
                    if #available(iOS 18.0, *) {
                        model = try await Entity(contentsOf: url)
                    } else {
                        model = try Entity.load(contentsOf: url)
                    }
                    var s = scale
                    if autoFit {
                        // Generated content has unknown size — fit largest dim to ~0.4 m.
                        let b = model.visualBounds(relativeTo: nil)
                        let maxDim = max(b.extents.x, max(b.extents.y, b.extents.z))
                        if maxDim > 0 { s = 0.4 / maxDim }
                    }
                    model.scale = SIMD3<Float>(repeating: s)
                    model.position = SIMD3<Float>(0, 0, 0)
                    anchor.addChild(model)

                    // Play every baked clip found ANYWHERE in the hierarchy.
                    // USDZ transform-track ("xformOp.timeSamples") animation is
                    // surfaced on root AND child entities; play all of them.
                    var clips = 0
                    func playAll(_ e: Entity) {
                        for anim in e.availableAnimations {
                            e.playAnimation(anim.repeat(),
                                            transitionDuration: 0.2,
                                            startsPaused: false)
                            clips += 1
                        }
                        for child in e.children { playAll(child) }
                    }
                    playAll(model)
                    print("SPATAIL: \(id) — played \(clips) animation clip(s)")
                    if clips == 0 {
                        print("SPATAIL: WARNING no animations on \(id); model is static.")
                    }
                } catch {
                    print("SPATAIL: failed to load \(id): \(error)")
                }
            }
        }

        @objc func onTap(_ g: UITapGestureRecognizer) {
            guard let view else { return }
            let p = g.location(in: view)
            Task { @MainActor in self.handleTap(at: p) }
        }

        func clear() {
            if let a = anchor { view?.scene.removeAnchor(a) }
            anchor = nil; placedId = nil
            runtime?.clear()
            runtime = nil
            model.onFocusStation = nil
        }
    }
}
#else
// visionOS / other: AR container is a no-op placeholder; the RealityView-based
// volumetric player lands in the Vision Pro target. The flow + analysis above
// are shared.
import SwiftUI
struct ARContainerView: View {
    @ObservedObject var model: SessionModel
    var body: some View {
        Color.black.overlay(Text("Vision Pro player — coming in the XR build")
            .foregroundStyle(.white))
    }
}
#endif
