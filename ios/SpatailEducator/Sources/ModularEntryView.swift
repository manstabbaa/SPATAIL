import SwiftUI
#if os(iOS)
import RealityKit
import ARKit
import UIKit

// ModularEntryView — the on-phone entry to the v0.5 modular experience: type what to
// explain OR snap a photo, hit generate, and the agent-composed mechanics play in AR.
// Self-contained (its own AR host) so it can be presented from anywhere in the app.

struct ModularEntryView: View {
    @State private var text = ""
    @State private var status = "Type what to explain, or tap the camera."
    @State private var busy = false
    @State private var experience: ModularExperience?
    @State private var showCamera = false
    @Environment(\.dismiss) private var dismiss
    private let client = GenerativeClient()

    var body: some View {
        ZStack(alignment: .bottom) {
            ModularARView(experience: experience, onStatus: { status = $0 })
                .ignoresSafeArea()

            VStack(spacing: 8) {
                if busy { ProgressView().tint(.white) }
                Text(status)
                    .font(.caption).foregroundStyle(.white)
                    .padding(8).background(.ultraThinMaterial, in: Capsule())

                if let e = experience {
                    Text("\(e.title) · \(e.composer) · \(e.mechanicsUsed.count) mechanics — tap to step")
                        .font(.caption2).foregroundStyle(.white.opacity(0.8))
                }

                HStack(spacing: 10) {
                    TextField("how a lever works…", text: $text)
                        .textFieldStyle(.roundedBorder)
                        .submitLabel(.go)
                        .onSubmit { Task { await go(text: text) } }
                    Button { Task { await go(text: text) } } label: {
                        Image(systemName: "sparkles").font(.title2)
                    }.disabled(busy || text.trimmingCharacters(in: .whitespaces).isEmpty)
                    Button { showCamera = true } label: {
                        Image(systemName: "camera.fill").font(.title2)
                    }.disabled(busy)
                }
                .padding(10).background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 14))
            }
            .padding()
        }
        .overlay(alignment: .topLeading) {
            Button { dismiss() } label: { Image(systemName: "xmark.circle.fill").font(.title) }
                .tint(.white).padding()
        }
        .sheet(isPresented: $showCamera) {
            CameraPicker { img in Task { await go(image: img) } }
        }
    }

    private func go(text: String) async {
        let t = text.trimmingCharacters(in: .whitespaces)
        guard !t.isEmpty else { return }
        busy = true; status = "thinking…"
        do { experience = try await client.generateModular(text: t); status = "ready — tap to step" }
        catch { status = "error: \(error.localizedDescription)" }
        busy = false
    }

    private func go(image: UIImage) async {
        busy = true; status = "looking at your photo…"
        do {
            let data = image.jpegData(compressionQuality: 0.7) ?? Data()
            experience = try await client.generateModular(imageJPEG: data)
            status = "ready — tap to step"
        } catch { status = "error: \(error.localizedDescription)" }
        busy = false
    }
}

/// SwiftUI host for an ARView running a ModularRuntime; re-presents when the experience changes.
struct ModularARView: UIViewRepresentable {
    let experience: ModularExperience?
    let onStatus: (String) -> Void

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeUIView(context: Context) -> ARView {
        let v = ARView(frame: .zero)
        let cfg = ARWorldTrackingConfiguration()
        cfg.planeDetection = [.horizontal]
        v.session.run(cfg)
        context.coordinator.runtime = ModularRuntime(view: v, onStatus: onStatus)
        return v
    }

    func updateUIView(_ v: ARView, context: Context) {
        guard let e = experience, context.coordinator.presentedId != e.experienceId else { return }
        context.coordinator.presentedId = e.experienceId
        context.coordinator.runtime?.present(e)
    }

    @MainActor final class Coordinator {
        var runtime: ModularRuntime?
        var presentedId: String?
    }
}

/// Minimal camera capture for the photo path.
struct CameraPicker: UIViewControllerRepresentable {
    let onImage: (UIImage) -> Void
    @Environment(\.dismiss) private var dismiss

    func makeUIViewController(context: Context) -> UIImagePickerController {
        let p = UIImagePickerController()
        p.sourceType = UIImagePickerController.isSourceTypeAvailable(.camera) ? .camera : .photoLibrary
        p.delegate = context.coordinator
        return p
    }
    func updateUIViewController(_ c: UIImagePickerController, context: Context) {}
    func makeCoordinator() -> C { C(self) }

    final class C: NSObject, UINavigationControllerDelegate, UIImagePickerControllerDelegate {
        let parent: CameraPicker
        init(_ p: CameraPicker) { parent = p }
        func imagePickerController(_ picker: UIImagePickerController,
                                   didFinishPickingMediaWithInfo info: [UIImagePickerController.InfoKey: Any]) {
            if let img = (info[.editedImage] as? UIImage) ?? (info[.originalImage] as? UIImage) {
                parent.onImage(img)
            }
            parent.dismiss()
        }
        func imagePickerControllerDidCancel(_ picker: UIImagePickerController) { parent.dismiss() }
    }
}
#endif
