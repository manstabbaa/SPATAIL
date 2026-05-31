import SwiftUI

// The "show your space → what do you want to see → here's what fits" flow.
//
//  1. Scan      — AR view detects floor + a table; builds a RoomProfile.
//  2. Ask       — pick a demo from the catalog the studio built.
//  3. Analyze   — SPATAIL ANALYSIS proposes scale variants for YOUR room.
//  4. Place     — tap a variant; the exhibit anchors + plays.

enum Stage { case scanning, choosing, prompting, generating, analyzing, placed }

@MainActor
final class SessionModel: ObservableObject {
    @Published var stage: Stage = .scanning
    @Published var room = RoomProfile()
    @Published var catalog: [CatalogEntry] = Catalog.load()
    @Published var selected: CatalogEntry?
    @Published var variants: [ScaleVariant] = []
    @Published var chosen: ScaleVariant?
    @Published var statusText = "Move your phone slowly to scan the space…"

    // generative loop
    @Published var prompt = ""
    @Published var genStage = ""
    @Published var genError: String?
    @Published var generatedURL: URL?
    @Published var serverURL = GenerativeClient.baseURL
    private let gen = GenerativeClient()

    func roomScanned(_ r: RoomProfile) {
        room = r
        statusText = String(format: "Found ~%.1f×%.1f m of space%@.",
                            r.floorClearW, r.floorClearD,
                            r.tablePresent ? " and a table" : "")
        if stage == .scanning { stage = .choosing }
    }

    func pick(_ e: CatalogEntry) {
        selected = e
        variants = SpatailAnalysis.variants(footprintW: e.footprint.w,
                                            depth: e.footprint.d,
                                            height: e.footprint.h, room: room)
        stage = .analyzing
    }

    func choose(_ v: ScaleVariant) { chosen = v; stage = .placed }
    func reset() {
        selected = nil; chosen = nil; variants = []; generatedURL = nil
        genError = nil; genStage = ""; stage = .choosing
    }

    // --- generative AR ----------------------------------------------------
    func startPrompting() { genError = nil; stage = .prompting }

    func saveServer() { GenerativeClient.baseURL = serverURL }

    func generate() {
        saveServer()
        let p = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !p.isEmpty else { return }
        genError = nil; genStage = "submitting…"; generatedURL = nil
        stage = .generating
        Task {
            do {
                let url = try await gen.generate(prompt: p) { [weak self] s in
                    Task { @MainActor in self?.genStage = s }
                }
                generatedURL = url
                selected = nil; chosen = nil
                stage = .placed
            } catch {
                genError = error.localizedDescription
                stage = .prompting
            }
        }
    }
}

struct ContentView: View {
    @StateObject private var model = SessionModel()

    var body: some View {
        ZStack(alignment: .bottom) {
            ARContainerView(model: model).ignoresSafeArea()
            panel
        }
    }

    @ViewBuilder private var panel: some View {
        VStack(spacing: 12) {
            switch model.stage {
            case .scanning:
                card { Text(model.statusText).font(.callout) }
            case .choosing:
                card {
                    Text("What do you want to see?").font(.headline)
                    Text(model.statusText).font(.caption).foregroundStyle(.secondary)
                    Button {
                        model.startPrompting()
                    } label: {
                        HStack {
                            Image(systemName: "sparkles")
                            Text("Generate something new…").bold()
                            Spacer(); Image(systemName: "chevron.right")
                        }
                    }.buttonStyle(.borderedProminent)
                    if !model.catalog.isEmpty {
                        Text("or pick a built-in demo")
                            .font(.caption2).foregroundStyle(.secondary)
                    }
                    ForEach(model.catalog) { e in
                        Button { model.pick(e) } label: {
                            HStack {
                                VStack(alignment: .leading) {
                                    Text(e.title).font(.subheadline).bold()
                                    Text(e.subtitle).font(.caption).foregroundStyle(.secondary)
                                }
                                Spacer(); Image(systemName: "chevron.right")
                            }
                        }.buttonStyle(.bordered)
                    }
                }
            case .prompting:
                card {
                    Text("Describe what to create").font(.headline)
                    TextField("e.g. a bouncing red rubber ball", text: $model.prompt,
                              axis: .vertical)
                        .textFieldStyle(.roundedBorder)
                        .lineLimit(1...3)
                    DisclosureGroup("Server") {
                        TextField("http://your-pc.tailnet:8787", text: $model.serverURL)
                            .textFieldStyle(.roundedBorder)
                            .autocorrectionDisabled()
                            .textInputAutocapitalization(.never)
                    }.font(.caption)
                    if let e = model.genError {
                        Text(e).font(.caption).foregroundStyle(.red)
                    }
                    HStack {
                        Button("Back") { model.reset() }.font(.caption)
                        Spacer()
                        Button("Generate") { model.generate() }
                            .buttonStyle(.borderedProminent)
                            .disabled(model.prompt.trimmingCharacters(in: .whitespaces).isEmpty)
                    }
                }
            case .generating:
                card {
                    HStack(spacing: 10) {
                        ProgressView()
                        VStack(alignment: .leading) {
                            Text("Generating…").font(.headline)
                            Text(model.genStage).font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    Text("“\(model.prompt)”").font(.caption2).foregroundStyle(.secondary)
                }
            case .analyzing:
                card {
                    Text("SPATAIL Analysis").font(.headline)
                    Text("How should it appear in your room?")
                        .font(.caption).foregroundStyle(.secondary)
                    ForEach(model.variants) { v in
                        Button { model.choose(v) } label: {
                            VStack(alignment: .leading, spacing: 2) {
                                HStack {
                                    Text(v.name.capitalized).bold()
                                    if !v.fits { Text("tight").font(.caption2)
                                        .padding(.horizontal, 6).background(.yellow.opacity(0.3))
                                        .clipShape(Capsule()) }
                                    Spacer()
                                    Text(String(format: "×%.2f", v.scale))
                                        .font(.caption).foregroundStyle(.secondary)
                                }
                                Text(v.reason).font(.caption2).foregroundStyle(.secondary)
                            }
                        }.buttonStyle(.bordered).tint(v.fits ? .accentColor : .orange)
                    }
                    Button("Back") { model.reset() }.font(.caption)
                }
            case .placed:
                card {
                    if model.generatedURL != nil {
                        Text("Generated").font(.headline)
                        Text("“\(model.prompt)”").font(.caption).foregroundStyle(.secondary)
                    } else {
                        Text(model.selected?.title ?? "").font(.headline)
                        Text(model.selected?.narration ?? "").font(.caption)
                    }
                    Button("Show something else") { model.reset() }
                        .buttonStyle(.borderedProminent)
                }
            }
        }
        .padding()
    }

    private func card<C: View>(@ViewBuilder _ c: () -> C) -> some View {
        VStack(alignment: .leading, spacing: 8, content: c)
            .padding()
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 16))
    }
}
