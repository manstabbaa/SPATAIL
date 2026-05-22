// ARExperienceContainerView.swift
//
// SwiftUI wrapper around the ARKit / RealityKit experience. The actual
// AR plumbing lives in ARExperienceCoordinator (a UIViewRepresentable);
// this view owns the on-phone overlay: a coaching-style hint before
// placement and a minimal control strip after placement.
//
// Design rule: phone overlay is tiny. The experience happens in AR.

import SwiftUI

struct ARExperienceContainerView: View {
    let contract: SpatialExperienceContract

    @StateObject private var sceneController = ARSceneController()

    var body: some View {
        ZStack(alignment: .top) {
            ARExperienceCoordinator(
                contract: contract,
                controller: sceneController,
            )
            .ignoresSafeArea()

            VStack {
                topOverlay
                Spacer()
                if sceneController.isPlaced {
                    bottomControls
                } else {
                    placementHint
                }
            }
            .padding(.horizontal, 16).padding(.vertical, 12)
        }
        .navigationBarBackButtonHidden(false)
        .navigationTitle("Spatial Preview")
        .navigationBarTitleDisplayMode(.inline)
    }

    private var topOverlay: some View {
        VStack(alignment: .leading, spacing: 6) {
            PromptBarView(prompt: contract.sourcePrompt,
                          domain: contract.detectedDomain.name)
            if sceneController.isPlaced,
               let line = sceneController.currentAttentionLine {
                HStack(spacing: 8) {
                    Image(systemName: "eye")
                        .foregroundColor(.spatailAccent)
                    Text("Step \(line.step):  \(line.narration)")
                        .font(.callout)
                        .foregroundColor(.white)
                    Spacer()
                }
                .padding(.horizontal, 12).padding(.vertical, 8)
                .background(Color.spatailBgElev.opacity(0.92))
                .overlay(RoundedRectangle(cornerRadius: 10).stroke(Color.spatailBorder, lineWidth: 1))
                .clipShape(RoundedRectangle(cornerRadius: 10))
            }
        }
    }

    private var placementHint: some View {
        HStack(spacing: 10) {
            Image(systemName: "hand.tap")
                .foregroundColor(.spatailAccent)
            VStack(alignment: .leading, spacing: 2) {
                Text("Find a horizontal surface")
                    .font(.callout).fontWeight(.semibold)
                    .foregroundColor(.white)
                Text("Tap to place the spatial experience. Designed as a Vision Pro scene — viewed through your iPhone.")
                    .font(.caption)
                    .foregroundColor(.spatailTextDim)
            }
            Spacer()
        }
        .padding(.horizontal, 14).padding(.vertical, 12)
        .background(Color.spatailBgElev.opacity(0.92))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Color.spatailBorder, lineWidth: 1))
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }

    private var bottomControls: some View {
        HStack(spacing: 10) {
            ControlButton(label: "Reset", system: "arrow.counterclockwise") {
                sceneController.reset()
            }
            ControlButton(label: "Next", system: "arrow.right") {
                sceneController.advanceAttention()
            }
            ControlButton(label: sceneController.isExploded ? "Collapse" : "Explode",
                          system: "rectangle.expand.vertical") {
                sceneController.toggleExplode()
            }
            ControlButton(label: sceneController.isHighlighted ? "Unhighlight" : "Highlight",
                          system: "sparkles") {
                sceneController.toggleHighlight()
            }
        }
    }
}

private struct ControlButton: View {
    let label: String
    let system: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(spacing: 3) {
                Image(systemName: system)
                Text(label)
                    .font(.caption2).fontWeight(.semibold)
            }
            .foregroundColor(.white)
            .padding(.vertical, 10)
            .frame(maxWidth: .infinity)
            .background(Color.spatailBgElev.opacity(0.92))
            .overlay(RoundedRectangle(cornerRadius: 10).stroke(Color.spatailBorder, lineWidth: 1))
            .clipShape(RoundedRectangle(cornerRadius: 10))
        }
    }
}
