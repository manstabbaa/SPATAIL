// LensView.swift — the product home: the live camera IS the app.
//
// Full-screen ARView (from ARSessionHub) with quiet, honest instruments over it:
//   • floating object chips over identified registry objects (label + confidence);
//     tap = ask about THIS object — the chip pre-scopes the AskBar to
//     {objectId, part?} so the generated experience targets the real thing.
//     Long-press lists the object's known parts ("Ask about the cap").
//   • a subtle scanning-state pill: room coverage %, LiDAR badge, uplink link dot —
//     states mirror exactly what the services publish (spec §0 law 3).
//   • the Truth Overlay (one toggle away, RootView's eye button) layered between
//     the camera and the chips.
//
// Chip positions are projected from each object's OBB on a 12 Hz frame consumer —
// geometry rides ARKit at full rate underneath; the chips are just labels and do
// not need 60 Hz. Everything visual uses the design-system tokens.

import SwiftUI
import ARKit
import RealityKit

// MARK: - LensView

struct LensView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var hub: ARSessionHub
    @EnvironmentObject private var scanner: RoomScannerService
    @EnvironmentObject private var registry: ObjectRegistry
    @EnvironmentObject private var uplink: VisionUplink
    @StateObject private var chips = LensChipsModel()
    @ObservedObject private var askScope = AskScope.shared

    var body: some View {
        ZStack {
            ARViewContainer(arView: hub.arView)

            if model.showTruthOverlay {
                TruthOverlayView()
            }

            chipsLayer

            VStack(spacing: 0) {
                scanPill
                    // RootView's status/toolbar row sits at the safe-area top;
                    // the pill tucks in just under it. (RootView applies
                    // .ignoresSafeArea() to the whole Lens, so the inset is
                    // re-derived from the window.)
                    .padding(.top, WindowChrome.topInset + 56)
                Spacer()
            }
        }
        .onAppear {
            // Deferred: attach wires publishers that can emit synchronously —
            // never mutate observed state inside the update transaction.
            DispatchQueue.main.async {
                chips.attach(hub: hub, registry: registry)
                print("[UI] Lens observing scanner \(scanner.tag)")
            }
        }
        .onChange(of: scanner.surfaces.count) { _, n in
            print("[UI] pill sees \(n) surfaces (scanner \(scanner.tag))")
        }
        .onDisappear { chips.detach() }
    }

    // MARK: Object chips

    private var chipsLayer: some View {
        ForEach(chips.chips) { chip in
            ObjectChip(chip: chip) { part in
                askScope.target(objectId: chip.id, label: chip.label, part: part)
            }
            .position(chip.point)
        }
    }

    // MARK: Scanning-state pill (honest states only)

    private var scanPill: some View {
        HStack(spacing: SpatailSpace.s2) {
            Image(systemName: "viewfinder")
                .imageScale(.small)
                .foregroundStyle(SpatailColor.paper)
            // Surface count leads: the coverage ratio (mapped area over the
            // whole revealed room-box) honestly reads 0% early in a big space,
            // which looks broken while 13 surfaces exist (field report
            // 2026-07-03). "What has it found" beats "how much is left".
            Text(scanner.surfaces.isEmpty
                 ? "Scanning…"
                 : "\(scanner.surfaces.count) surfaces · \(Int(scanner.coveragePercent.rounded()))%")
                .spatailType(.micro, weight: .medium)
                .foregroundStyle(SpatailColor.paper)

            if scanner.usingLiDAR {
                Text("LiDAR")
                    .spatailType(.micro, weight: .medium)
                    .foregroundStyle(SpatailColor.indigo300)
            }

            Circle()                             // uplink truth dot — never lies
                .fill(linkDot)
                .frame(width: 6, height: 6)
        }
        .padding(.horizontal, SpatailSpace.s3)
        .padding(.vertical, SpatailSpace.s1 + 2)
        .spatailGlass(tone: .dark, radius: SpatailRadius.pill)
    }

    private var linkDot: Color {
        switch uplink.state {
        case .idle: return SpatailColor.textFaint
        case .connecting: return SpatailColor.statusWarning
        case .streaming: return SpatailColor.statusSuccess
        case .failed: return SpatailColor.statusDanger
        }
    }
}

// MARK: - Object chip (glass, tappable, part-aware)

private struct ObjectChip: View {
    let chip: LensChipsModel.Chip
    /// Called with nil for "the whole object", or a part label.
    let onAsk: (String?) -> Void

    var body: some View {
        HStack(spacing: SpatailSpace.s1) {
            Text(chip.label)
                .spatailType(.micro, weight: .medium)
                .foregroundStyle(SpatailColor.paper)
                .lineLimit(1)
            Text("\(Int(chip.confidence * 100))%")
                .spatailType(.micro)
                .foregroundStyle(SpatailColor.indigo300)
        }
        .padding(.horizontal, SpatailSpace.s3)
        .padding(.vertical, SpatailSpace.s1 + 2)
        .spatailGlass(tone: .dark, radius: SpatailRadius.pill)
        .onTapGesture { onAsk(nil) }
        .contextMenu {
            Button { onAsk(nil) } label: {
                Label("Ask about the \(chip.label)", systemImage: "sparkles")
            }
            ForEach(chip.parts, id: \.self) { part in
                Button { onAsk(part) } label: {
                    Label("Ask about the \(part)", systemImage: "scope")
                }
            }
        }
        .fixedSize()
    }
}

// MARK: - Chip projection model (12 Hz, main — projection only, no work)

@MainActor
final class LensChipsModel: ObservableObject {

    struct Chip: Identifiable, Equatable {
        let id: UUID
        var label: String
        var confidence: Float
        var point: CGPoint
        var parts: [String]
    }

    @Published private(set) var chips: [Chip] = []

    private weak var hub: ARSessionHub?
    private weak var registry: ObjectRegistry?
    private var attached = false
    private static let consumerId = "lens.chips"

    func attach(hub: ARSessionHub, registry: ObjectRegistry) {
        guard !attached else { return }
        attached = true
        self.hub = hub
        self.registry = registry
        hub.addFrameConsumer(id: Self.consumerId, hz: 12) { [weak self] _ in
            self?.tick()
        }
    }

    func detach() {
        hub?.removeFrameConsumer(id: Self.consumerId)
        attached = false
        chips = []
    }

    private func tick() {
        guard let hub, let registry else { return }
        let arView = hub.arView
        let bounds = arView.bounds
        guard bounds.width > 1, bounds.height > 1 else {
            if !chips.isEmpty { chips = [] }
            return
        }

        var next: [Chip] = []
        for object in registry.objects {
            guard let label = object.label else { continue }   // chips = identified only
            let top = object.obb.center
                + SIMD3<Float>(0, object.obb.extents.y / 2 + 0.03, 0)
            guard let p = arView.project(top) else { continue } // nil = behind camera
            guard p.x > -60, p.x < bounds.width + 60,
                  p.y > -40, p.y < bounds.height + 40 else { continue }
            next.append(Chip(id: object.id,
                             label: label,
                             confidence: object.confidence,
                             point: p,
                             parts: object.parts.map(\.label)))
        }
        if next != chips { chips = next }
    }
}

// MARK: - ARView host

struct ARViewContainer: UIViewRepresentable {
    let arView: ARView
    func makeUIView(context: Context) -> ARView { arView }
    func updateUIView(_ uiView: ARView, context: Context) {}
}

// MARK: - Window chrome helper
//
// RootView applies .ignoresSafeArea() to the Lens, which zeroes the safe-area
// insets for everything inside it. Chrome that must clear the notch/status bar
// re-derives the real inset from the key window.

@MainActor
enum WindowChrome {
    static var topInset: CGFloat {
        let window = UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .flatMap(\.windows)
            .first(where: \.isKeyWindow)
        return window?.safeAreaInsets.top ?? 59
    }

    static var interfaceOrientation: UIInterfaceOrientation {
        UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .first?.interfaceOrientation ?? .portrait
    }
}
