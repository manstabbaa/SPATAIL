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
        return view
    }

    func updateUIView(_ view: ARView, context: Context) {
        // place/replace the exhibit when the user has chosen a variant
        if model.stage == .placed, let entry = model.selected, let v = model.chosen,
           context.coordinator.placedId != entry.id + v.name {
            context.coordinator.place(url: Bundle.main.url(forResource: entry.usdzName,
                                                           withExtension: "usdz"),
                                      id: entry.id + v.name,
                                      scale: Float(v.scale))
        }
        // place a freshly generated USDZ downloaded from the PC
        if model.stage == .placed, let gen = model.generatedURL,
           context.coordinator.placedId != "gen:" + gen.lastPathComponent {
            context.coordinator.place(url: gen,
                                      id: "gen:" + gen.lastPathComponent,
                                      scale: 1.0, autoFit: true)
        }
        if model.stage == .choosing || model.stage == .prompting {
            context.coordinator.clear()
        }
    }

    func makeCoordinator() -> Coordinator { Coordinator(model: model) }

    final class Coordinator: NSObject, ARSessionDelegate {
        let model: SessionModel
        weak var view: ARView?
        var placedId: String?
        private var anchor: AnchorEntity?
        private var scanned = false
        private var maxFloorArea: Float = 0
        private var tableH: Float?

        init(model: SessionModel) { self.model = model }

        // --- room estimation from detected planes ---------------------------
        func session(_ s: ARSession, didUpdate anchors: [ARAnchor]) {
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
            if area > maxFloorArea { maxFloorArea = area }
            if let t = foundTable { tableH = t }
            // emit an updated profile (debounced to first solid read)
            if area > 0.5 && !scanned {
                scanned = true
                var r = RoomProfile()
                r.floorClearW = Double(max(1.0, floorW))
                r.floorClearD = Double(max(1.0, floorD))
                r.tablePresent = tableH != nil
                if let t = tableH { r.tableTopH = Double(t) }
                r.source = "arkit"
                Task { @MainActor in self.model.roomScanned(r) }
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

        func clear() {
            if let a = anchor { view?.scene.removeAnchor(a) }
            anchor = nil; placedId = nil
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
