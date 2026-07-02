import SwiftUI

// The "show your space → what do you want to see → here's what fits" flow.
//
//  1. Scan      — AR view detects floor + a table; builds a RoomProfile.
//  2. Ask       — pick a demo from the catalog the studio built.
//  3. Analyze   — SPATAIL ANALYSIS proposes scale variants for YOUR room.
//  4. Place     — tap a variant; the exhibit anchors + plays.

enum Stage { case scanning, choosing, prompting, generating, analyzing, placed, experiencing, representing, reading }

// How a prompt is realised: a full multi-station Lesson (Director), a single
// generated Object, or a Representation (the brain → progressive runtime contract).
enum GenMode: String, CaseIterable, Identifiable {
    case lesson, representation, object
    var id: String { rawValue }
    var label: String {
        switch self {
        case .lesson: return "Lesson"
        case .representation: return "Representation"
        case .object: return "Object"
        }
    }
}

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
    // determinate build progress (dev tool) — step k / N from the Meshy pipeline.
    @Published var genStageIndex = 0
    @Published var genStageTotal = 0
    @Published var genError: String?
    @Published var generatedURL: URL?
    @Published var serverURL = GenerativeClient.baseURL
    @Published var genMode: GenMode = .lesson   // lesson | representation | object

    // experience loop (post-compile XR): the downloaded spec the runtime presents
    @Published var experience: DownloadedExperience?
    @Published var focusedStation = 0
    /// Bumped whenever the runtime should (re)present the current experience.
    @Published var experienceEpoch = 0
    /// Set by the view layer; lets the model drive station focus on the runtime.
    /// @MainActor because it calls the @MainActor ExperienceRuntime; only ever
    /// invoked from focusStation() which is already main-actor isolated.
    var onFocusStation: (@MainActor (Int) -> Void)?

    // representation loop (the Representation Engine): the downloaded bundle the
    // RepresentationRuntime presents, plus beat navigation.
    @Published var representation: RepresentationBundle?
    @Published var representationEpoch = 0
    @Published var beatIndex = 0
    /// Set by the view layer; drives beat focus on the RepresentationRuntime.
    var onFocusBeat: (@MainActor (Int) -> Void)?

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
        representation = nil; beatIndex = 0
        genError = nil; genStage = ""; stage = .choosing
    }

    // --- generative AR ----------------------------------------------------
    func startPrompting() { genError = nil; stage = .prompting }

    func saveServer() { GenerativeClient.baseURL = serverURL }

    // --- Educational Wrapper ---------------------------------------------
    func startReading() { genError = nil; representation = nil; stage = .reading }

    /// A selected concept from the reader → the SPATAIL pipeline (educational mode)
    /// → presented via the existing RepresentationRuntime. No new scene logic.
    func explainSelection(_ sel: CapturedSelection) {
        saveServer()
        let text = sel.selectedText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        genError = nil; genStage = "submitting…"
        generatedURL = nil; experience = nil; representation = nil
        stage = .generating
        Task {
            do {
                let bundle = try await gen.generateEducational(
                    selectedText: text, surroundingContext: sel.surroundingContext
                ) { [weak self] s in
                    Task { @MainActor in self?.genStage = s }
                }
                representation = bundle; beatIndex = 0; selected = nil; chosen = nil
                representationEpoch += 1; stage = .representing
            } catch {
                genError = error.localizedDescription
                stage = .reading
            }
        }
    }

    func generate() {
        saveServer()
        let p = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !p.isEmpty else { return }
        genError = nil; genStage = "submitting…"
        genStageIndex = 0; genStageTotal = 0
        generatedURL = nil; experience = nil; representation = nil
        stage = .generating
        let mode = genMode
        Task {
            do {
                switch mode {
                case .lesson:
                    let exp = try await gen.generateExperience(prompt: p) { [weak self] s in
                        Task { @MainActor in self?.genStage = s }
                    }
                    experience = exp; focusedStation = 0; selected = nil; chosen = nil
                    experienceEpoch += 1; stage = .experiencing
                case .object:
                    let url = try await gen.generate(prompt: p, onProgress: { [weak self] i, t in
                        Task { @MainActor in self?.genStageIndex = i; self?.genStageTotal = t }
                    }) { [weak self] s in
                        Task { @MainActor in self?.genStage = s }
                    }
                    generatedURL = url; selected = nil; chosen = nil; stage = .placed
                case .representation:
                    let bundle = try await gen.generateRepresentation(prompt: p) { [weak self] s in
                        Task { @MainActor in self?.genStage = s }
                    }
                    representation = bundle; beatIndex = 0; selected = nil; chosen = nil
                    representationEpoch += 1; stage = .representing
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

    // beat navigation in a representation
    var beatCount: Int { representation?.contract.beats.count ?? 0 }
    var currentBeat: RuntimeSceneContract.Beat? {
        guard let beats = representation?.contract.beats, beatIndex < beats.count
        else { return nil }
        return beats[beatIndex]
    }
    func showBeat(_ i: Int) {
        let n = beatCount; guard n > 0 else { return }
        beatIndex = min(max(i, 0), n - 1)
        onFocusBeat?(beatIndex)
    }
    func nextBeat() { showBeat(beatIndex + 1) }
    func prevBeat() { showBeat(beatIndex - 1) }
}

struct ContentView: View {
    @StateObject private var model = SessionModel()

    @State private var showModular = false
    @State private var showLive = false

    var body: some View {
        if model.stage == .reading {
            ReadingView(model: model)
        } else {
            ZStack(alignment: .bottom) {
                ARContainerView(model: model).ignoresSafeArea()
                panel
            }
            .fullScreenCover(isPresented: $showModular) { ModularEntryView() }
            .fullScreenCover(isPresented: $showLive) { LiveExplainerView() }
        }
    }

    @ViewBuilder private var panel: some View {
        VStack(spacing: 12) {
            switch model.stage {
            case .scanning:
                card { Text(model.statusText).font(.callout) }
            case .reading:
                EmptyView()   // full-screen ReadingView is shown by `body`
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
                    Button {
                        model.startReading()
                    } label: {
                        HStack {
                            Image(systemName: "book")
                            Text("Read & explain (Educational)").bold()
                            Spacer(); Image(systemName: "chevron.right")
                        }
                    }.buttonStyle(.bordered)
                    Button { showModular = true } label: {
                        HStack {
                            Image(systemName: "wand.and.stars")
                            Text("Explain anything — modular (text or photo)").bold()
                            Spacer(); Image(systemName: "chevron.right")
                        }
                    }.buttonStyle(.borderedProminent)
                    Button { showLive = true } label: {
                        HStack {
                            Image(systemName: "viewfinder")
                            Text("Live Explainer — point at a real object").bold()
                            Spacer(); Image(systemName: "chevron.right")
                        }
                    }.buttonStyle(.borderedProminent).tint(.teal)
                    // Built-in demos temporarily hidden from the app (Catalog.swift,
                    // Resources/*.usdz, and model.catalog are all kept — restore this
                    // ForEach over model.catalog to bring them back).
                }
            case .prompting:
                card {
                    Text(promptTitle).font(.headline)
                    TextField(promptPlaceholder, text: $model.prompt, axis: .vertical)
                        .textFieldStyle(.roundedBorder)
                        .lineLimit(1...3)
                    Picker("Mode", selection: $model.genMode) {
                        ForEach(GenMode.allCases) { m in Text(m.label).tag(m) }
                    }.pickerStyle(.segmented)
                    DisclosureGroup("Server") {
                        TextField("http://your-pc.tailnet:8787", text: $model.serverURL)
                            .textFieldStyle(.roundedBorder)
                            .autocorrectionDisabled()
                            .textInputAutocapitalization(.never)
                    }.font(.caption)
                    if let e = model.genError {
                        Text(e).font(.caption).foregroundStyle(.red)
                    }
                    Text(promptNote).font(.caption2).foregroundStyle(.secondary)
                    HStack {
                        Button("Back") { model.reset() }.font(.caption)
                        Spacer()
                        Button(buildLabel) { model.generate() }
                        .buttonStyle(.borderedProminent)
                        .disabled(model.prompt.trimmingCharacters(in: .whitespaces).isEmpty)
                    }
                }
            case .generating:
                card {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Generating…").font(.headline)
                        if model.genStageTotal > 0 {
                            // determinate build bar (dev tool): step k / N
                            ProgressView(value: Double(model.genStageIndex),
                                         total: Double(model.genStageTotal))
                            Text("\(model.genStageIndex)/\(model.genStageTotal) · \(model.genStage)")
                                .font(.caption).foregroundStyle(.secondary)
                        } else {
                            HStack(spacing: 10) {
                                ProgressView()
                                Text(model.genStage.isEmpty ? "Preparing…" : model.genStage)
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                        }
                        Text("“\(model.prompt)”").font(.caption2).foregroundStyle(.secondary)
                    }
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
            case .representing:
                card {
                    if let rep = model.representation, let beat = model.currentBeat {
                        HStack {
                            Text(rep.contract.title).font(.headline)
                            Spacer()
                            Text("\(model.beatIndex + 1)/\(model.beatCount)")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        Text("\(rep.contract.representation.domain) · \(rep.contract.representation.intent) · \(rep.contract.representation.strategy)")
                            .font(.caption2).foregroundStyle(.secondary)
                        Text(beat.title).font(.subheadline).bold()
                        Text(beat.narration).font(.caption)
                        HStack {
                            Button { model.prevBeat() } label: { Image(systemName: "chevron.left") }
                                .disabled(model.beatIndex == 0)
                            Spacer()
                            if model.beatIndex < model.beatCount - 1 {
                                Button("Next") { model.nextBeat() }.buttonStyle(.borderedProminent)
                            } else {
                                Button("Finish") { model.reset() }.buttonStyle(.borderedProminent)
                            }
                            Spacer()
                            Button { model.reset() } label: { Image(systemName: "xmark") }
                        }.padding(.top, 2)
                        Text("Tap the model to explode / isolate parts.")
                            .font(.caption2).foregroundStyle(.secondary)
                    } else {
                        Text(model.genStage.isEmpty ? "Preparing…" : model.genStage).font(.headline)
                    }
                }
            }
        }
        .padding()
    }

    private var promptTitle: String {
        switch model.genMode {
        case .lesson: return "What do you want to learn?"
        case .representation: return "What should I represent?"
        case .object: return "Describe what to create"
        }
    }
    private var promptPlaceholder: String {
        switch model.genMode {
        case .lesson: return "e.g. teach me how a lever works"
        case .representation: return "e.g. explain a V8 engine on my table"
        case .object: return "e.g. a bouncing red rubber ball"
        }
    }
    private var promptNote: String {
        switch model.genMode {
        case .lesson: return "Builds an interactive lesson in your room — takes a few minutes."
        case .representation: return "Plans the spatial experience and shows it progressively — fast, no waiting on Blender."
        case .object: return "Generates a single animated object."
        }
    }
    private var buildLabel: String {
        switch model.genMode {
        case .lesson: return "Build lesson"
        case .representation: return "Represent"
        case .object: return "Generate"
        }
    }

    private func card<C: View>(@ViewBuilder _ c: () -> C) -> some View {
        VStack(alignment: .leading, spacing: 8, content: c)
            .padding()
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 16))
    }
}
