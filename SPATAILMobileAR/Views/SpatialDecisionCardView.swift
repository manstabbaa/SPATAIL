// SpatialDecisionCardView.swift
//
// A single element row in the SpatialPlanPreview list. Shows the element
// title, the chosen representation mode + placement (chips), and the
// contract's own reasoning for both decisions.

import SwiftUI

struct SpatialDecisionCardView: View {
    let decision: SpatialReasoningDecision

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(decision.elementTitle)
                    .font(.headline)
                    .foregroundColor(.white)
                Spacer()
            }
            // Chips show the full decision triplet so it matches the
            // SPATAIL Contract Studio inspector on the web.
            FlowChips(items: [
                ("mode",     decision.representationMode.rawValue, .accent),
                ("place",    decision.placementKind,               .accent2),
                ("anchor",   decision.anchorStrategy,              .accent2),
                ("scale",    decision.scaleMode,                   .accent),
                ("attention",decision.attentionBehavior,           .accent2),
            ])
            VStack(alignment: .leading, spacing: 8) {
                LabeledReason(label: "WHY THIS REPRESENTATION",
                              text: decision.whyRepresentation)
                LabeledReason(label: "WHY THIS PLACEMENT",
                              text: decision.whyPlacement)
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.spatailBgElev)
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Color.spatailBorder, lineWidth: 1))
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }
}

private struct LabeledReason: View {
    let label: String
    let text: String

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(label)
                .font(.caption2).fontWeight(.semibold)
                .foregroundColor(.spatailTextDim)
                .tracking(0.7)
            Text(text)
                .font(.callout)
                .foregroundColor(.white)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

private struct Chip: View {
    enum Style { case accent, accent2 }
    let text: String
    let style: Style

    var body: some View {
        let color: Color = (style == .accent) ? .spatailAccent : .spatailAccent2
        Text(text)
            .font(.system(.caption2, design: .monospaced))
            .foregroundColor(color)
            .padding(.horizontal, 8).padding(.vertical, 3)
            .background(color.opacity(0.12))
            .overlay(Capsule().stroke(color.opacity(0.35), lineWidth: 1))
            .clipShape(Capsule())
    }
}

/// Wrapping row of (label, value, style) chips. Used by the decision card
/// to fit five chips on one row at iPhone widths without overflowing.
private struct FlowChips: View {
    let items: [(String, String, Chip.Style)]
    var body: some View {
        FlowLayout(spacing: 6) {
            ForEach(items, id: \.1) { item in
                Chip(text: "\(item.0): \(item.1)", style: item.2)
            }
        }
    }
}

/// Tiny wrapping HStack — sized to its children, wraps when the row width
/// exceeds the proposed width. Stays small on purpose; iOS 16+ has a
/// built-in `Layout` API for this.
private struct FlowLayout: Layout {
    var spacing: CGFloat = 6

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let maxW = proposal.width ?? .infinity
        var rowW: CGFloat = 0
        var totalH: CGFloat = 0
        var rowH: CGFloat = 0
        var widestRow: CGFloat = 0
        for sub in subviews {
            let s = sub.sizeThatFits(.unspecified)
            if rowW + s.width > maxW && rowW > 0 {
                widestRow = max(widestRow, rowW - spacing)
                totalH += rowH + spacing
                rowW = 0; rowH = 0
            }
            rowW += s.width + spacing
            rowH = max(rowH, s.height)
        }
        widestRow = max(widestRow, rowW - spacing)
        totalH += rowH
        return CGSize(width: widestRow, height: totalH)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize,
                       subviews: Subviews, cache: inout ()) {
        let maxX = bounds.maxX
        var x = bounds.minX
        var y = bounds.minY
        var rowH: CGFloat = 0
        for sub in subviews {
            let s = sub.sizeThatFits(.unspecified)
            if x + s.width > maxX && x > bounds.minX {
                x = bounds.minX
                y += rowH + spacing
                rowH = 0
            }
            sub.place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(s))
            x += s.width + spacing
            rowH = max(rowH, s.height)
        }
    }
}
