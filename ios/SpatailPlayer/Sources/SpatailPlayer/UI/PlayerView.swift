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
            ARViewContainer(bundle: bundle, status: $status)
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
//
// AR Quick Look-equivalent placement: horizontal plane detection, gesture
// recognizers for drag / pinch / rotate, environment-texturing IBL.
// Pattern follows Apple's RealityKit guidance:
//   - https://developer.apple.com/documentation/realitykit/arview/installgestures(_:for:)
//   - https://developer.apple.com/documentation/realitykit/anchorentity/init(plane:classification:minimumbounds:)
// The loaded USDZ is wrapped in a ModelEntity so gestures attach to a
// single HasCollision parent. Scale is applied on the wrapper (not the raw
// entity) because USDZ animation tracks can override transforms written
// directly to `Entity(contentsOf:)`'s root.

private struct ARViewContainer: UIViewRepresentable {
    let bundle: LoadedBundle?
    @Binding var status: String

    func makeCoordinator() -> Coordinator { Coordinator() }
    final class Coordinator {
        var loadedExperienceId: String? = nil
    }

    func makeUIView(context: Context) -> ARView {
        let view = ARView(frame: .zero)
        let config = ARWorldTrackingConfiguration()
        config.planeDetection = [.horizontal]
        config.environmentTexturing = .automatic
        view.session.run(config)
        return view
    }

    func updateUIView(_ view: ARView, context: Context) {
        guard let bundle else { return }
        let expId = bundle.manifest.experienceId
        guard context.coordinator.loadedExperienceId != expId else { return }
        context.coordinator.loadedExperienceId = expId

        Task { @MainActor in
            do {
                let controller = SceneController()
                let entity = try await controller.load(bundle)

                let wrapper = ModelEntity()
                wrapper.addChild(entity)

                // Compute scale from the entity's ACTUAL bounds, not from
                // manifest.scene.boundingBoxMeters — the Blender export
                // pipeline writes those values in source units (often cm)
                // while labeling them as meters. RealityKit consumes the
                // USDZ at its declared metersPerUnit, so visualBounds is
                // the ground truth.
                let localBounds = entity.visualBounds(relativeTo: wrapper)
                let largestExtent = Swift.max(localBounds.extents.x,
                                              Swift.max(localBounds.extents.y,
                                                        localBounds.extents.z))
                let targetSize = SceneController.tabletopTargetSizeMeters
                let initialScale: Float = (largestExtent > 0.0001)
                    ? targetSize / largestExtent
                    : 1.0
                wrapper.scale = SIMD3<Float>(repeating: initialScale)

                // Collision shape must be sized in wrapper-LOCAL units (pre-scale),
                // because RealityKit applies wrapper.scale to the shape at render.
                let collisionShape = ShapeResource.generateBox(
                    size: max(localBounds.extents, SIMD3<Float>(repeating: 0.01)))
                wrapper.components.set(CollisionComponent(shapes: [collisionShape]))
                entity.generateCollisionShapes(recursive: true)

                let anchor = AnchorEntity(plane: .horizontal,
                                          classification: .any,
                                          minimumBounds: [0.1, 0.1])
                anchor.addChild(wrapper)
                view.scene.anchors.removeAll()
                view.scene.anchors.append(anchor)

                view.installGestures([.translation, .rotation, .scale],
                                     for: wrapper)

                status = "Aim at a flat surface — \(bundle.manifest.title) will land on it. Pinch / drag / rotate to adjust."

                print("[ARViewContainer][SPATAIL-DIAG] anchored exp='\(expId)' " +
                      "rawExtents=\(localBounds.extents) " +
                      "manifestBbox=\(bundle.manifest.scene.boundingBoxMeters) " +
                      "initialScale=\(initialScale) " +
                      "finalSizeMeters=\(localBounds.extents * initialScale)")
            } catch {
                status = "Load failed: \(error.localizedDescription)"
                print("[ARViewContainer] load failed: \(error)")
            }
        }
    }
}

/// Element-wise max for clamping near-zero bounding boxes.
private func max(_ a: SIMD3<Float>, _ b: SIMD3<Float>) -> SIMD3<Float> {
    SIMD3<Float>(Swift.max(a.x, b.x), Swift.max(a.y, b.y), Swift.max(a.z, b.z))
}
#endif
