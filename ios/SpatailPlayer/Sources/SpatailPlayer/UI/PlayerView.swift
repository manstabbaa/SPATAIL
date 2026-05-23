// PlayerView.swift
// Top-level ARView wrapper. Hosts the SceneController and routes user input.
//
// v1 offers three ways to load a bundle:
//   1. "Load demo" button         — uses the .spatail shipped as a SwiftPM
//                                    resource. One tap, no Files / AirDrop.
//   2. "Open from Files…" button  — picks any .spatail the user has on disk
//                                    via SwiftUI's `.fileImporter`.
//   3. .onOpenURL                  — triggered when the user taps a .spatail
//                                    in Files / Mail / AirDrop sheet. Only
//                                    works once the app's Info.plist declares
//                                    the com.spatail.experience UTI.

#if os(iOS) || os(visionOS)
import SwiftUI
import RealityKit
import ARKit
import UniformTypeIdentifiers

public struct PlayerView: View {
    @State private var bundle: LoadedBundle?
    @State private var status: String = "Tap Load demo, or Open from Files."
    @State private var showFilePicker = false
    @State private var isLoading = false

    /// Demo bundles available inside the app binary. Filenames must match
    /// `Resources/<name>.spatail` and be declared in Package.swift resources.
    public static let demoBundleNames: [String] = ["f1_wheel_buttons"]

    public init() {}

    public var body: some View {
        ZStack(alignment: .bottom) {
            ARViewContainer(bundle: bundle)
                .ignoresSafeArea()

            VStack(spacing: 12) {
                Spacer()

                Text(status)
                    .font(.callout.weight(.medium))
                    .padding(.horizontal, 14)
                    .padding(.vertical, 8)
                    .background(.ultraThinMaterial, in: Capsule())
                    .foregroundStyle(.primary)

                HStack(spacing: 10) {
                    Button {
                        Task { await loadDemo() }
                    } label: {
                        Label("Load demo", systemImage: "play.fill")
                            .padding(.horizontal, 14)
                            .padding(.vertical, 10)
                            .background(.ultraThickMaterial, in: Capsule())
                    }
                    .disabled(isLoading)

                    Button {
                        showFilePicker = true
                    } label: {
                        Label("Open from Files…", systemImage: "folder")
                            .padding(.horizontal, 14)
                            .padding(.vertical, 10)
                            .background(.ultraThickMaterial, in: Capsule())
                    }
                    .disabled(isLoading)
                }
                .padding(.bottom, 28)
            }
        }
        .fileImporter(
            isPresented: $showFilePicker,
            allowedContentTypes: [.spatailExperience, .zip, .data],
            allowsMultipleSelection: false
        ) { result in
            switch result {
            case .success(let urls):
                if let url = urls.first {
                    Task { await load(from: url, label: url.lastPathComponent) }
                }
            case .failure(let err):
                status = "Pick failed: \(err.localizedDescription)"
            }
        }
        .onOpenURL { url in
            Task { await load(from: url, label: url.lastPathComponent) }
        }
    }

    // MARK: - Loaders

    private func loadDemo() async {
        guard let name = Self.demoBundleNames.first else {
            status = "No demo bundles compiled into the app."
            return
        }
        guard let url = Bundle.module.url(
            forResource: name, withExtension: "spatail"
        ) else {
            status = "Demo bundle \(name).spatail missing from app resources."
            return
        }
        await load(from: url, label: "demo: \(name)")
    }

    private func load(from url: URL, label: String) async {
        isLoading = true
        status = "Loading \(label)…"
        defer { isLoading = false }

        do {
            // Run the unzip + decode off the main actor.
            let loaded = try await Task.detached(priority: .userInitiated) {
                try BundleLoader.load(from: url)
            }.value
            bundle = loaded
            status = "Loaded: \(loaded.manifest.title)"
        } catch {
            status = "Load failed: \(error.localizedDescription)"
        }
    }
}

// MARK: - UTI

extension UTType {
    /// Matches the UTI we'll register in the app's Info.plist for `.spatail`.
    /// (`UTType("com.spatail.experience")` returns nil until the UTI is declared,
    /// which is why we also include `.zip` and `.data` in the fileImporter.)
    static var spatailExperience: UTType {
        UTType("com.spatail.experience") ?? .zip
    }
}

// MARK: - ARView container

private struct ARViewContainer: UIViewRepresentable {
    let bundle: LoadedBundle?

    func makeUIView(context: Context) -> ARView {
        let view = ARView(frame: .zero, cameraMode: .ar,
                           automaticallyConfigureSession: true)
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
                print("[PlayerView][SPATAIL-DIAG] post-attach " +
                      "entity.scale=\(entity.scale) " +
                      "entity.transformMatrix(world)=\(entity.transformMatrix(relativeTo: nil)) " +
                      "anchor.scale=\(anchor.scale) " +
                      "view.cameraMode=\(String(describing: view.cameraMode)) " +
                      "sessionRunning=\(view.session.currentFrame != nil)")
            } catch {
                print("[PlayerView] scene load failed: \(error)")
            }
        }
    }
}
#endif
