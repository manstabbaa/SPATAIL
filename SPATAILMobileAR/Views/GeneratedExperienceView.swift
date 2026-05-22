// GeneratedExperienceView.swift
//
// The bridge screen between picking a demo and entering AR.
// Shows: the prompt, the source card, the full spatial plan with
// reasoning, the attention plan, and a single "Launch Spatial
// Preview" button that hands the contract to the AR container.
//
// Design principle: minimal phone UI. This screen is a launchpad —
// the experience itself happens in AR.

import SwiftUI

struct GeneratedExperienceView: View {
    let contractRef: BundledContractRef
    @EnvironmentObject private var env: AppEnvironment

    @State private var contract: SpatialExperienceContract?
    @State private var loadError: String?
    @State private var goToAR = false

    var body: some View {
        ZStack {
            Color.spatailBg.ignoresSafeArea()
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    if let c = contract {
                        PromptBarView(prompt: c.sourcePrompt,
                                      domain: c.detectedDomain.name)
                        ContentCardInputView(contract: c)
                        SpatialPlanPreviewView(
                            decisions: env.explanation.decisions(for: c),
                            attention: env.explanation.attentionLines(for: c),
                            reasoningSummary: c.reasoningSummary,
                        )
                        launchButton
                    } else if let err = loadError {
                        Text(err).foregroundColor(.red)
                    } else {
                        ProgressView().tint(.white).padding()
                    }
                }
                .padding(.horizontal, 16).padding(.vertical, 12)
            }
        }
        .navigationTitle(contractRef.title)
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(Color.spatailBgElev, for: .navigationBar)
        .task { await load() }
        .navigationDestination(isPresented: $goToAR) {
            if let c = contract {
                ARExperienceContainerView(contract: c)
            }
        }
    }

    private var launchButton: some View {
        Button {
            goToAR = true
        } label: {
            HStack {
                Image(systemName: "arkit")
                Text("Launch Spatial Preview")
                    .fontWeight(.semibold)
                Spacer()
                Image(systemName: "arrow.right")
            }
            .padding(.horizontal, 16).padding(.vertical, 14)
            .foregroundColor(.white)
            .background(
                LinearGradient(
                    colors: [.spatailAccent, .spatailAccent2],
                    startPoint: .leading, endPoint: .trailing),
            )
            .clipShape(RoundedRectangle(cornerRadius: 12))
        }
        .padding(.top, 4)
    }

    private func load() async {
        do {
            contract = try env.planner.plan(forContractId: contractRef.id)
        } catch {
            loadError = error.localizedDescription
        }
    }
}
