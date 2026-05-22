// PlayerView.swift
// Top-level ARView wrapper. Hosts the SceneController and routes user input.

#if os(iOS) || os(visionOS)
import SwiftUI
import RealityKit
import ARKit

public struct PlayerView: View {
    @State private var bundle: LoadedBundle?
    @State private var status: String = "Drop a .spatail to begin."

    public init() {}

    public var body: some View {
        ZStack(alignment: .bottom) {
            ARViewContainer(bundle: bundle)
                .ignoresSafeArea()
            VStack {
                Spacer()
                Text(status)
                    .padding(10)
                    .background(.ultraThinMaterial, in: Capsule())
                    .padding(.bottom, 24)
            }
        }
        .onOpenURL { url in
            do {
                let loaded = try BundleLoader.load(from: url)
                bundle = loaded
                status = "Loaded \(loaded.manifest.title)"
            } catch {
                status = "Load failed: \(error.localizedDescription)"
            }
        }
    }
}

private struct ARViewContainer: UIViewRepresentable {
    let bundle: LoadedBundle?

    func makeUIView(context: Context) -> ARView {
        let view = ARView(frame: .zero, cameraMode: .ar, automaticallyConfigureSession: true)
        let config = ARWorldTrackingConfiguration()
        config.planeDetection = [.horizontal, .vertical]
        view.session.run(config)
        return view
    }

    func updateUIView(_ view: ARView, context: Context) {
        guard let bundle else { return }
        Task { @MainActor in
            let controller = SceneController()
            do {
                let entity = try await controller.load(bundle)
                let anchor = AnchorEntity(world: .init(0, 0, -0.6))
                anchor.addChild(entity)
                view.scene.anchors.removeAll()
                view.scene.anchors.append(anchor)
            } catch {
                print("[PlayerView] scene load failed: \(error)")
            }
        }
    }
}
#endif
