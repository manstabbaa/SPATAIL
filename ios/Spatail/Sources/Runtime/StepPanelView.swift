// StepPanelView.swift — the sequenced experience's reading surface: the current
// step's title, narration, fact panels and quiz on a design-system glass panel,
// PINNED beside the 3-D point the step explains (the contract's panel locus).
//
// Pinning: the runtime publishes the step's world anchor point (region / part /
// bbox anchor); a 12 Hz frame consumer (the LensChipsModel pattern — projection
// only, no work) projects it to screen space and the panel rides beside it per
// placement.ui.panelPosition. When the anchor is off-screen/behind the camera
// the panel settles to the bottom, above the AskBar — never lost.
//
// Also hosts the SpatailEngine HUD (score · objective · win/lose) and the
// shooter fire control when an engine kit is running.

import SwiftUI
import RealityKit
import simd

// MARK: - Anchor projection (12 Hz, main — projection only)

@MainActor
private final class StepAnchorProjector: ObservableObject {
    @Published private(set) var point: CGPoint?

    private weak var hub: ARSessionHub?
    private weak var runtime: ExperienceRuntime?
    private var attached = false
    private static let consumerId = "runtime.steppanel"

    func attach(hub: ARSessionHub, runtime: ExperienceRuntime) {
        guard !attached else { return }
        attached = true
        self.hub = hub
        self.runtime = runtime
        hub.addFrameConsumer(id: Self.consumerId, hz: 12) { [weak self] _ in
            self?.tick()
        }
    }

    func detach() {
        hub?.removeFrameConsumer(id: Self.consumerId)
        attached = false
        point = nil
    }

    private func tick() {
        guard let hub, let runtime else { return }
        guard let anchor = runtime.stepHUD?.anchorWorld else {
            if point != nil { point = nil }
            return
        }
        let arView = hub.arView
        let bounds = arView.bounds
        guard bounds.width > 1, bounds.height > 1,
              let p = arView.project(anchor),          // nil = behind camera
              p.x > 0, p.x < bounds.width,
              p.y > 0, p.y < bounds.height else {
            if point != nil { point = nil }
            return
        }
        if point != p { point = p }
    }
}

// MARK: - StepPanelView

struct StepPanelView: View {
    @EnvironmentObject private var runtime: ExperienceRuntime
    @EnvironmentObject private var hub: ARSessionHub
    @StateObject private var projector = StepAnchorProjector()

    var body: some View {
        GeometryReader { geo in
            ZStack {
                if let hud = runtime.stepHUD {
                    panel(hud: hud, in: geo.size)
                        // quiz state resets per step AND per experience
                        .id("\(hud.experienceTitle)#\(hud.index)")
                        .transition(.opacity)
                }
                if let engine = runtime.engineHUD {
                    engineChrome(engine, in: geo.size)
                }
            }
            .animation(SpatailMotion.standard, value: runtime.stepHUD)
        }
        .onAppear { DispatchQueue.main.async { projector.attach(hub: hub, runtime: runtime) } }
        .onDisappear { projector.detach() }
        .allowsHitTesting(runtime.stepHUD != nil || runtime.engineHUD != nil)
    }

    // MARK: step panel (glass, pinned per locus)

    @ViewBuilder
    private func panel(hud: StepHUD, in size: CGSize) -> some View {
        let width = min(size.width - SpatailSpace.s4 * 2, 320)
        StepPanelCard(hud: hud,
                      onPrevious: { runtime.stepPrevious() },
                      onNext: { runtime.stepNext() },
                      onQuiz: { correct in runtime.quizAnswered(correct: correct) })
            .frame(width: width)
            .position(panelPosition(hud: hud, panelWidth: width, in: size))
    }

    /// FIXED bottom dock, always. The panel used to chase a projected world
    /// anchor across the screen — on device it wandered top-to-bottom with
    /// every head move (field report 2026-07-03). Reading needs a stable
    /// place; the 3D scene, not the panel, is what points at the object.
    private func panelPosition(hud: StepHUD, panelWidth: CGFloat,
                               in size: CGSize) -> CGPoint {
        let estimatedHeight: CGFloat = 180
        return CGPoint(x: size.width / 2,
                       y: size.height - estimatedHeight / 2 - 148)
    }

    // MARK: engine HUD (score · objective · outcome · fire control)

    @ViewBuilder
    private func engineChrome(_ engine: EngineHost.HUDState, in size: CGSize) -> some View {
        VStack {
            if !engine.fields.isEmpty {
                HStack(spacing: SpatailSpace.s2) {
                    ForEach(engine.fields, id: \.key) { field in
                        HStack(spacing: SpatailSpace.s1) {
                            Text(field.key.uppercased())
                                .spatailType(.label)
                                .foregroundStyle(SpatailGlassTone.dark.eyebrow)
                            Text(field.value)
                                .spatailType(.sm, weight: .medium)
                                .foregroundStyle(SpatailColor.paper)
                        }
                    }
                }
                .padding(.horizontal, SpatailSpace.s3)
                .padding(.vertical, SpatailSpace.s2)
                .spatailGlass(tone: .dark, radius: SpatailRadius.pill)
                .padding(.top, WindowChrome.cachedTopInset + 100)
            }
            Spacer()
            if engine.genre == "shooter" {
                SpatailButton(title: "Fire", variant: .primary, size: .lg,
                              iconLeft: "scope") {
                    runtime.engineFire()
                }
                .padding(.bottom, 220)
            }
        }
        .frame(width: size.width, height: size.height)
    }
}

// MARK: - The card itself (design-system glass; quiz interactive)

private struct StepPanelCard: View {
    let hud: StepHUD
    let onPrevious: () -> Void
    let onNext: () -> Void
    let onQuiz: (Bool) -> Void

    /// Quiz selections this step (panel index → chosen option). Card is
    /// re-created per step via .id(hud.index), so this resets naturally.
    @State private var quizChoices: [Int: Int] = [:]

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("Step \(hud.index + 1) of \(hud.count) · \(hud.experienceTitle)"
                    .uppercased())
                .spatailType(.label)
                .foregroundStyle(SpatailGlassTone.dark.eyebrow)
                .lineLimit(1)
                .padding(.bottom, SpatailSpace.s1)

            if !hud.title.isEmpty {
                Text(hud.title)
                    .spatailType(.title, weight: .bold)
                    .foregroundStyle(SpatailColor.paper)
                    .padding(.bottom, SpatailSpace.s2)
            }

            // Body content is capped and scrolls: a step with long narration +
            // panels must stay a compact card, never a screen-filling sheet
            // (field report 2026-07-03). Narration is spoken anyway.
            ScrollView(showsIndicators: false) {
                VStack(alignment: .leading, spacing: 0) {
                    if !hud.narration.isEmpty {
                        Text(hud.narration)
                            .spatailType(.sm)
                            .foregroundStyle(SpatailColor.paper.opacity(0.92))
                            .fixedSize(horizontal: false, vertical: true)
                    }

                    ForEach(Array(hud.panels.enumerated()), id: \.offset) { i, panel in
                        panelBlock(panel, index: i)
                            .padding(.top, SpatailSpace.s3)
                    }
                }
            }
            .frame(maxHeight: 150)

            HStack {
                SpatailIconButton(systemName: "chevron.backward", label: "Previous step",
                                  size: .sm, variant: .ghost, action: onPrevious)
                Spacer()
                if hud.advance == "auto" {
                    Text("advances automatically")
                        .spatailType(.micro)
                        .foregroundStyle(SpatailColor.textFaint)
                } else {
                    Text(hud.index + 1 == hud.count ? "tap to restart" : "tap to continue")
                        .spatailType(.micro)
                        .foregroundStyle(SpatailColor.textFaint)
                }
                Spacer()
                SpatailIconButton(systemName: "chevron.forward", label: "Next step",
                                  size: .sm, variant: .ghost, action: onNext)
            }
            .padding(.top, SpatailSpace.s3)
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 18)
        .spatailGlass(tone: .dark)
    }

    @ViewBuilder
    private func panelBlock(_ panel: StepHUD.Panel, index: Int) -> some View {
        if panel.kind == "quiz", !panel.question.isEmpty {
            VStack(alignment: .leading, spacing: SpatailSpace.s2) {
                Text(panel.question)
                    .spatailType(.sm, weight: .medium)
                    .foregroundStyle(SpatailColor.paper)
                    .fixedSize(horizontal: false, vertical: true)
                ForEach(Array(panel.options.enumerated()), id: \.offset) { j, option in
                    quizOption(option, optionIndex: j, panel: panel, panelIndex: index)
                }
            }
        } else if !panel.body.isEmpty {
            VStack(alignment: .leading, spacing: SpatailSpace.s1) {
                if !panel.title.isEmpty {
                    Text(panel.title)
                        .spatailType(.sm, weight: .medium)
                        .foregroundStyle(SpatailColor.indigo300)
                }
                Text(panel.body)
                    .spatailType(.sm)
                    .foregroundStyle(SpatailColor.paper.opacity(0.92))
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func quizOption(_ option: String, optionIndex: Int,
                            panel: StepHUD.Panel, panelIndex: Int) -> some View {
        let chosen = quizChoices[panelIndex]
        let isChosen = chosen == optionIndex
        let isCorrect = optionIndex == panel.answer
        let revealed = chosen != nil && isChosen
        return Button {
            guard quizChoices[panelIndex] != optionIndex else { return }
            quizChoices[panelIndex] = optionIndex
            onQuiz(isCorrect)
        } label: {
            HStack(spacing: SpatailSpace.s2) {
                Image(systemName: revealed
                        ? (isCorrect ? "checkmark.circle.fill" : "xmark.circle.fill")
                        : "circle")
                    .imageScale(.small)
                    .foregroundStyle(revealed
                        ? (isCorrect ? SpatailColor.statusSuccess : SpatailColor.statusDanger)
                        : SpatailColor.textFaint)
                Text(option)
                    .spatailType(.sm)
                    .foregroundStyle(SpatailColor.paper.opacity(0.92))
                    .multilineTextAlignment(.leading)
                Spacer(minLength: 0)
            }
            .padding(.horizontal, SpatailSpace.s2)
            .padding(.vertical, SpatailSpace.s1 + 2)
            .background(
                Squircle(SpatailRadius.sm)
                    .fill(isChosen ? SpatailColor.paper.opacity(0.10) : .clear))
        }
        .buttonStyle(.plain)
    }
}
