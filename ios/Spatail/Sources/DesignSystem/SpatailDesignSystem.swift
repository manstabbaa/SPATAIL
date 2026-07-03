import SwiftUI

// SPATAIL — Brand/flow design system (SwiftUI token layer).
//
// A 1:1 port of design_handoff_spatail_app/design-system/tokens/*.css. Treat THIS
// file as the single source of truth in Swift; never hardcode a hex / radius / size
// at a call site — reference a token here. The non-negotiables (design-system/CLAUDE.md):
//   • Display/hero type is ALWAYS Regular (400). Hierarchy from size + tight tracking.
//   • One accent only: indigo #4941C1, used sparingly.
//   • Apple corners: radius ladder + continuous squircle curvature + concentric nesting.
//   • visionOS frosted glass: translucent fill + blur/saturation + top specular + rim + float.
//
// This is NOT the spatial-placement design system (the §-numbered AR rules). This is
// the 2-D UI chrome look.

// MARK: - Color hex helper

extension Color {
    /// 0xRRGGBB literal → Color, with optional opacity (mirrors the CSS rgba alpha).
    init(hex: UInt32, opacity: Double = 1) {
        self.init(
            .sRGB,
            red: Double((hex >> 16) & 0xFF) / 255,
            green: Double((hex >> 8) & 0xFF) / 255,
            blue: Double(hex & 0xFF) / 255,
            opacity: opacity
        )
    }
}

// MARK: - Color tokens  (tokens/colors.css)

enum SpatailColor {
    // Ink & paper
    static let ink900 = Color(hex: 0x0D0D0D)
    static let ink800 = Color(hex: 0x161617)
    static let ink700 = Color(hex: 0x1F1F21)
    static let paper  = Color(hex: 0xFFFFFF)
    static let paper2 = Color(hex: 0xFAFAFA)

    // Cool gray ramp 50…900
    static let gray50  = Color(hex: 0xF5F6F7)
    static let gray100 = Color(hex: 0xEBECEE)
    static let gray200 = Color(hex: 0xDDDFE2)
    static let gray300 = Color(hex: 0xC5C8CD)
    static let gray400 = Color(hex: 0xA4A8AF)
    static let gray500 = Color(hex: 0x80858D)
    static let gray600 = Color(hex: 0x5C616A)
    static let gray700 = Color(hex: 0x3F444C)
    static let gray800 = Color(hex: 0x2A2E34)
    static let gray900 = Color(hex: 0x16181C)

    // The one accent — indigo (from the wordmark)
    static let indigo50  = Color(hex: 0xEEEDF9)
    static let indigo100 = Color(hex: 0xD8D6F1)
    static let indigo300 = Color(hex: 0x9B96E0)
    static let indigo500 = Color(hex: 0x4941C1)   // ⭐ the accent
    static let indigo600 = Color(hex: 0x3D36A6)   // hover
    static let indigo700 = Color(hex: 0x2F2A82)   // press

    // Glass fills / edges
    static let glassLight       = Color(hex: 0xF8F9FB, opacity: 0.55)
    static let glassDark        = Color(hex: 0x1C1C22, opacity: 0.52)
    static let glassBorderLight = Color(hex: 0xFFFFFF, opacity: 0.55)
    static let glassBorderDark  = Color(hex: 0xFFFFFF, opacity: 0.10)
    static let glassHiLight     = Color(hex: 0xFFFFFF, opacity: 0.85)   // top specular
    static let glassRimLight    = Color(hex: 0xFFFFFF, opacity: 0.30)   // hairline rim
    static let glassHiDark      = Color(hex: 0xFFFFFF, opacity: 0.22)
    static let glassRimDark     = Color(hex: 0xFFFFFF, opacity: 0.08)

    // Semantic text
    static let textStrong   = ink900
    static let textBody     = gray800
    static let textMuted    = gray500
    static let textFaint    = gray400
    static let textInverse  = paper
    static let textAccent   = indigo500
    static let textOnAccent = paper

    // Surfaces
    static let surfacePage    = paper
    static let surfaceSunken  = gray50
    static let surfaceCard    = paper
    static let surfaceInverse = ink900
    static let surfaceAccent  = indigo500
    static let surfaceGlass   = glassLight

    // Borders
    static let borderHairline = gray200
    static let borderDefault  = gray300
    static let borderStrong   = gray400
    static let borderAccent   = indigo500

    // Interaction tints
    static let accentHover = indigo600
    static let accentPress = indigo700
    static let hoverTint   = gray50
    static let pressTint   = gray100
    static let focusRing   = indigo500

    // Status
    static let statusInfo    = Color(hex: 0x4139B9)
    static let statusSuccess = Color(hex: 0xA4E761)
    static let statusWarning = Color(hex: 0xFFD54B)
    static let statusDanger  = Color(hex: 0xFF6601)
}

// MARK: - Spacing  (tokens/spacing.css — strict 8pt grid)

enum SpatailSpace {
    static let s0: CGFloat = 0
    static let s1: CGFloat = 4
    static let s2: CGFloat = 8
    static let s3: CGFloat = 12
    static let s4: CGFloat = 16
    static let s5: CGFloat = 24
    static let s6: CGFloat = 32
    static let s7: CGFloat = 48
    static let s8: CGFloat = 64
    static let s9: CGFloat = 96
    static let s10: CGFloat = 128
    static let s11: CGFloat = 192
}

// MARK: - Corner radii  (tokens/effects.css — Apple radius ladder)
//
// Rounded rectangles use the continuous-curvature squircle (RoundedRectangle .continuous,
// which IS Apple's superellipse). True circles/capsules use Circle()/Capsule() — never
// squircled. Concentric nesting (inner = outer − padding) is applied at call sites.

enum SpatailRadius {
    static let r0: CGFloat    = 0
    static let xs: CGFloat    = 6
    static let sm: CGFloat    = 8
    static let md: CGFloat    = 12     // buttons, inputs, icon buttons
    static let lg: CGFloat    = 16     // cards, list sections
    static let xl: CGFloat    = 22     // large cards, sheets
    static let glass: CGFloat = 28     // floating glass panels (curviest tier)
    static let pill: CGFloat  = 999    // capsules, toggles, avatars

    /// Concentric child radius: inner = outer − padding, floored at xs.
    static func concentric(outer: CGFloat, inset: CGFloat) -> CGFloat {
        max(xs, outer - inset)
    }
}

/// The app's standard squircle. Use everywhere a rounded rectangle is wanted.
func Squircle(_ radius: CGFloat) -> RoundedRectangle {
    RoundedRectangle(cornerRadius: radius, style: .continuous)
}

// MARK: - Typography  (tokens/typography.css + fonts.css)
//
// Family is Helvetica Neue (ships with iOS — no bundled font needed). Display/hero is
// ALWAYS Regular; the scale below encodes the default weight per the token spec.

enum SpatailWeight {
    case thin, light, regular, medium, bold, heavy, black

    /// Helvetica Neue PostScript faces available on iOS (heavy/black fall back to Bold).
    var postScriptName: String {
        switch self {
        case .thin:    return "HelveticaNeue-Thin"
        case .light:   return "HelveticaNeue-Light"
        case .regular: return "HelveticaNeue"
        case .medium:  return "HelveticaNeue-Medium"
        case .bold:    return "HelveticaNeue-Bold"
        case .heavy:   return "HelveticaNeue-Bold"
        case .black:   return "HelveticaNeue-Bold"
        }
    }
}

enum SpatailFont {
    static func custom(_ size: CGFloat, _ weight: SpatailWeight = .regular) -> Font {
        .custom(weight.postScriptName, fixedSize: size)
    }
}

/// A typographic style = size + default weight + tracking (em) + line-height multiplier.
/// Apply with `.spatailType(_:)`. Tracking is converted em→points internally.
struct SpatailTextStyle {
    var size: CGFloat
    var weight: SpatailWeight
    var trackingEm: CGFloat
    var leading: CGFloat          // line-height multiplier

    // Scale (px) — display 72 · h1 48 · h2 34 · h3 24 · title 20 · lg 18 · base 16 · sm 14 · xs 12 · micro 11.
    // RULE: display/h-levels default to Regular (400). Labels/titles/buttons use Medium (500).
    static let display = SpatailTextStyle(size: 72, weight: .regular, trackingEm: -0.02, leading: 1.05)
    static let h1      = SpatailTextStyle(size: 48, weight: .regular, trackingEm: -0.02, leading: 1.05)
    static let h2      = SpatailTextStyle(size: 34, weight: .regular, trackingEm: -0.01, leading: 1.1)
    static let h3      = SpatailTextStyle(size: 24, weight: .regular, trackingEm: -0.01, leading: 1.2)
    static let title   = SpatailTextStyle(size: 20, weight: .medium,  trackingEm: -0.01, leading: 1.2)
    static let lg      = SpatailTextStyle(size: 18, weight: .regular, trackingEm: 0,     leading: 1.5)
    static let base    = SpatailTextStyle(size: 16, weight: .regular, trackingEm: 0,     leading: 1.5)
    static let sm      = SpatailTextStyle(size: 14, weight: .regular, trackingEm: 0,     leading: 1.5)
    static let xs      = SpatailTextStyle(size: 12, weight: .regular, trackingEm: 0,     leading: 1.5)
    static let micro   = SpatailTextStyle(size: 11, weight: .medium,  trackingEm: 0,     leading: 1.5)
    /// Uppercase eyebrow label: micro size, wide 0.08em tracking.
    static let label   = SpatailTextStyle(size: 11, weight: .medium,  trackingEm: 0.08,  leading: 1.5)
}

private struct SpatailTypeModifier: ViewModifier {
    let style: SpatailTextStyle
    var weightOverride: SpatailWeight?
    func body(content: Content) -> some View {
        let w = weightOverride ?? style.weight
        content
            .font(SpatailFont.custom(style.size, w))
            .tracking(style.trackingEm * style.size)            // em → points
            .lineSpacing(style.size * (style.leading - 1))
    }
}

extension View {
    /// Apply a Spatail type style (font + tracking + line-height). Optionally override weight.
    func spatailType(_ style: SpatailTextStyle, weight: SpatailWeight? = nil) -> some View {
        modifier(SpatailTypeModifier(style: style, weightOverride: weight))
    }
}

// MARK: - Elevation & shadows  (tokens/effects.css)
//
// SwiftUI shadows have no spread; the float values approximate the CSS blur/spread.

struct SpatailShadow {
    var color: Color
    var radius: CGFloat
    var x: CGFloat = 0
    var y: CGFloat

    // 0 1px 2px / 0 2px 8px / 0 8px 24px / 0 18px 48px, all rgba(13,13,13,a)
    static let s1 = SpatailShadow(color: Color(hex: 0x0D0D0D, opacity: 0.06), radius: 1,  y: 1)
    static let s2 = SpatailShadow(color: Color(hex: 0x0D0D0D, opacity: 0.08), radius: 4,  y: 2)
    static let s3 = SpatailShadow(color: Color(hex: 0x0D0D0D, opacity: 0.10), radius: 12, y: 8)
    static let s4 = SpatailShadow(color: Color(hex: 0x0D0D0D, opacity: 0.14), radius: 24, y: 18)
    static let float = SpatailShadow(color: Color(hex: 0x0D0D0D, opacity: 0.28), radius: 26, y: 22)
}

extension View {
    func spatailShadow(_ s: SpatailShadow) -> some View {
        shadow(color: s.color, radius: s.radius, x: s.x, y: s.y)
    }
}

// MARK: - Motion  (tokens/effects.css)
//
// Understated, never bouncy. ease-standard cubic-bezier(0.2,0,0,1).

enum SpatailMotion {
    static let durFast = 0.12
    static let durBase = 0.20
    static let durSlow = 0.32
    static let durDeliberate = 0.48

    static let standard = Animation.timingCurve(0.2, 0, 0, 1, duration: durBase)
    static let entrance = Animation.timingCurve(0.16, 1, 0.3, 1, duration: durSlow)
    static let exit     = Animation.timingCurve(0.4, 0, 1, 1, duration: durBase)
    static let fast     = Animation.timingCurve(0.2, 0, 0, 1, duration: durFast)
}

// MARK: - Glass material  (tokens/effects.css glass composite)
//
// The signature visionOS surface: translucent fill over a heavy blur+saturation,
// a bright top specular edge, a faint all-round rim, a sheen wash, and a soft float
// shadow. The inset rings ARE the edge — no separate border. Apply with `.spatailGlass()`.

enum SpatailGlassTone {
    case light, dark

    var fill: Color { self == .light ? SpatailColor.glassLight : SpatailColor.glassDark }
    var heading: Color { self == .light ? SpatailColor.textStrong : SpatailColor.paper }
    var eyebrow: Color { self == .light ? SpatailColor.textMuted : Color(hex: 0xFFFFFF, opacity: 0.62) }

    /// Top specular → rim gradient stroke (bright at the very top, faint elsewhere).
    var edge: LinearGradient {
        let hi  = self == .light ? SpatailColor.glassHiLight  : SpatailColor.glassHiDark
        let rim = self == .light ? SpatailColor.glassRimLight : SpatailColor.glassRimDark
        return LinearGradient(
            stops: [.init(color: hi, location: 0), .init(color: rim, location: 0.12),
                    .init(color: rim, location: 1)],
            startPoint: .top, endPoint: .bottom)
    }

    /// Sheen wash — light catching the top of the glass.
    var sheen: LinearGradient {
        if self == .light {
            return LinearGradient(stops: [
                .init(color: Color(hex: 0xFFFFFF, opacity: 0.45), location: 0),
                .init(color: Color(hex: 0xFFFFFF, opacity: 0.04), location: 0.42),
                .init(color: Color(hex: 0xFFFFFF, opacity: 0),    location: 1),
            ], startPoint: .top, endPoint: .bottom)
        } else {
            return LinearGradient(stops: [
                .init(color: Color(hex: 0xFFFFFF, opacity: 0.14), location: 0),
                .init(color: Color(hex: 0xFFFFFF, opacity: 0.02), location: 0.40),
                .init(color: Color(hex: 0xFFFFFF, opacity: 0),    location: 1),
            ], startPoint: .top, endPoint: .bottom)
        }
    }
}

private struct SpatailGlassModifier: ViewModifier {
    var tone: SpatailGlassTone
    var radius: CGFloat
    func body(content: Content) -> some View {
        let shape = RoundedRectangle(cornerRadius: radius, style: .continuous)
        return content
            .background {
                ZStack {
                    shape.fill(.ultraThinMaterial)   // blur + saturation (vibrancy)
                    shape.fill(tone.fill)            // translucent glass tint
                    shape.fill(tone.sheen)           // top sheen wash
                }
            }
            .overlay { shape.strokeBorder(tone.edge, lineWidth: 1) }
            .clipShape(shape)
            .compositingGroup()
            .spatailShadow(.s1)                       // tight contact shadow
            .spatailShadow(.float)                    // soft ambient float
    }
}

extension View {
    /// The visionOS glass material. Default radius = glass (28).
    func spatailGlass(tone: SpatailGlassTone = .light, radius: CGFloat = SpatailRadius.glass) -> some View {
        modifier(SpatailGlassModifier(tone: tone, radius: radius))
    }
}
