import SwiftUI

// SPATAIL — design-system foundation gallery (DEBUG verification surface).
// Renders the token layer + GlassPanel/Button/IconButton/Badge over a scene-like
// backdrop so the glass material is visible. Launch the app with `--spatail-gallery`
// (see SpatailEducatorApp). Not shipped in the normal flow.

struct DesignSystemGallery: View {
    @State private var listening = false
    @State private var presentMode = true

    var body: some View {
        ZStack {
            sceneBackdrop
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: SpatailSpace.s6) {
                        header
                        glassSection
                        buttonsSection
                        iconAndBadgeSection
                        typeScaleSection
                        colorSection
                        radiusSection.id("bottom")
                    }
                    .padding(SpatailSpace.s5)
                }
                .onAppear {
                    // Debug-only: `--spatail-gallery-bottom` jumps to the lower sections
                    // so the simulator screenshot can capture type/color/radius.
                    if ProcessInfo.processInfo.arguments.contains("--spatail-gallery-bottom") {
                        DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) {
                            withAnimation { proxy.scrollTo("bottom", anchor: .bottom) }
                        }
                    }
                }
            }
        }
        .preferredColorScheme(.light)
    }

    // A warm room-ish gradient so the frosted glass reads (stand-in for a camera frame).
    private var sceneBackdrop: some View {
        LinearGradient(
            colors: [Color(hex: 0xE9E4DC), Color(hex: 0xCFC7BC), Color(hex: 0x9A9488)],
            startPoint: .topLeading, endPoint: .bottomTrailing
        )
        .overlay(
            RadialGradient(colors: [SpatailColor.indigo300.opacity(0.35), .clear],
                           center: .topTrailing, startRadius: 20, endRadius: 360)
        )
        .ignoresSafeArea()
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: SpatailSpace.s1) {
            Text("FOUNDATION").spatailType(.label).foregroundStyle(SpatailColor.textMuted)
            Text("Spatail Design System").spatailType(.h2).foregroundStyle(SpatailColor.textStrong)
        }
    }

    private func sectionLabel(_ s: String) -> some View {
        Text(s.uppercased()).spatailType(.label).foregroundStyle(SpatailColor.textMuted)
    }

    // MARK: GlassPanel — light + dark, the prompt.md example
    private var glassSection: some View {
        VStack(alignment: .leading, spacing: SpatailSpace.s3) {
            sectionLabel("GlassPanel")
            GlassPanel(eyebrow: "Now placing", title: "Bauhaus Chair") {
                Text("1:1 · anchored to floor")
                    .spatailType(.sm).foregroundStyle(SpatailColor.textBody)
            }
            HStack(spacing: SpatailSpace.s3) {
                GlassPanel(eyebrow: "Anchored", title: "Trail e-bike", tone: .dark) {
                    Text("scale 1:1 · 4 parts")
                        .spatailType(.sm).foregroundStyle(Color(hex: 0xFFFFFF, opacity: 0.82))
                }
                // floating spec chip
                GlassPanel {
                    VStack(alignment: .leading, spacing: SpatailSpace.s2) {
                        SpatailBadge(text: "Anchored", tone: .accent)
                        Text("1.62 m").spatailType(.sm, weight: .medium)
                            .foregroundStyle(SpatailColor.textStrong)
                    }
                }
            }
        }
    }

    // MARK: Buttons — variants + sizes
    private var buttonsSection: some View {
        VStack(alignment: .leading, spacing: SpatailSpace.s3) {
            sectionLabel("Buttons")
            HStack(spacing: SpatailSpace.s3) {
                SpatailButton(title: "Place in space", variant: .primary, iconLeft: "arkit")
                SpatailButton(title: "Cancel", variant: .secondary)
                SpatailButton(title: "Skip", variant: .ghost, size: .sm)
            }
            HStack(spacing: SpatailSpace.s3) {
                SpatailButton(title: "Sm", variant: .inverse, size: .sm)
                SpatailButton(title: "Md", variant: .inverse, size: .md)
                SpatailButton(title: "Lg", variant: .inverse, size: .lg)
                SpatailButton(title: "Off", variant: .primary, disabled: true)
                Spacer(minLength: 0)
            }
        }
    }

    // MARK: IconButtons + Badges
    private var iconAndBadgeSection: some View {
        VStack(alignment: .leading, spacing: SpatailSpace.s3) {
            sectionLabel("IconButton · Badge")
            HStack(spacing: SpatailSpace.s3) {
                SpatailIconButton(systemName: "square.stack.3d.up", label: "Library", variant: .outline)
                SpatailIconButton(systemName: "person.crop.circle", label: "Profile", variant: .ghost)
                SpatailIconButton(systemName: "mic.fill", label: "Listen",
                                  variant: listening ? .solid : .outline, active: listening) { listening.toggle() }
                SpatailIconButton(systemName: "wand.and.stars", label: "Generate", variant: .solid)
            }
            HStack(spacing: SpatailSpace.s2) {
                SpatailBadge(text: "Accent", tone: .accent)
                SpatailBadge(text: "Neutral", tone: .neutral)
                SpatailBadge(text: "Outline", tone: .outline)
                SpatailBadge(text: "Saved", tone: .success)
                SpatailBadge(text: "Beta", tone: .warning)
                SpatailBadge(text: "Sign out", tone: .danger)
            }
        }
    }

    // MARK: Type scale — display is Regular (the non-negotiable)
    private var typeScaleSection: some View {
        VStack(alignment: .leading, spacing: SpatailSpace.s2) {
            sectionLabel("Type — display is Regular 400")
            Text("Display 72").spatailType(.display).foregroundStyle(SpatailColor.textStrong)
            Text("Heading 34").spatailType(.h2).foregroundStyle(SpatailColor.textStrong)
            Text("Title 20 Medium").spatailType(.title).foregroundStyle(SpatailColor.textStrong)
            Text("Body 16 — hierarchy from size + tight tracking, never bold.")
                .spatailType(.base).foregroundStyle(SpatailColor.textBody)
            Text("MICRO 11 EYEBROW").spatailType(.label).foregroundStyle(SpatailColor.textMuted)
        }
    }

    // MARK: Color — ink/paper, gray ramp, the one accent, status
    private var colorSection: some View {
        VStack(alignment: .leading, spacing: SpatailSpace.s2) {
            sectionLabel("Color — one accent: indigo #4941C1")
            swatchRow([SpatailColor.ink900, SpatailColor.gray800, SpatailColor.gray600,
                       SpatailColor.gray400, SpatailColor.gray200, SpatailColor.paper])
            swatchRow([SpatailColor.indigo700, SpatailColor.indigo600, SpatailColor.indigo500,
                       SpatailColor.indigo300, SpatailColor.indigo100, SpatailColor.indigo50])
            swatchRow([SpatailColor.statusInfo, SpatailColor.statusSuccess,
                       SpatailColor.statusWarning, SpatailColor.statusDanger])
        }
    }

    private func swatchRow(_ colors: [Color]) -> some View {
        HStack(spacing: SpatailSpace.s2) {
            ForEach(Array(colors.enumerated()), id: \.offset) { _, c in
                Squircle(SpatailRadius.sm).fill(c)
                    .frame(width: 44, height: 32)
                    .overlay(Squircle(SpatailRadius.sm).strokeBorder(SpatailColor.borderHairline, lineWidth: 1))
            }
        }
    }

    // MARK: Radii — squircle vs true circle
    private var radiusSection: some View {
        VStack(alignment: .leading, spacing: SpatailSpace.s2) {
            sectionLabel("Corners — squircle (rects) · round (circles)")
            HStack(spacing: SpatailSpace.s3) {
                ForEach([("md 12", SpatailRadius.md), ("lg 16", SpatailRadius.lg),
                         ("xl 22", SpatailRadius.xl), ("glass 28", SpatailRadius.glass)], id: \.0) { name, r in
                    VStack(spacing: SpatailSpace.s1) {
                        Squircle(r).fill(SpatailColor.gray100)
                            .frame(width: 56, height: 56)
                            .overlay(Squircle(r).strokeBorder(SpatailColor.borderDefault, lineWidth: 1))
                        Text(name).spatailType(.micro).foregroundStyle(SpatailColor.textMuted)
                    }
                }
                VStack(spacing: SpatailSpace.s1) {
                    Circle().fill(SpatailColor.indigo500).frame(width: 56, height: 56)
                    Text("pill / round").spatailType(.micro).foregroundStyle(SpatailColor.textMuted)
                }
            }
        }
    }
}

#if DEBUG
#Preview { DesignSystemGallery() }
#endif
