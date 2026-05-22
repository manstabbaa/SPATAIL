// ContentCardInputView.swift
//
// Renders the source card that produced the contract — the "what came
// in" half of the SPATAIL story. For v1 we read the source content
// straight out of the contract (sourceInputs + the per-element
// sourceContent) since we don't have the original card JSON on
// device; the displayed summary is enough to make the input legible
// before the user launches AR.

import SwiftUI

struct ContentCardInputView: View {
    let contract: SpatialExperienceContract

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("SOURCE CONTENT")
                .font(.caption2).fontWeight(.semibold)
                .foregroundColor(.spatailTextDim)
                .tracking(0.8)
            ForEach(Array(contract.sourceInputs.enumerated()), id: \.offset) { _, input in
                HStack(alignment: .top, spacing: 8) {
                    Text(input.kind)
                        .font(.system(.caption2, design: .monospaced))
                        .foregroundColor(.spatailAccent)
                        .padding(.horizontal, 6).padding(.vertical, 1)
                        .background(Color.spatailAccent.opacity(0.10))
                        .clipShape(Capsule())
                    Text(input.title ?? input.key ?? "—")
                        .font(.callout)
                        .foregroundColor(.white)
                    Spacer()
                }
                Divider().overlay(Color.spatailBorder)
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.spatailBgElev)
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Color.spatailBorder, lineWidth: 1))
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }
}
