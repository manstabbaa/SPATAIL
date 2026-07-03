// GenerationProgressView.swift — the 9-stage Meshy build progress, honest.
//
// Port of SpatailViewer's determinate progress loop (ViewerNet.awaitGeneratedUSDZ →
// bottomBar) restyled into the design system. The PC job server reports the
// asset factory's 9 stages verbatim (studio/meshy/asset_service.MESHY_STAGES:
// "imagining the object (multi-view)" … "publishing"); this view shows exactly
// what the server said — stage name, index/total, percent — plus the
// placeholder-then-swap status line, because the scene already holds a
// placeholder while the real model bakes.
//
// GenerationTracker owns the polling (1.8 s cadence, 20 min deadline, same as
// the proven viewer loop). Kick it with:
//     GenerationTracker.shared.track(jobId: contract.generationJobId, client: brainClient)
// right after a /modular contract lands with a generationJobId.

import SwiftUI

// MARK: - Tracker (poll /jobs/{id} until done/failed)

@MainActor
final class GenerationTracker: ObservableObject {

    static let shared = GenerationTracker()

    enum Phase: Equatable {
        case running
        case done
        case failed(String)
    }

    struct Progress: Equatable {
        var jobId: String
        var stage: String
        var stageIndex: Int
        var stageTotal: Int
        var percent: Int
        var phase: Phase
    }

    @Published private(set) var progress: Progress?

    private var pollTask: Task<Void, Never>?
    private static let pollInterval: UInt64 = 1_800_000_000   // 1.8 s, viewer-proven
    private static let deadline: TimeInterval = 1200          // Meshy takes minutes

    func track(jobId: String, client: BrainClient) {
        guard !jobId.isEmpty else { return }
        pollTask?.cancel()
        progress = Progress(jobId: jobId, stage: "queued on the PC brain",
                            stageIndex: 0, stageTotal: 9, percent: 0, phase: .running)

        pollTask = Task { [weak self] in
            let deadline = Date().addingTimeInterval(Self.deadline)
            while !Task.isCancelled {
                guard let self, self.progress?.jobId == jobId else { return }
                if Date() > deadline {
                    self.progress?.phase = .failed("Generation timed out after 20 minutes.")
                    return
                }
                do {
                    let wire = try await client.jobStatus(id: jobId)
                    guard !Task.isCancelled, self.progress?.jobId == jobId else { return }
                    if self.apply(wire, jobId: jobId) {
                        self.scheduleAutoDismiss(jobId: jobId)
                        return
                    }
                } catch {
                    // Transient poll failure — keep trying until the deadline;
                    // the bar simply holds its last honest state.
                }
                try? await Task.sleep(nanoseconds: Self.pollInterval)
            }
        }
    }

    func dismiss() {
        pollTask?.cancel()
        pollTask = nil
        progress = nil
    }

    /// The single seam onto W2's JobStatusWire — if its field names drift, this
    /// function is the only place to reconcile. Returns true on a terminal state.
    private func apply(_ wire: JobStatusWire, jobId: String) -> Bool {
        let total = wire.stageTotal ?? progress?.stageTotal ?? 9
        let index = wire.stageIndex ?? progress?.stageIndex ?? 0
        let percent = wire.percent
            ?? (total > 0 ? Int((Double(index) / Double(total) * 100).rounded()) : 0)
        let stage = wire.stage ?? wire.message ?? wire.status

        switch wire.status {
        case "done":
            progress = Progress(jobId: jobId, stage: "real model ready",
                                stageIndex: total, stageTotal: total,
                                percent: 100, phase: .done)
            return true
        case "error", "failed":
            progress = Progress(jobId: jobId, stage: stage,
                                stageIndex: index, stageTotal: total, percent: percent,
                                phase: .failed(wire.message ?? "Generation failed on the PC brain."))
            return true
        default:
            progress = Progress(jobId: jobId, stage: stage,
                                stageIndex: index, stageTotal: total,
                                percent: percent, phase: .running)
            return false
        }
    }

    private func scheduleAutoDismiss(jobId: String) {
        guard case .done? = progress?.phase else { return }  // failures stay until dismissed
        Task { [weak self] in
            try? await Task.sleep(nanoseconds: 4_000_000_000)
            guard let self, self.progress?.jobId == jobId,
                  case .done? = self.progress?.phase else { return }
            self.progress = nil
        }
    }
}

// MARK: - View

struct GenerationProgressView: View {
    @ObservedObject private var tracker = GenerationTracker.shared

    var body: some View {
        if let progress = tracker.progress {
            card(progress)
        }
    }

    private func card(_ p: GenerationTracker.Progress) -> some View {
        VStack(alignment: .leading, spacing: SpatailSpace.s2) {
            HStack {
                Text("BUILDING THE REAL MODEL")
                    .spatailType(.label)
                    .foregroundStyle(SpatailGlassTone.dark.eyebrow)
                Spacer()
                Text("\(min(p.stageIndex, p.stageTotal))/\(p.stageTotal)")
                    .spatailType(.micro, weight: .medium)
                    .foregroundStyle(SpatailColor.paper.opacity(0.75))
                if case .failed = p.phase {
                    SpatailIconButton(systemName: "xmark", label: "Dismiss build status",
                                      size: .sm, variant: .ghost) {
                        tracker.dismiss()
                    }
                }
            }

            ProgressView(value: Double(min(max(p.percent, 0), 100)), total: 100)
                .tint(barTint(p.phase))

            HStack(spacing: SpatailSpace.s2) {
                phaseDot(p.phase)
                Text(p.stage)
                    .spatailType(.xs, weight: .medium)
                    .foregroundStyle(SpatailColor.paper)
                    .lineLimit(1)
                Spacer()
                Text("\(p.percent)%")
                    .spatailType(.xs)
                    .foregroundStyle(SpatailColor.indigo300)
            }

            Text(statusLine(p.phase))
                .spatailType(.micro)
                .foregroundStyle(SpatailColor.paper.opacity(0.62))
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.horizontal, SpatailSpace.s4)
        .padding(.vertical, SpatailSpace.s3)
        .frame(maxWidth: .infinity, alignment: .leading)
        .spatailGlass(tone: .dark, radius: SpatailRadius.lg)
    }

    private func barTint(_ phase: GenerationTracker.Phase) -> Color {
        switch phase {
        case .running: return SpatailColor.indigo300
        case .done: return SpatailColor.statusSuccess
        case .failed: return SpatailColor.statusDanger
        }
    }

    private func phaseDot(_ phase: GenerationTracker.Phase) -> some View {
        Circle()
            .fill(barTint(phase))
            .frame(width: 6, height: 6)
    }

    /// Placeholder-then-swap, said plainly (the UI never lies about what's shown).
    private func statusLine(_ phase: GenerationTracker.Phase) -> String {
        switch phase {
        case .running:
            return "A placeholder is holding the spot — the real model swaps in when it's ready."
        case .done:
            return "Real model ready — swapping into the scene."
        case .failed(let message):
            return message
        }
    }
}
