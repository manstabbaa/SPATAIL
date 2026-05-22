// PromptBarView.swift
//
// Slim header used at the top of the GeneratedExperienceView and the
// AR overlay. Displays the source prompt and the detected domain in
// a single readable strip.

import SwiftUI

struct PromptBarView: View {
    let prompt: String
    let domain: String

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("PROMPT")
                .font(.caption2).fontWeight(.semibold)
                .foregroundColor(.spatailTextDim)
                .tracking(1.0)
            Text(prompt)
                .font(.callout).fontWeight(.semibold)
                .foregroundColor(.white)
            HStack(spacing: 6) {
                Circle()
                    .fill(Color.spatailAccent)
                    .frame(width: 6, height: 6)
                Text(domain.replacingOccurrences(of: "_", with: " "))
                    .font(.caption2)
                    .foregroundColor(.spatailTextDim)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 14).padding(.vertical, 10)
        .background(Color.spatailBgElev.opacity(0.92))
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(Color.spatailBorder, lineWidth: 1))
        .clipShape(RoundedRectangle(cornerRadius: 10))
    }
}
