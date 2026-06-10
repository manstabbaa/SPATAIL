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
    @State private var streamModel: StreamPayload? = nil
    @State private var showCamera = false
    // The two streams of a spatial experience: overlay the REAL object in front of
    // you (object tracking) vs place the experience in your space (tabletop).
    @State private var trackObject = false
    @Environment(\.dismiss) private var dismiss
    private let client = GenerativeClient()

    var body: some View {
        ZStack(alignment: .bottom) {
            ModularARView(experience: experience, streamModel: streamModel, onStatus: { status = $0 })
                .ignoresSafeArea()

            VStack(spacing: 8) {
                if busy { ProgressView().tint(.white) }
                Text(status)
                    .font(.caption).foregroundStyle(.white)
                    .padding(8).background(.ultraThinMaterial, in: Capsule())

                if let e = experience {
                    Text("\(e.title) · \(e.composer) · \(e.sequence.count) steps — tap to step")
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

                // Stream choice: tracked overlay vs placed-in-space.
                Toggle(isOn: $trackObject) {
                    Label("Overlay the real object (object tracking)", systemImage: "viewfinder")
                        .font(.caption).foregroundStyle(.white)
                }
                .toggleStyle(.switch).tint(.teal)
                .padding(.horizontal, 12).padding(.vertical, 6)
                .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 14))
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

    /// Tracked stream: find a served .referenceobject whose subjects match the prompt.
    private func matchTrackable(_ prompt: String) async -> GenerativeClient.Trackable? {
        guard trackObject else { return nil }
        let words = Set(prompt.lowercased().split(separator: " ").map(String.init))
        let all = (try? await client.fetchTrackables()) ?? []
        return all.first { t in
            t.subjects.contains { subj in
                let sw = Set(subj.lowercased().split(separator: " ").map(String.init))
                return !sw.isDisjoint(with: words)
            }
        }
    }

    private func go(text: String) async {
        let t = text.trimmingCharacters(in: .whitespaces)
        guard !t.isEmpty else { return }
        busy = true; status = "thinking…"
        do {
            let trackable = await matchTrackable(t)
            if trackObject && trackable == nil {
                status = "no trained tracker matches yet — overlay will pin near the object"
            }
            experience = try await client.generateModular(
                text: t, tracked: trackObject,
                trackedObjectId: trackable?.id ?? "",
                referenceObjectUrl: trackable?.url ?? "")
            status = "ready — tap to step"
        } catch { status = "error: \(error.localizedDescription)" }
        busy = false
    }

    private func go(image: UIImage) async {
        busy = true; status = "looking at your photo…"
        do {
            let data = image.jpegData(compressionQuality: 0.7) ?? Data()
            let q = text.trimmingCharacters(in: .whitespaces)
            let trackable = await matchTrackable(q.isEmpty ? "object" : q)
            let exp = try await client.generateModular(
                imageJPEG: data, question: q, tracked: trackObject,
                trackedObjectId: trackable?.id ?? "",
                referenceObjectUrl: trackable?.url ?? "")
            experience = exp
            busy = false
            if let jid = exp.generationJobId, !jid.isEmpty {
                status = "Blender is building a real model… (a few minutes)"
                Task { await streamGenerated(jid, exp) }
            } else {
                status = "ready — tap to step"
            }
        } catch { status = "error: \(error.localizedDescription)"; busy = false }
    }

    /// Poll the queued Blender build and stream the real model in over the placeholder.
    private func streamGenerated(_ jobId: String, _ exp: ModularExperience) async {
        do {
            let url = try await client.awaitGeneratedModel(jobId: jobId) { s in
                status = "building: \(s)"
            }
            let target = exp.assets.first(where: { $0.role == "primary_object" })?.id
                      ?? exp.assets.first?.id ?? ""
            streamModel = StreamPayload(assetId: target, url: url)
            status = "real model ready — tap to step"
        } catch { status = "model build failed: \(error.localizedDescription)" }
    }
}

/// A Blender-generated model (local USDZ) to stream into a specific asset's holder.
struct StreamPayload: Equatable { let assetId: String; let url: URL }

/// SwiftUI host for an ARView running a ModularRuntime; re-presents when the experience changes.
struct ModularARView: UIViewRepresentable {
    let experience: ModularExperience?
    var streamModel: StreamPayload? = nil
    let onStatus: (String) -> Void

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeUIView(context: Context) -> ARView {
        let v = ARView(frame: .zero)
        let cfg = ARWorldTrackingConfiguration()
        // horizontal + vertical so the RoomModelBuilder can classify floor/table/wall
        cfg.planeDetection = [.horizontal, .vertical]
        cfg.environmentTexturing = .automatic
        v.session.run(cfg)
        v.session.delegate = context.coordinator         // feeds the RoomModel
        context.coordinator.runtime = ModularRuntime(view: v, onStatus: onStatus)
        return v
    }

    func updateUIView(_ v: ARView, context: Context) {
        if let e = experience, context.coordinator.presentedId != e.experienceId {
            context.coordinator.presentedId = e.experienceId
            context.coordinator.runtime?.present(e)
        }
        if let sm = streamModel, context.coordinator.streamedPath != sm.url.path {
            context.coordinator.streamedPath = sm.url.path
            context.coordinator.runtime?.streamLocalModel(sm.url, into: sm.assetId)
        }
    }

    @MainActor final class Coordinator: NSObject, ARSessionDelegate {
        var runtime: ModularRuntime?
        var presentedId: String?
        var streamedPath: String?
        private let roomBuilder = RoomModelBuilder()
        private var lastRoomPush: TimeInterval = 0

        // ARKit callbacks arrive off the main actor; hop to MainActor for state.
        // Planes feed the RoomModel; ARObjectAnchors (the TRACKED stream, iOS 27
        // object tracking) forward to the runtime's ObjectAnchoringController.
        nonisolated func session(_ s: ARSession, didAdd anchors: [ARAnchor]) {
            let planes = anchors.compactMap { $0 as? ARPlaneAnchor }
            let objects = anchors.compactMap { $0 as? ARObjectAnchor }
            if planes.isEmpty && objects.isEmpty { return }
            Task { @MainActor in
                if !planes.isEmpty { self.roomBuilder.update(planes: planes) }
                if !objects.isEmpty { self.runtime?.objectAnchoring.objectAnchorsAdded(objects) }
            }
        }
        nonisolated func session(_ s: ARSession, didUpdate anchors: [ARAnchor]) {
            let planes = anchors.compactMap { $0 as? ARPlaneAnchor }
            let objects = anchors.compactMap { $0 as? ARObjectAnchor }
            if planes.isEmpty && objects.isEmpty { return }
            Task { @MainActor in
                if !planes.isEmpty { self.roomBuilder.update(planes: planes) }
                if !objects.isEmpty { self.runtime?.objectAnchoring.objectAnchorsUpdated(objects) }
            }
        }
        nonisolated func session(_ s: ARSession, didRemove anchors: [ARAnchor]) {
            let objects = anchors.compactMap { $0 as? ARObjectAnchor }
            if objects.isEmpty { return }
            Task { @MainActor in self.runtime?.objectAnchoring.objectAnchorsRemoved(objects) }
        }
        nonisolated func session(_ s: ARSession, didUpdate frame: ARFrame) {
            let cam = frame.camera.transform
            let light = frame.lightEstimate
            Task { @MainActor in
                self.roomBuilder.update(cameraTransform: cam)
                self.roomBuilder.update(light: light)
                let now = CACurrentMediaTime()
                if now - self.lastRoomPush > 1.0 {            // refresh RoomModel ~1 Hz
                    self.lastRoomPush = now
                    self.runtime?.roomModel = self.roomBuilder.build()
                }
            }
        }
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
#else
// visionOS / non-iOS: the modular entry (camera + ARView host) is iPhone-only.
// Provide a placeholder so shared ContentView's `.fullScreenCover { ModularEntryView() }`
// resolves on every target (mirrors ARContainerView's #else stub).
struct ModularEntryView: View {
    var body: some View {
        Color.black.overlay(Text("Modular mode — available on iPhone")
            .foregroundStyle(.white))
    }
}
#endif
