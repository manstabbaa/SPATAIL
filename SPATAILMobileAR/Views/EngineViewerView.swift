// EngineViewerView.swift
//
// ENGINE VIEWER — "see the room the way SPATAIL sees it."
//
// Live AR debug instrument: the whole room rendered as color-coded blockout
// slabs (one color per classified SurfaceKind, with kind + m² labels), so a
// human can immediately read what the camera/engine currently believes the
// space is made of. A legend pins the color code; the coverage arc shows how
// much of the room has been read.
//
// It is also the live end of the fusion loop: Connect streams ARKit camera
// frames to the PC engine (VLM label chip updates), Send Room streams the
// current scan up as a `room.update`, and the brain's `experience.delta`
// answer renders in-place through ARSceneRenderer (foam ghosts on the real
// table) with the shopping line in the decision card.
//
// SPATAIL_NEEDS_MAC_BUILD_VERIFY: ARKit + RealityKit view; authored against
// the ScannerARView / LiveVisionView patterns in this target.

import SwiftUI
import ARKit
import RealityKit
import Combine
import CoreImage

struct EngineViewerView: View {
    @StateObject private var scanner = RoomScannerService()
    @StateObject private var model = LiveVisionModel()
    @StateObject private var bridge = EngineViewerBridge()
    @State private var showLabels = true
    @State private var lastSendNote: String?

    var body: some View {
        ZStack {
            BlockoutARView(scanner: scanner, model: model,
                           bridge: bridge, showLabels: showLabels)
                .ignoresSafeArea()

            VStack(spacing: 10) {
                topBar
                legend
                Spacer()
                if let exp = model.lastExperience {
                    decisionCard(exp)
                }
                controls
            }
            .padding(14)
        }
        .preferredColorScheme(.dark)
        .navigationTitle("Engine Viewer")
        .navigationBarTitleDisplayMode(.inline)
        .onDisappear { model.stopAll() }
    }

    // MARK: top status — the two inputs, side by side

    private var topBar: some View {
        HStack(spacing: 8) {
            chip(icon: scanner.lidarAvailable ? "scanner.fill" : "rectangle.dashed",
                 text: scanner.lidarAvailable ? "LiDAR" : "planes")
            chip(icon: "square.stack.3d.up",
                 text: "\(scanner.liveSurfaces.count) surfaces · \(Int(scanner.coverage * 100))%")
            chip(icon: "eye",
                 text: model.lastIdentification?.primary ?? "—")
            Spacer()
            Toggle(isOn: $showLabels) { Image(systemName: "textformat") }
                .toggleStyle(.button)
                .tint(.spatailAccent)
        }
    }

    private func chip(icon: String, text: String) -> some View {
        HStack(spacing: 5) {
            Image(systemName: icon).imageScale(.small)
            Text(text).font(.caption).fontWeight(.semibold).lineLimit(1)
        }
        .padding(.horizontal, 10).padding(.vertical, 6)
        .background(Color.black.opacity(0.5))
        .foregroundColor(.white)
        .clipShape(Capsule())
    }

    // MARK: legend — the color code, same table the AR slabs use

    private var legend: some View {
        HStack(spacing: 8) {
            ForEach(SurfaceKind.allCases.filter { $0 != .unknown }, id: \.self) { kind in
                let area = scanner.areaByKind[kind] ?? 0
                if area >= 0.05 || [.floor, .wall, .table].contains(kind) {
                    HStack(spacing: 4) {
                        RoundedRectangle(cornerRadius: 3)
                            .fill(Color(uiColor: RoomBlockoutBuilder.color(for: kind)))
                            .frame(width: 12, height: 12)
                        Text(kind.rawValue)
                            .font(.system(.caption2, design: .monospaced))
                        if area >= 0.05 {
                            Text(String(format: "%.1f", area))
                                .font(.system(.caption2, design: .monospaced))
                                .foregroundColor(.white.opacity(0.6))
                        }
                    }
                }
            }
            Spacer()
        }
        .padding(.horizontal, 10).padding(.vertical, 6)
        .background(Color.black.opacity(0.5))
        .foregroundColor(.white)
        .clipShape(RoundedRectangle(cornerRadius: 10))
    }

    // MARK: the brain's decision

    private func decisionCard(_ exp: SpatialExperienceContract) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("BRAIN DECIDED · v\(model.lastExperienceVersion)")
                .font(.system(.caption2, design: .monospaced))
                .foregroundColor(.spatailTextDim)
            Text(exp.reasoningSummary.isEmpty ? exp.title : exp.reasoningSummary)
                .font(.callout).fontWeight(.semibold)
                .foregroundColor(.white)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.black.opacity(0.55))
        .clipShape(RoundedRectangle(cornerRadius: 14))
    }

    // MARK: controls — connect + send room

    private var controls: some View {
        VStack(spacing: 8) {
            if let note = lastSendNote {
                Text(note)
                    .font(.caption2)
                    .foregroundColor(.spatailTextDim)
            }
            HStack(spacing: 8) {
                TextField("pc-ip:8798", text: $model.serverHost)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .keyboardType(.URL)
                    .font(.system(.caption, design: .monospaced))
                    .padding(.horizontal, 12).padding(.vertical, 8)
                    .background(Color.black.opacity(0.5))
                    .foregroundColor(.white)
                    .clipShape(Capsule())
                    .disabled(model.state == .streaming)

                Button(model.state == .streaming ? "Disconnect" : "Connect") {
                    if model.state == .streaming { model.disconnect() }
                    else { model.connect() }
                }
                .font(.caption).fontWeight(.bold)
                .padding(.horizontal, 14).padding(.vertical, 8)
                .background(Color.spatailAccent)
                .foregroundColor(.white)
                .clipShape(Capsule())

                Button("Send Room") { sendRoom() }
                    .font(.caption).fontWeight(.bold)
                    .padding(.horizontal, 14).padding(.vertical, 8)
                    .background(model.state == .streaming
                                ? Color.spatailAccent2 : Color.gray.opacity(0.5))
                    .foregroundColor(.white)
                    .clipShape(Capsule())
                    .disabled(model.state != .streaming)
            }
        }
    }

    private func sendRoom() {
        let room = scanner.snapshotContract()
        var pos: [Float]? = nil
        var fwd: [Float]? = nil
        if let cam = bridge.arView?.session.currentFrame?.camera {
            let t = cam.transform
            pos = [t.columns.3.x, t.columns.3.y, t.columns.3.z]
            fwd = [-t.columns.2.x, -t.columns.2.y, -t.columns.2.z]
        }
        model.sendRoom(room, posePosition: pos, poseForward: fwd)
        lastSendNote = "sent \(room.surfaces.count) surfaces → brain"
    }
}

// MARK: - Bridge (lets SwiftUI buttons reach the live ARView)

@MainActor
final class EngineViewerBridge: ObservableObject {
    weak var arView: ARView?
}

// MARK: - AR view: scan + blockout + brain-decision rendering + frame uplink

private struct BlockoutARView: UIViewRepresentable {
    let scanner: RoomScannerService
    let model: LiveVisionModel
    let bridge: EngineViewerBridge
    let showLabels: Bool

    func makeUIView(context: Context) -> ARView {
        let arView = ARView(frame: .zero, cameraMode: .ar,
                            automaticallyConfigureSession: false)
        scanner.attach(session: arView.session)
        arView.session.run(scanner.configuration())
        bridge.arView = arView
        context.coordinator.attach(arView: arView, showLabels: showLabels)
        return arView
    }

    func updateUIView(_ uiView: ARView, context: Context) {
        context.coordinator.showLabels = showLabels
    }

    func makeCoordinator() -> Coordinator {
        Coordinator(scanner: scanner, model: model)
    }

    // Timer teardown lives here, not in deinit: the Coordinator is @MainActor
    // and Swift 6 forbids touching its non-Sendable Timer from a nonisolated
    // deinit. dismantleUIView is guaranteed main-actor by UIViewRepresentable.
    static func dismantleUIView(_ uiView: ARView, coordinator: Coordinator) {
        coordinator.detach()
    }

    @MainActor
    final class Coordinator {
        private let scanner: RoomScannerService
        private let model: LiveVisionModel
        private weak var arView: ARView?
        private var blockoutAnchor: AnchorEntity?
        private var decisionAnchor: AnchorEntity?
        private var cancellables: Set<AnyCancellable> = []
        private var frameTimer: Timer?
        private var lastSignature = ""
        private let ciContext = CIContext(options: [.useSoftwareRenderer: false])
        var showLabels = true

        init(scanner: RoomScannerService, model: LiveVisionModel) {
            self.scanner = scanner
            self.model = model
        }

        func attach(arView: ARView, showLabels: Bool) {
            self.arView = arView
            self.showLabels = showLabels

            let blockout = AnchorEntity(world: SIMD3<Float>(0, 0, 0))
            arView.scene.addAnchor(blockout)
            blockoutAnchor = blockout

            let decision = AnchorEntity(world: SIMD3<Float>(0, 0, 0))
            arView.scene.addAnchor(decision)
            decisionAnchor = decision

            // Rebuild the blockout when the classified surfaces change
            // materially. Throttled: hull extraction runs per anchor update,
            // but regenerating meshes + text every frame would stutter.
            scanner.$liveSurfaces
                .throttle(for: .milliseconds(600), scheduler: RunLoop.main,
                          latest: true)
                .sink { [weak self] surfaces in
                    self?.rebuildBlockout(surfaces)
                }
                .store(in: &cancellables)

            // Render the brain's decision whenever a new contract lands.
            model.$lastExperience
                .compactMap { $0 }
                .sink { [weak self] exp in
                    self?.rebuildDecision(exp)
                }
                .store(in: &cancellables)

            // ARKit frame uplink (~2.5 fps): the Engine Viewer owns the camera
            // through ARKit, so frames for the VLM come from the AR session,
            // not the AVCapture streamer LiveVisionView uses. Encoding happens
            // on the main actor — a 640 px JPEG at 2.5 fps is fine for a dev
            // instrument.
            frameTimer = Timer.scheduledTimer(withTimeInterval: 0.4,
                                              repeats: true) { [weak self] _ in
                Task { @MainActor [weak self] in self?.pushFrame() }
            }
        }

        func detach() {
            frameTimer?.invalidate()
            frameTimer = nil
            cancellables.removeAll()
        }

        private func rebuildBlockout(_ surfaces: [RoomSurface]) {
            // Cheap change signature: count + kinds + total area to 0.1 m².
            let total = surfaces.reduce(Float(0)) { $0 + $1.area }
            let kinds = Set(surfaces.map(\.kind.rawValue)).sorted().joined()
            let signature = "\(surfaces.count)|\(kinds)|\(Int(total * 10))|\(showLabels)"
            guard signature != lastSignature, let anchor = blockoutAnchor else { return }
            lastSignature = signature

            anchor.children.removeAll()
            let root = RoomBlockoutBuilder().build(surfaces: surfaces,
                                                   labels: showLabels)
            anchor.addChild(root)
        }

        private func rebuildDecision(_ exp: SpatialExperienceContract) {
            guard let anchor = decisionAnchor else { return }
            anchor.children.removeAll()
            let result = ARSceneRenderer().build(for: exp)
            anchor.addChild(result.root)
        }

        private func pushFrame() {
            guard let sender = model.sender,
                  let frame = arView?.session.currentFrame else { return }
            var image = CIImage(cvPixelBuffer: frame.capturedImage).oriented(.right)
            let longEdge = max(image.extent.width, image.extent.height)
            if longEdge > 640 {
                let s = 640 / longEdge
                image = image.transformed(by: CGAffineTransform(scaleX: s, y: s))
            }
            let opts: [CIImageRepresentationOption: Any] =
                [kCGImageDestinationLossyCompressionQuality as CIImageRepresentationOption: 0.5]
            guard let data = ciContext.jpegRepresentation(
                of: image, colorSpace: CGColorSpaceCreateDeviceRGB(),
                options: opts) else { return }
            sender.send(data)
        }
    }
}
