import SwiftUI
#if os(iOS)
import RealityKit
import ARKit
import Vision
import UIKit
import Combine
import simd

// LiveExplainerView — point the phone at a real object; SPATAIL identifies it from
// the LIVE camera feed, world-anchors a marker on it, then feeds a crop to the
// existing modular pipeline (Gemini -> Blender) and STREAMS the explanatory USDZ
// in OVERLAID on the real object, tracking it as the camera moves.
//
// Pipeline reuse (no server changes):
//   live ARFrame  -> Vision (VNClassifyImageRequest) -> {label, screen point}
//   screen point  -> ARKit raycast (LiDAR sceneDepth) -> world ARAnchor on the object
//   crop JPEG     -> GenerativeClient.generateModular(imageJPEG:question:)  (kind=image)
//                 -> returns contract + generationJobId (Blender build queued)
//   poll job      -> GenerativeClient.awaitGeneratedModel(jobId:) -> local USDZ
//   USDZ          -> AnchorEntity(.anchor: worldAnchor) -> overlays + tracks the object
//
// Honest scope (v1): the user FREEZES the target with a tap. We anchor at that
// instant against the LiDAR-fused world map, so the overlay tracks the static
// real object rock-solidly as the camera moves. Continuous re-tracking of a
// MOVING object is explicitly out of scope (see the verdict in the design notes).

// MARK: - View

struct LiveExplainerView: View {
    @StateObject private var vm = LiveExplainerModel()
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        ZStack(alignment: .bottom) {
            LiveExplainerARView(model: vm).ignoresSafeArea()

            // Live reticle + label while hunting; hidden once locked.
            if vm.phase == .scanning, let d = vm.liveDetection {
                DetectionReticle(label: d.label, confidence: d.confidence)
                    .position(d.screenPoint)
                    .allowsHitTesting(false)
            }

            VStack(spacing: 10) {
                if vm.phase == .building || vm.phase == .placing { ProgressView().tint(.white) }
                Text(vm.status)
                    .font(.caption).foregroundStyle(.white)
                    .multilineTextAlignment(.center)
                    .padding(8).background(.ultraThinMaterial, in: Capsule())

                controls
            }
            .padding()
        }
        .overlay(alignment: .topLeading) {
            Button { dismiss() } label: { Image(systemName: "xmark.circle.fill").font(.title) }
                .tint(.white).padding()
        }
        .onDisappear { vm.teardown() }
    }

    @ViewBuilder private var controls: some View {
        switch vm.phase {
        case .scanning:
            Button {
                vm.lockAndExplain()
            } label: {
                Label(vm.liveDetection == nil ? "Center an object" : "Explain “\(vm.liveDetection!.label)”",
                      systemImage: "scope")
                    .bold().padding(.horizontal, 6)
            }
            .buttonStyle(.borderedProminent)
            .disabled(vm.liveDetection == nil)
        case .placing, .building:
            Button(role: .cancel) { vm.cancelAndRescan() } label: {
                Label("Cancel", systemImage: "xmark")
            }.buttonStyle(.bordered).tint(.white)
        case .explaining:
            HStack(spacing: 12) {
                Button { vm.runtime?.next() } label: {
                    Label("Step", systemImage: "forward.frame")
                }.buttonStyle(.borderedProminent)
                Button(role: .cancel) { vm.cancelAndRescan() } label: {
                    Label("New object", systemImage: "arrow.counterclockwise")
                }.buttonStyle(.bordered).tint(.white)
            }
        }
    }
}

/// A small crosshair + label chip drawn at the detection's screen point.
private struct DetectionReticle: View {
    let label: String
    let confidence: Float
    var body: some View {
        VStack(spacing: 6) {
            Text("\(label)  \(Int(confidence * 100))%")
                .font(.caption2).bold().foregroundStyle(.black)
                .padding(.horizontal, 8).padding(.vertical, 3)
                .background(.yellow, in: Capsule())
            ZStack {
                Circle().stroke(.yellow, lineWidth: 2).frame(width: 64, height: 64)
                Circle().fill(.yellow).frame(width: 5, height: 5)
            }
        }
    }
}

// MARK: - View model (owns phase + status; AR host calls back into it)

@MainActor
final class LiveExplainerModel: ObservableObject {
    enum Phase { case scanning, placing, building, explaining }

    struct LiveDetection: Equatable {
        let label: String
        let confidence: Float
        let screenPoint: CGPoint   // in the AR view's coordinate space (points)
    }

    @Published var phase: Phase = .scanning
    @Published var status = "Point at an object and center it."
    @Published var liveDetection: LiveDetection?

    /// Set by the AR host so the view's Step button can drive beats.
    weak var runtime: ModularRuntime?
    /// Set by the AR host; the view model asks it to lock/cancel.
    weak var host: LiveExplainerARView.Coordinator?

    func lockAndExplain() {
        guard liveDetection != nil else { return }
        phase = .placing
        status = "Locking onto the object…"
        host?.lockCurrentDetection()
    }

    func cancelAndRescan() {
        host?.resetToScanning()
        phase = .scanning
        liveDetection = nil
        status = "Point at an object and center it."
    }

    func teardown() { host?.teardown() }
}

// MARK: - AR host (detection loop + raycast placement + streaming)

struct LiveExplainerARView: UIViewRepresentable {
    @ObservedObject var model: LiveExplainerModel

    func makeCoordinator() -> Coordinator { Coordinator(model: model) }

    func makeUIView(context: Context) -> ARView {
        let v = ARView(frame: .zero)
        let cfg = ARWorldTrackingConfiguration()
        cfg.planeDetection = [.horizontal, .vertical]   // helps fallback raycasts
        cfg.environmentTexturing = .automatic
        // LiDAR depth — gate on capability so non-LiDAR devices still run (raycast
        // then falls back to estimatedPlane). iPhone 14 Pro supports both.
        if ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth) {
            cfg.frameSemantics.insert(.sceneDepth)
        }
        if ARWorldTrackingConfiguration.supportsFrameSemantics(.smoothedSceneDepth) {
            cfg.frameSemantics.insert(.smoothedSceneDepth)
        }
        v.session.run(cfg)
        v.session.delegate = context.coordinator
        context.coordinator.view = v
        context.coordinator.runtime = ModularRuntime(view: v) { [weak model] s in
            Task { @MainActor in model?.status = s }
        }
        // hand the runtime + coordinator back to the view model
        model.runtime = context.coordinator.runtime
        model.host = context.coordinator
        return v
    }

    func updateUIView(_ v: ARView, context: Context) {}

    @MainActor
    final class Coordinator: NSObject, ARSessionDelegate {
        let model: LiveExplainerModel
        weak var view: ARView?
        var runtime: ModularRuntime?

        private let client = GenerativeClient()
        private let detector = LiveObjectDetector()

        // anchoring
        private var objectAnchor: ARAnchor?       // the world anchor on the real object
        private var anchorEntity: AnchorEntity?   // RealityKit anchor bound to it
        private var lockedScreenPoint: CGPoint?   // where the user locked (for the crop)

        // detection throttling
        private var lastVisionTime: TimeInterval = 0
        private var visionInFlight = false
        private var lockRequested = false

        init(model: LiveExplainerModel) { self.model = model; super.init() }

        // MARK: per-frame — run Vision (throttled, off the AR-delegate path)

        nonisolated func session(_ s: ARSession, didUpdate frame: ARFrame) {
            // ARKit delivers frames ~60fps off the main actor. Capture only value-y
            // things here (the buffer ref + a timestamp); read all view/actor state on
            // the main actor, then Vision runs on the detector's own queue.
            let now = CACurrentMediaTime()
            let pixelBuffer = frame.capturedImage

            Task { @MainActor in
                guard self.model.phase == .scanning else { return }
                guard !self.visionInFlight, now - self.lastVisionTime > 0.30 else { return }
                self.visionInFlight = true
                self.lastVisionTime = now
                let interfaceOrientation = self.currentImageOrientation()
                let displaySize = self.viewBoundsSize
                self.detector.classify(pixelBuffer: pixelBuffer,
                                       orientation: interfaceOrientation) { [weak self] result in
                    // detector calls back on its own queue; bounce to main
                    Task { @MainActor in
                        guard let self else { return }
                        self.visionInFlight = false
                        guard self.model.phase == .scanning else { return }
                        guard let r = result else { self.model.liveDetection = nil; return }
                        // Map Vision's normalized point (origin bottom-left) to view points.
                        let pt = Self.viewPoint(fromNormalized: r.normalizedPoint, in: displaySize)
                        self.model.liveDetection = .init(label: r.label,
                                                         confidence: r.confidence,
                                                         screenPoint: pt)
                        if self.lockRequested { self.lockRequested = false; self.performLock(at: pt) }
                    }
                }
            }
        }

        // MARK: lock + raycast placement

        /// Called by the view model on tap. We lock the LAST live detection's point.
        func lockCurrentDetection() {
            guard let d = model.liveDetection else { return }
            // Use the freshest detection point; if a Vision pass is mid-flight, set a
            // flag so the next result drives the lock (keeps the crop + point aligned).
            if visionInFlight { lockRequested = true; return }
            performLock(at: d.screenPoint)
        }

        private func performLock(at point: CGPoint) {
            guard let view else { return }
            lockedScreenPoint = point

            // 1) Raycast from the detection screen point into the world. On LiDAR we
            //    prefer existing/estimated geometry (the real object's surface). Try a
            //    precise mesh/plane hit first, then fall back to estimated planes.
            let anchor = raycastWorldAnchor(from: point, in: view)
            guard let worldAnchor = anchor else {
                model.status = "Couldn’t find the surface — move a little closer and retry."
                model.phase = .scanning
                return
            }
            objectAnchor = worldAnchor
            view.session.add(anchor: worldAnchor)

            // 2) Bind a RealityKit anchor to that ARAnchor so anything we attach
            //    overlays + tracks the real object as the camera moves.
            let ae = AnchorEntity(anchor: worldAnchor)
            anchorEntity = ae
            view.scene.addAnchor(ae)
            addPlacementMarker(to: ae)

            // 3) Kick the explain pipeline with a crop around the locked point.
            model.phase = .building
            model.status = "Looking at it…"
            explain(croppingAround: point, in: view)
        }

        /// Raycast strategy tuned for LiDAR: try the most precise target, widen if needed.
        private func raycastWorldAnchor(from point: CGPoint, in view: ARView) -> ARAnchor? {
            // (a) Precise: existing plane geometry (uses the LiDAR mesh classification).
            if let q = view.makeRaycastQuery(from: point, allowing: .existingPlaneGeometry, alignment: .any),
               let hit = view.session.raycast(q).first {
                return ARAnchor(transform: hit.worldTransform)
            }
            // (b) Estimated planes — works on arbitrary surfaces from depth, no detected plane needed.
            if let q = view.makeRaycastQuery(from: point, allowing: .estimatedPlane, alignment: .any),
               let hit = view.session.raycast(q).first {
                return ARAnchor(transform: hit.worldTransform)
            }
            // (c) Last resort: drop the anchor a fixed distance down the camera ray so
            //     we always anchor SOMETHING the user can see (better than failing).
            if let ray = view.ray(through: point) {
                let p = ray.origin + simd_normalize(ray.direction) * 0.5
                var t = matrix_identity_float4x4
                t.columns.3 = SIMD4<Float>(p.x, p.y, p.z, 1)
                return ARAnchor(transform: t)
            }
            return nil
        }

        /// A small ring + pin so the user sees the lock land on the real object even
        /// before Blender returns. Replaced/clad by the streamed USDZ.
        private func addPlacementMarker(to ae: AnchorEntity) {
            // Thin square disc (generateBox is iOS 13+; generateCylinder is iOS 18+).
            let marker = ModelEntity(
                mesh: .generateBox(size: SIMD3(0.1, 0.002, 0.1), cornerRadius: 0.05),
                materials: [UnlitMaterial(color: UIColor.systemTeal.withAlphaComponent(0.9))])
            marker.name = "spatail.live.marker"
            ae.addChild(marker)
        }

        // MARK: explain — crop -> modular pipeline -> stream USDZ onto the anchor

        private func explain(croppingAround point: CGPoint, in view: ARView) {
            guard let frame = view.session.currentFrame else {
                model.status = "Lost the camera frame — retry."; model.phase = .scanning; return
            }
            // Crop a square region of the live frame around the detection so Gemini
            // identifies THIS object (not the whole room). Falls back to full frame.
            guard let jpeg = LiveFrameCropper.croppedJPEG(from: frame.capturedImage,
                                                          aroundViewPoint: point,
                                                          viewSize: viewBoundsSize,
                                                          fraction: 0.5)
                ?? LiveFrameCropper.fullJPEG(from: frame.capturedImage)
            else {
                model.status = "Couldn’t read the camera image — retry."
                model.phase = .scanning; return
            }
            let question = model.liveDetection.map { "What is this \($0.label) and how does it work?" }
                ?? "What is this object and how does it work?"

            Task {
                do {
                    // Reuse the EXACT existing image path: Gemini brief -> Blender job.
                    let exp = try await client.generateModular(imageJPEG: jpeg, question: question)
                    // Present mechanics immediately on the object anchor (boxes/labels),
                    // anchored to the real object — not on a freshly detected plane.
                    presentOnAnchor(exp)
                    if let jid = exp.generationJobId, !jid.isEmpty {
                        model.phase = .building
                        model.status = "Building a real model in Blender… (a few minutes)"
                        await streamGenerated(jid, exp, in: view)
                    } else {
                        model.phase = .explaining
                        model.status = "Ready — tap Step."
                    }
                } catch {
                    model.status = "Couldn’t explain that: \(error.localizedDescription)"
                    model.phase = .scanning
                }
            }
        }

        /// Drive ModularRuntime, but RE-PARENT its scene anchor onto our world object
        /// anchor so all mechanics overlay + track the real object.
        private func presentOnAnchor(_ exp: ModularExperience) {
            guard let runtime, let host = anchorEntity else { return }
            runtime.present(exp, into: host)   // see ModularRuntime extension below
        }

        private func streamGenerated(_ jobId: String, _ exp: ModularExperience, in view: ARView) async {
            do {
                let url = try await client.awaitGeneratedModel(jobId: jobId) { [weak self] s in
                    Task { @MainActor in self?.model.status = "building: \(s)" }
                }
                // Hide the placeholder marker; the real model is the overlay now.
                anchorEntity?.children.first(where: { $0.name == "spatail.live.marker" })?
                    .removeFromParent()
                let target = exp.assets.first(where: { $0.role == "primary_object" })?.id
                          ?? exp.assets.first?.id ?? ""
                runtime?.streamLocalModel(url, into: target)
                model.phase = .explaining
                model.status = "Overlaid on the object — tap Step. Move around to inspect it."
            } catch {
                model.status = "Model build failed: \(error.localizedDescription)"
                model.phase = .explaining   // mechanics still usable without the baked model
            }
        }

        // MARK: reset / teardown

        func resetToScanning() {
            runtime?.clear()
            if let a = objectAnchor { view?.session.remove(anchor: a) }
            if let ae = anchorEntity { view?.scene.removeAnchor(ae) }
            objectAnchor = nil; anchorEntity = nil; lockedScreenPoint = nil
            lockRequested = false
        }

        func teardown() {
            resetToScanning()
            view?.session.pause()
        }

        // MARK: coordinate helpers (all main-actor; called from inside the @MainActor hop)

        /// View bounds size (main actor).
        private var viewBoundsSize: CGSize {
            view?.bounds.size ?? UIScreen.main.bounds.size
        }

        /// Map the live UI orientation -> the CGImagePropertyOrientation that makes the
        /// rear camera's native-landscape buffer read "up" for Vision. `.right` = portrait.
        private func currentImageOrientation() -> CGImagePropertyOrientation {
            let ui = (UIApplication.shared.connectedScenes
                        .compactMap { $0 as? UIWindowScene }.first?
                        .interfaceOrientation) ?? .portrait
            switch ui {
            case .landscapeLeft:  return .down
            case .landscapeRight: return .up
            case .portraitUpsideDown: return .left
            default:              return .right    // portrait
            }
        }

        /// Vision normalized point (origin bottom-left, y up) -> view points (origin top-left).
        nonisolated static func viewPoint(fromNormalized p: CGPoint, in size: CGSize) -> CGPoint {
            CGPoint(x: p.x * size.width, y: (1 - p.y) * size.height)
        }
    }
}

// MARK: - Vision detector (built-in classifier; swap-in point for a CoreML model)

/// Wraps Vision so the AR loop stays clean. v1 uses the BUILT-IN scene classifier
/// (VNClassifyImageRequest) — no bundled model, ships today, recognizes ~1000
/// everyday categories. For a real bounding box on arbitrary objects, drop in a
/// VNCoreMLRequest with a detector (e.g. YOLOv3/v8 .mlmodel) — see `classifyCoreML`.
final class LiveObjectDetector {
    struct Result { let label: String; let confidence: Float; let normalizedPoint: CGPoint }

    private let queue = DispatchQueue(label: "spatail.live.vision", qos: .userInitiated)

    func classify(pixelBuffer: CVPixelBuffer,
                  orientation: CGImagePropertyOrientation,
                  completion: @escaping (Result?) -> Void) {
        queue.async {
            let request = VNClassifyImageRequest()
            let handler = VNImageRequestHandler(cvPixelBuffer: pixelBuffer,
                                                orientation: orientation, options: [:])
            do {
                try handler.perform([request])
                // `results` is already [VNClassificationObservation]? — keep only
                // observations that clear Vision's recall/precision floor, take the best.
                guard let obs = request.results?
                    .filter({ $0.hasMinimumRecall(0.4, forPrecision: 0.3) })
                    .max(by: { $0.confidence < $1.confidence }),
                      obs.confidence > 0.25 else {
                    completion(nil); return
                }
                // VNClassifyImageRequest gives a whole-frame label (no box). Treat the
                // frame CENTER as the location — the user centers the object, and the
                // LiDAR raycast there finds its surface. A detector model would give a
                // real box; map its center the same way (see classifyCoreML).
                completion(Result(label: Self.pretty(obs.identifier),
                                  confidence: obs.confidence,
                                  normalizedPoint: CGPoint(x: 0.5, y: 0.5)))
            } catch {
                completion(nil)
            }
        }
    }

    // Drop-in upgrade for a real bounding box on arbitrary objects. Bundle a
    // detector .mlmodel (Create ML object detector, or a converted YOLO) and call
    // this instead of `classify`. Returns the highest-confidence box's label +
    // box-center as the normalized point.
    //
    //   func classifyCoreML(pixelBuffer:orientation:completion:) {
    //     let model = try VNCoreMLModel(for: MyDetector(configuration: .init()).model)
    //     let req = VNCoreMLRequest(model: model) { req, _ in
    //       guard let obs = (req.results as? [VNRecognizedObjectObservation])?
    //               .max(by: { $0.confidence < $1.confidence }) else { return completion(nil) }
    //       let bb = obs.boundingBox                 // normalized, origin bottom-left
    //       let center = CGPoint(x: bb.midX, y: bb.midY)
    //       let label = obs.labels.first?.identifier ?? "object"
    //       completion(Result(label: label, confidence: obs.confidence, normalizedPoint: center))
    //     }
    //     req.imageCropAndScaleOption = .scaleFill
    //     try? VNImageRequestHandler(cvPixelBuffer: pixelBuffer, orientation: orientation)
    //       .perform([req])
    //   }

    static func pretty(_ identifier: String) -> String {
        identifier.replacingOccurrences(of: "_", with: " ").capitalized
    }
}

// MARK: - Frame cropping (live ARFrame buffer -> JPEG for the modular pipeline)

enum LiveFrameCropper {
    private static let ctx = CIContext()

    /// Crop a square fraction of the frame centered on a VIEW point, return JPEG.
    /// The ARFrame buffer is camera-native landscape; we crop in image space using the
    /// view point's normalized position, then re-orient to portrait-up for Gemini.
    static func croppedJPEG(from pixelBuffer: CVPixelBuffer,
                            aroundViewPoint viewPoint: CGPoint,
                            viewSize: CGSize,
                            fraction: CGFloat) -> Data? {
        guard viewSize.width > 0, viewSize.height > 0 else { return nil }
        var ci = CIImage(cvPixelBuffer: pixelBuffer)
        // Re-orient the native-landscape buffer to portrait-up so the crop math and
        // the bytes Gemini sees are both upright (portrait UI is the common case).
        ci = ci.oriented(.right)
        let ext = ci.extent
        // Normalized position of the view point (0..1, top-left origin) maps directly
        // because we centered the object; clamp so the crop window stays in-bounds.
        let nx = min(max(viewPoint.x / viewSize.width, 0), 1)
        let ny = min(max(viewPoint.y / viewSize.height, 0), 1)
        let side = min(ext.width, ext.height) * fraction
        // CIImage origin is bottom-left; flip ny.
        let cx = ext.minX + nx * ext.width
        let cy = ext.minY + (1 - ny) * ext.height
        let rect = CGRect(x: cx - side / 2, y: cy - side / 2, width: side, height: side)
            .intersection(ext)
        let cropped = ci.cropped(to: rect)
        return jpeg(from: cropped, sourceExtent: rect)
    }

    static func fullJPEG(from pixelBuffer: CVPixelBuffer) -> Data? {
        let ci = CIImage(cvPixelBuffer: pixelBuffer).oriented(.right)
        return jpeg(from: ci, sourceExtent: ci.extent)
    }

    private static func jpeg(from ci: CIImage, sourceExtent: CGRect) -> Data? {
        guard let cg = ctx.createCGImage(ci, from: sourceExtent) else { return nil }
        return UIImage(cgImage: cg).jpegData(compressionQuality: 0.7)
    }
}
#else
// visionOS / non-iOS: Live Explainer (live camera + world raycast) is iPhone-only.
struct LiveExplainerView: View {
    var body: some View {
        Color.black.overlay(Text("Live Explainer — available on iPhone with LiDAR")
            .foregroundStyle(.white))
    }
}
#endif
