// SpatialPlanPreviewView.swift
//
// The list of spatial decisions before the user launches AR. This is
// where the Vision Pro design intent shows on a phone screen — the
// user sees each element, the chosen mode, the chosen placement, and
// the contract's reasoning for both — *before* the AR session starts.

import SwiftUI

struct SpatialPlanPreviewView: View {
    let decisions: [SpatialReasoningDecision]
    let attention: [AttentionLine]
    let reasoningSummary: String

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("SPATIAL PLAN  ·  \(decisions.count) elements")
                .font(.caption2).fontWeight(.semibold)
                .foregroundColor(.spatailTextDim)
                .tracking(0.8)
            ForEach(decisions, id: \.elementId) { d in
                SpatialDecisionCardView(decision: d)
            }
            attentionPlan
            summary
        }
    }

    private var attentionPlan: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("ATTENTION PLAN")
                .font(.caption2).fontWeight(.semibold)
                .foregroundColor(.spatailTextDim)
                .tracking(0.8)
            ForEach(attention) { line in
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    Text("\(line.step)")
                        .font(.system(.caption, design: .monospaced))
                        .foregroundColor(.spatailAccent2)
                        .frame(width: 16, alignment: .leading)
                    Text(line.narration)
                        .font(.callout)
                        .foregroundColor(.white)
                    Spacer()
                }
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.spatailBgElev)
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Color.spatailBorder, lineWidth: 1))
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }

    private var summary: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("REASONING SUMMARY")
                .font(.caption2).fontWeight(.semibold)
                .foregroundColor(.spatailTextDim)
                .tracking(0.8)
            Text(reasoningSummary)
                .font(.callout)
                .foregroundColor(.spatailTextDim)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.spatailBgElev)
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Color.spatailBorder, lineWidth: 1))
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }
}
