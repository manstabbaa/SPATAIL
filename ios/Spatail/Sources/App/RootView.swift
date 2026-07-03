import SwiftUI

// RootView — the whole app is one screen: the full-bleed Lens (W6's AR view)
// with quiet glass chrome floating over it. Status chip top-left, toolbar
// top-right (truth overlay · library · settings), AskBar at the thumb.

struct RootView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var uplink: VisionUplink

    // Sever-proof chrome state (see LensView's note on the AttributeGraph
    // cycle): fed by direct publisher subscriptions, not graph observation.
    @State private var linkState: LinkState = .idle
    @State private var overlayOn = false
    @State private var askState: AppModel.AskState = .idle

    var body: some View {
        #if DEBUG
        let _ = Self._printChanges()
        #endif
        ZStack {
            LensView()
                .ignoresSafeArea()

            // The sequenced experience's reading surface (step narration/quiz
            // glass panel + engine HUD) — over the Lens, under the chrome.
            StepPanelView()
                .ignoresSafeArea()

            VStack(spacing: 0) {
                HStack(alignment: .top) {
                    LinkStatusChip(state: linkState)
                    Spacer()
                    toolbar
                }
                .padding(.horizontal, SpatailSpace.s4)
                .padding(.top, SpatailSpace.s2)

                Spacer()

                askStatus
                    .padding(.horizontal, SpatailSpace.s4)
                    .padding(.bottom, SpatailSpace.s2)

                AskBar()
                    .padding(.horizontal, SpatailSpace.s4)
                    .padding(.bottom, SpatailSpace.s3)
            }
        }
        .sheet(isPresented: $model.showLibrary) { LibraryView() }
        .sheet(isPresented: $model.showSettings) { SettingsView() }
        .onReceive(uplink.$state) { linkState = $0 }
        .onReceive(model.$showTruthOverlay) { overlayOn = $0 }
        .onReceive(model.$askState) { askState = $0 }
        // Deferred out of the render transaction: start() mutates @Published
        // state (uplink.state, …) and doing that DURING the first view update
        // is what produced "AttributeGraph: cycle detected" and a severed,
        // frozen observation graph on device (field sessions 2026-07-03: pill
        // stuck on Scanning…, eye toggle dead, while the services ran fine).
        .onAppear { DispatchQueue.main.async { model.start() } }
    }

    // MARK: Toolbar (truth overlay · library · settings)

    private var toolbar: some View {
        HStack(spacing: SpatailSpace.s1) {
            SpatailIconButton(systemName: "eye", label: "Truth overlay",
                              variant: .ghost, active: overlayOn) {
                model.showTruthOverlay.toggle()
            }
            SpatailIconButton(systemName: "books.vertical", label: "Library",
                              variant: .ghost, active: model.showLibrary) {
                model.showLibrary = true
            }
            SpatailIconButton(systemName: "gearshape", label: "Settings",
                              variant: .ghost, active: model.showSettings) {
                model.showSettings = true
            }
        }
        .padding(SpatailSpace.s1)
        .spatailGlass(tone: .dark, radius: SpatailRadius.lg)
    }

    // MARK: Ask feedback (generation progress / failure, above the AskBar)

    @ViewBuilder
    private var askStatus: some View {
        switch askState {
        case .idle:
            EmptyView()
        case .generating(let prompt):
            HStack(spacing: SpatailSpace.s2) {
                ProgressView().tint(SpatailColor.paper)
                Text("Building “\(prompt)”…")
                    .spatailType(.sm)
                    .foregroundStyle(SpatailColor.paper)
                    .lineLimit(1)
            }
            .padding(.horizontal, SpatailSpace.s4)
            .padding(.vertical, SpatailSpace.s2)
            .spatailGlass(tone: .dark, radius: SpatailRadius.pill)
        case .failed(let message):
            HStack(spacing: SpatailSpace.s2) {
                Image(systemName: "exclamationmark.triangle.fill")
                    .foregroundStyle(SpatailColor.statusDanger)
                Text(message)
                    .spatailType(.sm)
                    .foregroundStyle(SpatailColor.paper)
                    .lineLimit(2)
                SpatailIconButton(systemName: "xmark", label: "Dismiss error",
                                  size: .sm, variant: .ghost) {
                    model.dismissAskFailure()
                }
            }
            .padding(.horizontal, SpatailSpace.s3)
            .padding(.vertical, SpatailSpace.s2)
            .spatailGlass(tone: .dark, radius: SpatailRadius.lg)
        }
    }
}

// MARK: - Link status chip (honest — mirrors LinkState exactly, spec §0 law 3)

private struct LinkStatusChip: View {
    let state: LinkState

    private var label: String {
        switch state {
        case .idle: return "Offline"
        case .connecting: return "Connecting…"
        case .streaming: return "Live"
        case .failed(let n): return "Link lost (\(n)×)"
        }
    }

    private var dot: Color {
        switch state {
        case .idle: return SpatailColor.textFaint
        case .connecting: return SpatailColor.statusWarning
        case .streaming: return SpatailColor.statusSuccess
        case .failed: return SpatailColor.statusDanger
        }
    }

    var body: some View {
        HStack(spacing: SpatailSpace.s2) {
            Circle().fill(dot).frame(width: 8, height: 8)   // true circle — never squircled
            Text(label)
                .spatailType(.micro, weight: .medium)
                .foregroundStyle(SpatailColor.paper)
        }
        .padding(.horizontal, SpatailSpace.s3)
        .padding(.vertical, SpatailSpace.s2)
        .spatailGlass(tone: .dark, radius: SpatailRadius.pill)
    }
}
