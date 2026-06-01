import SwiftUI

// The "show your space → what do you want to see → here's what fits" flow.
//
//  1. Scan      — AR view detects floor + a table; builds a RoomProfile.
//  2. Ask       — pick a demo from the catalog the studio built.
//  3. Analyze   — SPATAIL ANALYSIS proposes scale variants for YOUR room.
//  4. Place     — tap a variant; the exhibit anchors + plays.

enum Stage { case scanning, choosing, prompting, generating, analyzing, placed, experiencing }

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
    @Published var makeExperience = true        // true = full multi-station experience

    // experience loop (post-compile XR): the downloaded spec the runtime presents
    @Published var experience: DownloadedExperience?
    @Published var focusedStation = 0
    /// Bumped whenever the runtime should (re)present the current experience.
    @Published var experienceEpoch = 0
    /// Set by the view layer; lets the model drive station focus on the runtime.
    var onFocusStation: ((Int) -> Void)?

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
        experience = nil; focusedStation = 0
        genError = nil; genStage = ""; stage = .choosing
    }

    // --- generative AR ----------------------------------------------------
    func startPrompting() { genError = nil; stage = .prompting }

    func saveServer() { GenerativeClient.baseURL = serverURL }

    func generate() {
        saveServer()
        let p = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !p.isEmpty else { return }
        genError = nil; genStage = "submitting…"
        generatedURL = nil; experience = nil
        stage = .generating
        let wantExperience = makeExperience
        Task {
            do {
                if wantExperience {
                    let exp = try await gen.generateExperience(prompt: p) { [weak self] s in
                        Task { @MainActor in self?.genStage = s }
                    }
                    experience = exp
                    focusedStation = 0
                    selected = nil; chosen = nil
                    experienceEpoch += 1
                    stage = .experiencing
                } else {
                    let url = try await gen.generate(prompt: p) { [weak self] s in
                        Task { @MainActor in self?.genStage = s }
                    }
                    generatedURL = url
                    selected = nil; chosen = nil
                    stage = .placed
                }
            } catch {
                genError = error.localizedDescription
                stage = .prompting
            }
        }
    }

    // station navigation in a guided experience
    func focusStation(_ i: Int) {
        guard let exp = experience else { return }
        let n = exp.spec.stations.count
        focusedStation = min(max(i, 0), n - 1)
        onFocusStation?(focusedStation)
    }
    func nextStation() { focusStation(focusedStation + 1) }
    func prevStation() { focusStation(focusedStation - 1) }
    var stationCount: Int { experience?.spec.stations.count ?? 0 }
    var currentStation: ExperienceSpec.Station? {
        guard let exp = experience, focusedStation < exp.spec.orderedStations.count
        else { return nil }
        return exp.spec.orderedStations[focusedStation]
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
                    Text(model.makeExperience ? "What do you want to learn?"
                                              : "Describe what to create").font(.headline)
                    TextField(model.makeExperience ? "e.g. teach me how a lever works"
                                                   : "e.g. a bouncing red rubber ball",
                              text: $model.prompt, axis: .vertical)
                        .textFieldStyle(.roundedBorder)
                        .lineLimit(1...3)
                    Toggle(isOn: $model.makeExperience) {
                        Label("Build a full lesson (multi-station)", systemImage: "sparkles")
                            .font(.caption)
                    }.toggleStyle(.switch)
                    DisclosureGroup("Server") {
                        TextField("http://your-pc.tailnet:8787", text: $model.serverURL)
                            .textFieldStyle(.roundedBorder)
                            .autocorrectionDisabled()
                            .textInputAutocapitalization(.never)
                    }.font(.caption)
                    if let e = model.genError {
                        Text(e).font(.caption).foregroundStyle(.red)
                    }
                    if model.makeExperience {
                        Text("Builds an interactive lesson in your room — takes a few minutes.")
                            .font(.caption2).foregroundStyle(.secondary)
                    }
                    HStack {
                        Button("Back") { model.reset() }.font(.caption)
                        Spacer()
                        Button(model.makeExperience ? "Build lesson" : "Generate") {
                            model.generate()
                        }
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
            case .experiencing:
                card {
                    if let exp = model.experience, let st = model.currentStation {
                        HStack {
                            Text(exp.spec.title).font(.headline)
                            Spacer()
                            Text("\(model.focusedStation + 1)/\(model.stationCount)")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        Text(st.title).font(.subheadline).bold()
                        if !st.subtitle.isEmpty {
                            Text(st.subtitle).font(.caption).foregroundStyle(.secondary)
                        }
                        if !st.narration.isEmpty {
                            Text(st.narration).font(.caption)
                        }
                        HStack {
                            Button { model.prevStation() } label: {
                                Image(systemName: "chevron.left")
                            }.disabled(model.focusedStation == 0)
                            Spacer()
                            if model.focusedStation < model.stationCount - 1 {
                                Button("Next") { model.nextStation() }
                                    .buttonStyle(.borderedProminent)
                            } else {
                                Button("Finish") { model.reset() }
                                    .buttonStyle(.borderedProminent)
                            }
                            Spacer()
                            Button { model.reset() } label: { Image(systemName: "xmark") }
                        }.padding(.top, 2)
                    } else {
                        Text("Preparing experience…").font(.headline)
                    }
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
