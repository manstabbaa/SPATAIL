// FusionHUDView.swift
//
// The on-device developer instrument for the fusion brain — "see what SPATAIL
// is reading and understanding." It puts the whole input→brain→output loop on
// one screen, registered in the real world:
//
//   INPUT  (context)   the VLM's noun for what the camera is looking at
//   INPUT  (geometry)  the scanned mesh the brain reasons over (shown as the
//                      ARKit scene-understanding overlay)
//   OUTPUT (decision)  the brain's SpatialExperienceContract, rendered as the
//                      foam-ghost edge strips + corner guards on the real
//                      surface, plus the "how much" shopping line.
//
// The contract is injected (the live session streams it from the PC brain, or
// a bundled sample is passed in for offline inspection). visionLabel is the
// latest VLM primary (from VisionUplink) so the two inputs sit side by side.
//
// SPATAIL_NEEDS_MAC_BUILD_VERIFY: RealityKit/ARKit view — authored against the
// existing ScannerARView pattern; compile + run on a LiDAR device.

import SwiftUI
import ARKit
import RealityKit

struct FusionHUDView: View {
    let contract: SpatialExperienceContract
    var visionLabel: String?

    @StateObject private var scanner = RoomScannerService()

    private var edgeCount: Int {
        contract.spatialElements.filter { $0.placementKindEnum == .surface_edge }.count
    }
    private var cornerCount: Int {
        contract.spatialElements.filter { $0.placementKindEnum == .surface_corner }.count
    }
    private var totalStrips: Int {
        contract.spatialElements
            .filter { $0.placementKindEnum == .surface_edge }
            .reduce(0) { $0 + ($1.placement.count ?? 0) }
    }

    var body: some View {
        ZStack {
            FusionARView(scanner: scanner, contract: contract)
                .ignoresSafeArea()

            VStack {
                inputBar
                Spacer()
                decisionCard
            }
            .padding(14)
        }
        .preferredColorScheme(.dark)
        .navigationTitle("Fusion HUD")
        .navigationBarTitleDisplayMode(.inline)
    }

    // MARK: the two fused inputs, side by side

    private var inputBar: some View {
        HStack(spacing: 10) {
            chip(system: "eye", label: "VLM", value: visionLabel ?? "—")
            chip(system: scanner.lidarAvailable ? "scanner.fill" : "rectangle.dashed",
                 label: "SCAN",
                 value: scanner.lidarAvailable ? "LiDAR mesh" : "planes")
            Spacer()
        }
    }

    private func chip(system: String, label: String, value: String) -> some View {
        HStack(spacing: 6) {
            Image(systemName: system).imageScale(.small)
            Text(label)
                .font(.system(.caption2, design: .monospaced))
                .foregroundColor(.spatailTextDim)
            Text(value)
                .font(.caption).fontWeight(.semibold)
                .foregroundColor(.white)
                .lineLimit(1)
        }
        .padding(.horizontal, 10).padding(.vertical, 6)
        .background(Color.black.opacity(0.45))
        .clipShape(Capsule())
    }

    // MARK: what SPATAIL decided

    private var decisionCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("SPATAIL DECIDED")
                .font(.system(.caption2, design: .monospaced))
                .foregroundColor(.spatailTextDim)

            Text(contract.reasoningSummary.isEmpty ? contract.title : contract.reasoningSummary)
                .font(.callout).fontWeight(.semibold)
                .foregroundColor(.white)
                .fixedSize(horizontal: false, vertical: true)

            HStack(spacing: 14) {
                metric("\(totalStrips)", "strips")
                metric("\(edgeCount)", "edges")
                metric("\(cornerCount)", "corners")
            }
            .padding(.top, 2)
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.black.opacity(0.5))
        .clipShape(RoundedRectangle(cornerRadius: 16))
    }

    private func metric(_ value: String, _ caption: String) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(value)
                .font(.system(.title3, design: .rounded)).bold()
                .foregroundColor(.spatailAccent)
            Text(caption)
                .font(.system(.caption2, design: .monospaced))
                .foregroundColor(.white.opacity(0.7))
        }
    }
}

// MARK: - AR session view: live scan mesh + the rendered contract

private struct FusionARView: UIViewRepresentable {
    let scanner: RoomScannerService
    let contract: SpatialExperienceContract

    func makeUIView(context: Context) -> ARView {
        let arView = ARView(frame: .zero, cameraMode: .ar,
                            automaticallyConfigureSession: false)
        // Reuse the real scanner so the HUD shows the same classified mesh the
        // brain reasons over (the "geometry" input).
        scanner.attach(session: arView.session)
        arView.session.run(scanner.configuration())

        // Draw the scene-understanding mesh so "what SPATAIL reads" is visible.
        arView.debugOptions.insert(.showSceneUnderstanding)

        // Render the brain's decision. Contract positions are in the room/world
        // frame the scan established, so anchor the root at world origin.
        let result = ARSceneRenderer().build(for: contract)
        let anchor = AnchorEntity(world: SIMD3<Float>(0, 0, 0))
        anchor.addChild(result.root)
        arView.scene.addAnchor(anchor)

        return arView
    }

    func updateUIView(_ uiView: ARView, context: Context) {}
}
