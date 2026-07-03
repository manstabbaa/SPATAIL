import SwiftUI

// SPATAIL — Brand/flow primitives (SwiftUI port of design-system/components/**).
// Ports: GlassPanel (spatial), Button + IconButton (buttons), Badge (data-display).
// Props/look mirror the reference .jsx; values come only from SpatailDesignSystem.swift.

// MARK: - GlassPanel  (components/spatial/GlassPanel.jsx)
//
// The signature MR surface: a floating glass panel over a scene. Optional eyebrow +
// title, then arbitrary content. Light tone over warm scenes, dark over bright.

struct GlassPanel<Content: View>: View {
    var eyebrow: String? = nil
    var title: String? = nil
    var tone: SpatailGlassTone = .light
    @ViewBuilder var content: Content

    private var hasContent: Bool { !(content is EmptyView) }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if let eyebrow {
                Text(eyebrow.uppercased())
                    .spatailType(.label)
                    .foregroundStyle(tone.eyebrow)
                    .padding(.bottom, SpatailSpace.s1)        // 4
            }
            if let title {
                Text(title)
                    .spatailType(.title, weight: .bold)        // panel title is bold per the .jsx
                    .foregroundStyle(tone.heading)
                    .padding(.bottom, hasContent ? SpatailSpace.s2 : 0)   // 8 if content follows
            }
            content
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 18)
        .spatailGlass(tone: tone)
    }
}

extension GlassPanel where Content == EmptyView {
    init(eyebrow: String? = nil, title: String? = nil, tone: SpatailGlassTone = .light) {
        self.init(eyebrow: eyebrow, title: title, tone: tone) { EmptyView() }
    }
}

// MARK: - Button  (components/buttons/Button.jsx)

enum SpatailButtonVariant { case primary, secondary, ghost, inverse }
enum SpatailControlSize {
    case sm, md, lg
    var height: CGFloat { self == .sm ? 32 : self == .md ? 40 : 48 }
    var hPad: CGFloat   { self == .sm ? 12 : self == .md ? 18 : 24 }
    var radius: CGFloat { self == .sm ? SpatailRadius.sm : self == .md ? SpatailRadius.md : SpatailRadius.lg }
    var textStyle: SpatailTextStyle { self == .sm ? .sm : self == .md ? .base : .lg }
}

/// The single primary action carries the indigo accent; everything else stays neutral.
struct SpatailButtonStyle: ButtonStyle {
    var variant: SpatailButtonVariant = .secondary
    var size: SpatailControlSize = .md
    var disabled: Bool = false

    private var background: Color {
        switch variant {
        case .primary:   return SpatailColor.surfaceAccent
        case .secondary: return SpatailColor.paper
        case .ghost:     return .clear
        case .inverse:   return SpatailColor.surfaceInverse
        }
    }
    private var foreground: Color {
        switch variant {
        case .primary:   return SpatailColor.textOnAccent
        case .secondary, .ghost: return SpatailColor.textStrong
        case .inverse:   return SpatailColor.textInverse
        }
    }
    private var borderColor: Color {
        variant == .secondary ? SpatailColor.borderDefault : .clear
    }
    /// Pressed background (mirrors the .jsx hover/press tints).
    private func pressedBackground() -> Color {
        switch variant {
        case .primary:   return SpatailColor.accentPress
        case .secondary: return SpatailColor.pressTint
        case .ghost:     return SpatailColor.pressTint
        case .inverse:   return SpatailColor.ink700
        }
    }

    func makeBody(configuration: Configuration) -> some View {
        let pressed = configuration.isPressed && !disabled
        configuration.label
            .spatailType(size.textStyle, weight: .medium)
            .foregroundStyle(foreground)
            .frame(height: size.height)
            .padding(.horizontal, size.hPad)
            .background(Squircle(size.radius).fill(pressed ? pressedBackground() : background))
            .overlay(Squircle(size.radius).strokeBorder(borderColor, lineWidth: 1))
            .opacity(disabled ? 0.4 : 1)
            .scaleEffect(pressed ? 0.98 : 1)                    // press: scale(0.98)
            .animation(SpatailMotion.fast, value: configuration.isPressed)
    }
}

/// Convenience text/icon button. `iconLeft`/`iconRight` are SF Symbol names.
struct SpatailButton: View {
    var title: String? = nil
    var variant: SpatailButtonVariant = .secondary
    var size: SpatailControlSize = .md
    var iconLeft: String? = nil
    var iconRight: String? = nil
    var disabled: Bool = false
    var action: () -> Void = {}

    var body: some View {
        Button(action: action) {
            HStack(spacing: SpatailSpace.s2) {           // gap 8
                if let iconLeft { Image(systemName: iconLeft) }
                if let title, !title.isEmpty { Text(title).lineLimit(1) }
                if let iconRight { Image(systemName: iconRight) }
            }
        }
        .buttonStyle(SpatailButtonStyle(variant: variant, size: size, disabled: disabled))
        .disabled(disabled)
    }
}

// MARK: - IconButton  (components/buttons/IconButton.jsx)

enum SpatailIconButtonVariant { case ghost, outline, solid }

/// A square, quiet control for toolbars and panels. Icon-only — always give a label.
struct SpatailIconButton: View {
    var systemName: String
    var label: String
    var size: SpatailControlSize = .md
    var variant: SpatailIconButtonVariant = .ghost
    var active: Bool = false
    var disabled: Bool = false
    var action: () -> Void = {}

    private var background: Color {
        switch variant {
        case .ghost:   return active ? SpatailColor.pressTint : .clear
        case .outline: return SpatailColor.paper
        case .solid:   return SpatailColor.surfaceAccent
        }
    }
    private var foreground: Color {
        switch variant {
        case .ghost, .outline: return active ? SpatailColor.textAccent : SpatailColor.textBody
        case .solid:           return SpatailColor.textOnAccent
        }
    }
    private var borderColor: Color {
        variant == .outline ? (active ? SpatailColor.borderAccent : SpatailColor.borderDefault) : .clear
    }

    var body: some View {
        Button(action: action) {
            Image(systemName: systemName)
                .foregroundStyle(foreground)
                .frame(width: size.height, height: size.height)   // square
                .background(Squircle(size.radius).fill(background))
                .overlay(Squircle(size.radius).strokeBorder(borderColor, lineWidth: 1))
        }
        .buttonStyle(.plain)
        .opacity(disabled ? 0.4 : 1)
        .disabled(disabled)
        .accessibilityLabel(label)
    }
}

// MARK: - Badge  (components/data-display/Badge.jsx) — pill, status-tinted

enum SpatailBadgeTone { case accent, neutral, outline, success, warning, danger }

struct SpatailBadge: View {
    var text: String
    var tone: SpatailBadgeTone = .neutral

    private var background: Color {
        switch tone {
        case .accent:  return SpatailColor.indigo500
        case .neutral: return SpatailColor.gray100
        case .outline: return .clear
        case .success: return SpatailColor.statusSuccess
        case .warning: return SpatailColor.statusWarning
        case .danger:  return SpatailColor.statusDanger
        }
    }
    private var foreground: Color {
        switch tone {
        case .accent, .danger: return SpatailColor.paper
        case .neutral:  return SpatailColor.textBody
        case .outline:  return SpatailColor.textMuted
        case .success, .warning: return SpatailColor.ink900
        }
    }
    private var borderColor: Color { tone == .outline ? SpatailColor.borderDefault : .clear }

    var body: some View {
        Text(text)
            .spatailType(.micro, weight: .medium)
            .foregroundStyle(foreground)
            .padding(.horizontal, SpatailSpace.s2)
            .padding(.vertical, SpatailSpace.s1)
            .background(Capsule().fill(background))          // true capsule — not squircled
            .overlay(Capsule().strokeBorder(borderColor, lineWidth: 1))
    }
}
