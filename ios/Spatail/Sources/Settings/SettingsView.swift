import SwiftUI

// SettingsView — the brain endpoints + honest link truth, design-system styled.
// Presented as a sheet from RootView. The connection readouts show exactly what
// the uplink reports (spec §0 law 3: the UI never lies about streaming).

struct SettingsView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var settings: SettingsStore
    @EnvironmentObject private var uplink: VisionUplink
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: SpatailSpace.s5) {
                header
                endpointsCard
                statusCard
                vlmNoteCard
            }
            .padding(SpatailSpace.s4)
        }
        .background(SpatailColor.surfaceSunken.ignoresSafeArea())
    }

    // MARK: Header

    private var header: some View {
        HStack(alignment: .center) {
            VStack(alignment: .leading, spacing: SpatailSpace.s1) {
                Text("SPATAIL").spatailType(.label).foregroundStyle(SpatailColor.textMuted)
                Text("Settings").spatailType(.h3).foregroundStyle(SpatailColor.textStrong)
            }
            Spacer()
            SpatailIconButton(systemName: "xmark", label: "Close", variant: .outline) {
                dismiss()
            }
        }
    }

    // MARK: Endpoints

    private var endpointsCard: some View {
        SettingsCard(eyebrow: "PC brain", title: "Endpoints") {
            VStack(alignment: .leading, spacing: SpatailSpace.s4) {
                EndpointField(label: "Job server (HTTP)",
                              placeholder: SettingsStore.defaultJobServerURL,
                              text: $settings.jobServerURLString)
                EndpointField(label: "Vision socket (WS)",
                              placeholder: SettingsStore.defaultVisionSocketURL,
                              text: $settings.visionSocketURLString)

                Text("Use the PC's Tailscale MagicDNS name (spatail-pc or pc-name.<tailnet>.ts.net) or its 100.x tailnet IP. Job server serves /modular, /jobs and assets; the vision socket carries live frames and identifications.")
                    .spatailType(.xs)
                    .foregroundStyle(SpatailColor.textMuted)
                    .fixedSize(horizontal: false, vertical: true)

                if let problem = settings.endpointsProblem {
                    HStack(spacing: SpatailSpace.s2) {
                        Image(systemName: "exclamationmark.triangle.fill")
                        Text(problem).spatailType(.sm)
                    }
                    .foregroundStyle(SpatailColor.statusDanger)
                }

                HStack(spacing: SpatailSpace.s2) {
                    SpatailButton(title: "Connect", variant: .primary,
                                  iconLeft: "bolt.fill",
                                  disabled: settings.endpoints == nil) {
                        model.connectBrain()
                    }
                    SpatailButton(title: "Disconnect", variant: .secondary) {
                        model.disconnectBrain()
                    }
                    Spacer()
                    SpatailButton(title: "Reset", variant: .ghost) {
                        settings.resetToDefaults()
                    }
                }
            }
        }
    }

    // MARK: Link status

    private var statusCard: some View {
        SettingsCard(eyebrow: "Live link", title: "Connection") {
            VStack(alignment: .leading, spacing: SpatailSpace.s3) {
                StatusRow(label: "Vision socket") { linkBadge }
                StatusRow(label: "Room sent this connection") {
                    SpatailBadge(text: uplink.hasSentRoomThisConnection ? "Yes" : "Not yet",
                                 tone: uplink.hasSentRoomThisConnection ? .success : .outline)
                }
                StatusRow(label: "Identity feed") {
                    SpatailBadge(text: uplink.lastIdentification == nil ? "—" : "Receiving",
                                 tone: uplink.lastIdentification == nil ? .outline : .success)
                }
                StatusRow(label: "Experience feed") {
                    SpatailBadge(text: uplink.lastExperienceDelta == nil ? "—" : "Receiving",
                                 tone: uplink.lastExperienceDelta == nil ? .outline : .success)
                }
            }
        }
    }

    private var linkBadge: SpatailBadge {
        switch uplink.state {
        case .idle:
            return SpatailBadge(text: "Idle", tone: .outline)
        case .connecting:
            return SpatailBadge(text: "Connecting…", tone: .warning)
        case .streaming:
            return SpatailBadge(text: "Streaming", tone: .success)
        case .failed(let n):
            return SpatailBadge(text: "Failed (\(n) consecutive)", tone: .danger)
        }
    }

    // MARK: VLM source note

    private var vlmNoteCard: some View {
        SettingsCard(eyebrow: "How it sees", title: "VLM source") {
            Text("Identification runs on the PC brain (Qwen2.5-VL via Ollama, NIM fallback — spatail_vision_engine.py). The phone streams ~3 fps JPEG at 960 px; ARKit owns pose and form at 60 Hz on-device. Identity may be ~1 s stale — geometry never is.")
                .spatailType(.sm)
                .foregroundStyle(SpatailColor.textBody)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

// MARK: - Building blocks

/// A paper card with eyebrow + title, the sheet-side sibling of GlassPanel
/// (glass floats over the scene; settings sit on a sunken page).
private struct SettingsCard<Content: View>: View {
    var eyebrow: String
    var title: String
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(eyebrow.uppercased())
                .spatailType(.label)
                .foregroundStyle(SpatailColor.textMuted)
                .padding(.bottom, SpatailSpace.s1)
            Text(title)
                .spatailType(.title)
                .foregroundStyle(SpatailColor.textStrong)
                .padding(.bottom, SpatailSpace.s3)
            content
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(SpatailSpace.s4)
        .background(Squircle(SpatailRadius.lg).fill(SpatailColor.surfaceCard))
        .overlay(Squircle(SpatailRadius.lg).strokeBorder(SpatailColor.borderHairline, lineWidth: 1))
        .spatailShadow(.s2)
    }
}

private struct EndpointField: View {
    let label: String
    let placeholder: String
    @Binding var text: String

    var body: some View {
        VStack(alignment: .leading, spacing: SpatailSpace.s1) {
            Text(label.uppercased())
                .spatailType(.label)
                .foregroundStyle(SpatailColor.textMuted)
            TextField(placeholder, text: $text)
                .spatailType(.base)
                .foregroundStyle(SpatailColor.textStrong)
                .keyboardType(.URL)
                .autocorrectionDisabled()
                .textInputAutocapitalization(.never)
                .padding(.horizontal, SpatailSpace.s3)
                .frame(height: 44)
                .background(Squircle(SpatailRadius.md).fill(SpatailColor.paper))
                .overlay(Squircle(SpatailRadius.md).strokeBorder(SpatailColor.borderDefault, lineWidth: 1))
        }
    }
}

private struct StatusRow<Trailing: View>: View {
    let label: String
    @ViewBuilder var trailing: Trailing

    var body: some View {
        HStack {
            Text(label)
                .spatailType(.sm)
                .foregroundStyle(SpatailColor.textBody)
            Spacer()
            trailing
        }
    }
}
