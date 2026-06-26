// LivePerceptionView.swift
//
// The screen that runs the live perception → placement pipeline. Hosts the
// LivePerceptionCoordinator (AR) under a debug overlay and a minimal brand
// control strip (intent presets, mock ↔ real detector, debug toggle).
//
// Default intent is the test scenario from the brief:
//   "Highlight the detected object and place an explanation next to it."
// With the mock detector that yields: a blue holographic highlight on the
// resolved world point, a floating "Detected Object" label above it, and an
// explanation panel beside it.

import SwiftUI

/// Route value so DemoSelectorView can navigate here without a contract.
struct LivePerceptionRoute: Hashable {}

struct LivePerceptionView: View {
    @StateObject private var debug = PerceptionDebugModel()

    @State private var intent: String = LivePerceptionView.defaultIntent
    @State private var source: DetectionSource = .mock

    static let defaultIntent = "Highlight the detected object and place an explanation next to it."

    private static let presets: [String] = [
        "Highlight the detected object and place an explanation next to it.",
        "Label what you detect.",
        "Explain this object.",
        "Place a model next to it.",
    ]

    var body: some View {
        ZStack(alignment: .bottom) {
            LivePerceptionCoordinator(intent: intent, source: source, debug: debug)
                .ignoresSafeArea()

            if debug.enabled {
                PerceptionDebugOverlay(model: debug)
                    .ignoresSafeArea()
            }

            controlStrip
        }
        .navigationTitle("Live Perception")
        .navigationBarTitleDisplayMode(.inline)
        .onAppear { debug.intent = intent }
        .onChange(of: intent) { debug.intent = $0 }
    }

    private var controlStrip: some View {
        VStack(spacing: 10) {
            // Intent presets.
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    ForEach(Self.presets, id: \.self) { preset in
                        Button { intent = preset } label: {
                            Text(preset)
                                .font(.caption2).fontWeight(.medium)
                                .lineLimit(1)
                                .padding(.horizontal, 10).padding(.vertical, 7)
                                .background(intent == preset ? Color.spatailAccent.opacity(0.25) : Color.spatailBgElev.opacity(0.9))
                                .foregroundColor(intent == preset ? .spatailAccent : .white)
                                .overlay(Capsule().stroke(Color.spatailBorder, lineWidth: 1))
                                .clipShape(Capsule())
                        }
                    }
                }
                .padding(.horizontal, 12)
            }

            HStack(spacing: 12) {
                // Mock ↔ real detector switch.
                Picker("Detector", selection: $source) {
                    ForEach(DetectionSource.allCases) { s in
                        Text(s.displayName).tag(s)
                    }
                }
                .pickerStyle(.segmented)
                .frame(maxWidth: 200)

                Spacer()

                Toggle(isOn: $debug.enabled) {
                    Image(systemName: "ladybug")
                }
                .toggleStyle(.button)
                .tint(.spatailAccent)
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 8)
            .background(Color.spatailBg.opacity(0.7))
        }
        .padding(.bottom, 8)
    }
}
