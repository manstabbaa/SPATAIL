// ARExperienceCoordinator.swift
//
// UIViewRepresentable that hosts an ARView and wires:
//   - world-tracking config with horizontal + vertical plane detection
//   - coaching overlay (horizontal plane)
//   - tap-to-place: first tap drops the experience root anchor at the
//     raycast hit point
//   - ARSceneController as the SwiftUI-observable state bridge
//
// Why ARView (not RealityView): RealityView is iOS 18+. ARView via
// UIViewRepresentable runs on iOS 16+ which matches the deployment
// target. The renderer code (entities, materials, anchors) is the
// same either way — only the host changes.

import SwiftUI
import ARKit
import RealityKit
import Combine

/// Observable state owned by the SwiftUI ARExperienceContainerView.
/// Mutated by the AR side, read by the overlay.
final class ARSceneController: ObservableObject {
    @Published var isPlaced: Bool = false
    @Published var isExploded: Bool = false
    @Published var isHighlighted: Bool = false
    @Published var currentAttentionLine: AttentionLine?

    // Callbacks bridge UI buttons -> AR scene mutations. Set by the
    // coordinator on view creation.
    var onReset: () -> Void = {}
    var onAdvanceAttention: () -> Void = {}
    var onToggleExplode: () -> Void = {}
    var onToggleHighlight: () -> Void = {}

    func reset()              { onReset() }
    func advanceAttention()   { onAdvanceAttention() }
    func toggleExplode()      { onToggleExplode() }
    func toggleHighlight()    { onToggleHighlight() }
}

struct ARExperienceCoordinator: UIViewRepresentable {
    let contract: SpatialExperienceContract
    let controller: ARSceneController

    func makeCoordinator() -> Coordinator {
        Coordinator(contract: contract, controller: controller)
    }

    func makeUIView(context: Context) -> ARView {
        let arView = ARView(frame: .zero,
                            cameraMode: .ar,
                            automaticallyConfigureSession: false)
        arView.environment.lighting.intensityExponent = 0.5

        let config = ARWorldTrackingConfiguration()
        config.planeDetection = [.horizontal, .vertical]
        // SPATAIL_NEEDS_MAC_BUILD_VERIFY: scene-reconstruction option list
        // is `.mesh`, `.meshWithClassification` (LiDAR only). The static
        // `supportsSceneReconstruction(_:)` check gates the runtime call —
        // setting `.mesh` on a non-LiDAR device crashes at session.run.
        if ARWorldTrackingConfiguration.supportsSceneReconstruction(.mesh) {
            config.sceneReconstruction = .mesh
        }
        arView.session.run(config)

        // Coaching overlay — horizontal first; we'll graduate to vertical
        // if the user is in a room with wall surfaces.
        let coaching = ARCoachingOverlayView()
        coaching.session = arView.session
        coaching.goal = .horizontalPlane
        coaching.translatesAutoresizingMaskIntoConstraints = false
        arView.addSubview(coaching)
        NSLayoutConstraint.activate([
            coaching.topAnchor.constraint(equalTo: arView.topAnchor),
            coaching.bottomAnchor.constraint(equalTo: arView.bottomAnchor),
            coaching.leadingAnchor.constraint(equalTo: arView.leadingAnchor),
            coaching.trailingAnchor.constraint(equalTo: arView.trailingAnchor),
        ])

        let tap = UITapGestureRecognizer(target: context.coordinator,
                                         action: #selector(Coordinator.handleTap(_:)))
        arView.addGestureRecognizer(tap)

        context.coordinator.attach(arView: arView)
        return arView
    }

    func updateUIView(_ uiView: ARView, context: Context) {
        // No-op: state mutations flow through the Coordinator's callbacks.
    }

    // ----------------------------------------------------------------

    final class Coordinator: NSObject {
        let contract: SpatialExperienceContract
        let controller: ARSceneController
        private weak var arView: ARView?
        private var rootEntity: Entity?
        private var renderResult: ARSceneRenderer.RenderResult?
        private var attentionCursor: Int = 0

        init(contract: SpatialExperienceContract, controller: ARSceneController) {
            self.contract = contract
            self.controller = controller
            super.init()
            wireControllerCallbacks()
        }

        func attach(arView: ARView) {
            self.arView = arView
        }

        @objc func handleTap(_ gesture: UITapGestureRecognizer) {
            guard renderResult == nil, let arView else { return }
            let point = gesture.location(in: arView)
            guard let result = arView.raycast(
                from: point,
                allowing: .estimatedPlane,
                alignment: .horizontal,
            ).first else { return }

            let anchor = AnchorEntity(world: result.worldTransform)
            let renderer = ARSceneRenderer()
            let render = renderer.build(for: contract)
            anchor.addChild(render.root)
            arView.scene.addAnchor(anchor)

            self.rootEntity = render.root
            self.renderResult = render

            // Seed the attention pointer.
            attentionCursor = 0
            controller.currentAttentionLine = currentLine()
            controller.isPlaced = true
        }

        private func wireControllerCallbacks() {
            controller.onReset = { [weak self] in self?.reset() }
            controller.onAdvanceAttention = { [weak self] in self?.advance() }
            controller.onToggleExplode = { [weak self] in self?.toggleExplode() }
            controller.onToggleHighlight = { [weak self] in self?.toggleHighlight() }
        }

        // ---- Brick implementations ---------------------------------

        private func reset() {
            guard let render = renderResult else { return }
            for (_, e) in render.explodableTargets {
                e.collapse()
            }
            for entity in render.highlightables {
                entity.setHighlighted(false)
            }
            attentionCursor = 0
            controller.currentAttentionLine = currentLine()
            controller.isExploded = false
            controller.isHighlighted = false
        }

        private func advance() {
            attentionCursor = (attentionCursor + 1) % max(1, contract.attentionPlan.count)
            controller.currentAttentionLine = currentLine()
        }

        private func toggleExplode() {
            guard let render = renderResult else { return }
            controller.isExploded.toggle()
            for (_, entity) in render.explodableTargets {
                if controller.isExploded { entity.explode() } else { entity.collapse() }
            }
        }

        private func toggleHighlight() {
            guard let render = renderResult else { return }
            controller.isHighlighted.toggle()
            for entity in render.highlightables {
                entity.setHighlighted(controller.isHighlighted)
            }
        }

        private func currentLine() -> AttentionLine? {
            guard !contract.attentionPlan.isEmpty,
                  attentionCursor < contract.attentionPlan.count else { return nil }
            let step = contract.attentionPlan[attentionCursor]
            let titleById = Dictionary(uniqueKeysWithValues:
                contract.spatialElements.map { ($0.id, $0.title) })
            return AttentionLine(
                step: step.step,
                focusElementId: step.focusElementId,
                focusTitle: titleById[step.focusElementId] ?? step.focusElementId,
                narration: step.narration,
            )
        }
    }
}
