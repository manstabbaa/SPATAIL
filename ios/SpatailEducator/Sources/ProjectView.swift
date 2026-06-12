import SwiftUI
#if os(iOS)

// The single CANVAS: a live camera/AR feed + a persistent prompt bar. The FIRST
// prompt lazily creates a project and builds the scene; every prompt after EVOLVES
// it (regenerate-from-prior, approach B; explicit edit-ops are the next step).
// One ARView for the whole app (Home hosts this directly — no second AR session).

@MainActor
final class ProjectSession: ObservableObject {
    @Published var conversation: [ConversationTurn] = []
    @Published var experience: ModularExperience?
    /// Bumped on every new contract — experienceId is a stable subject slug, so the
    /// AR host re-presents on epoch, not id (same-subject re-prompts must re-place).
    @Published var experienceEpoch = 0
    /// Bumped by the reposition button — re-place the scene against the live room.
    @Published var repositionEpoch = 0
    @Published var streamModel: StreamPayload?
    @Published var status = ""
    @Published var busy = false
    @Published private(set) var title = ""
    /// The stream choice: overlay the REAL object in front of you (object
    /// tracking) instead of placing the experience in free space.
    @Published var trackObject = false

    private var meta: ProjectMeta?
    private let store: ProjectStore
    private let client = GenerativeClient()

    init(store: ProjectStore, meta: ProjectMeta? = nil) {
        self.store = store; self.meta = meta; self.title = meta?.title ?? ""
    }

    func load() {
        guard let m = meta else { return }
        conversation = store.loadConversation(m.id)
        if let data = store.loadContract(m.id),
           let exp = try? JSONDecoder().decode(ModularExperience.self, from: data) {
            experience = exp
            // no epoch bump here: load() re-fires from onAppear (e.g. returning from
            // the Asset Factory cover) and must not tear down the live scene; a fresh
            // coordinator (presentedEpoch -1) presents a loaded project at any epoch
            if title.isEmpty { title = exp.title }
        }
    }

    private var priorContext: (subject: String, summary: String)? {
        guard let e = experience else { return nil }
        let subj = e.understanding.subject.isEmpty ? e.title : e.understanding.subject
        let sum = e.understanding.summary.isEmpty ? e.title : e.understanding.summary
        return (subj, sum)
    }

    func send(_ prompt: String) {
        let p = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !p.isEmpty, !busy else { return }
        if meta == nil { meta = store.create(title: "", firstPrompt: p) }   // lazy create
        guard let pid = meta?.id else { return }
        let isFirst = experience == nil
        conversation.append(ConversationTurn(role: "user", text: p))
        store.saveConversation(conversation, for: pid)
        busy = true; status = isFirst ? "creating…" : "evolving the scene…"
        let prior = priorContext
        let tracked = trackObject
        Task {
            do {
                // Tracked stream: pick a served .referenceobject whose subjects
                // match the prompt (if the library has one trained for it).
                var trackableId = "", trackableUrl = ""
                if tracked {
                    let words = Set(p.lowercased().split(separator: " ").map(String.init))
                    if let match = ((try? await client.fetchTrackables()) ?? []).first(where: { t in
                        t.subjects.contains { subj in
                            !Set(subj.lowercased().split(separator: " ").map(String.init)).isDisjoint(with: words)
                        }
                    }) { trackableId = match.id; trackableUrl = match.url }
                }
                let result = try await client.generateProject(
                    text: p, prior: prior, tracked: tracked,
                    trackedObjectId: trackableId, referenceObjectUrl: trackableUrl)
                experience = result.experience
                experienceEpoch += 1
                store.saveContract(result.raw, for: pid)
                if var m = meta {
                    m.updatedAt = Date()
                    if m.title.isEmpty || isFirst { m.title = result.experience.title }
                    meta = m; title = m.title; store.saveMeta(m)
                }
                conversation.append(ConversationTurn(role: "assistant",
                    text: "Updated — \(result.experience.title) · \(result.experience.sequence.count) steps"))
                store.saveConversation(conversation, for: pid)
                busy = false; status = "ready — tap to step, or keep prompting"
                if let jid = result.experience.generationJobId, !jid.isEmpty {
                    status = "building a real model…"
                    await streamGenerated(jid, result.experience, pid)
                }
            } catch {
                busy = false; status = "error: \(error.localizedDescription)"
                conversation.append(ConversationTurn(role: "assistant",
                    text: "Couldn't do that: \(error.localizedDescription)"))
                store.saveConversation(conversation, for: pid)
            }
        }
    }

    private func streamGenerated(_ jobId: String, _ exp: ModularExperience, _ pid: String) async {
        do {
            let url = try await client.awaitGeneratedModel(jobId: jobId) { [weak self] s in
                Task { @MainActor in self?.status = "building: \(s)" }
            }
            let target = exp.assets.first(where: { $0.role == "primary_object" })?.id
                       ?? exp.assets.first?.id ?? ""
            if let cached = store.cacheAsset(url, assetId: target, projectId: pid) {
                if var m = meta { m.cachedAssets[target] = cached.lastPathComponent; meta = m; store.saveMeta(m) }
                streamModel = StreamPayload(assetId: target, url: cached)
            } else {
                streamModel = StreamPayload(assetId: target, url: url)
            }
            status = "real model ready"
        } catch { status = "model build failed: \(error.localizedDescription)" }
    }

    func restreamCached() {
        guard let m = meta else { return }
        for (assetId, file) in m.cachedAssets {
            let u = store.assetsDir(m.id).appendingPathComponent(file)
            if FileManager.default.fileExists(atPath: u.path) {
                streamModel = StreamPayload(assetId: assetId, url: u)
            }
        }
    }
}

struct CanvasView: View {
    @ObservedObject var store: ProjectStore
    let meta: ProjectMeta?
    var initialPrompt: String?
    let onOpenProjects: () -> Void
    @StateObject private var session: ProjectSession
    @State private var input = ""

    init(store: ProjectStore, meta: ProjectMeta?, initialPrompt: String? = nil,
         onOpenProjects: @escaping () -> Void) {
        self.store = store; self.meta = meta; self.initialPrompt = initialPrompt
        self.onOpenProjects = onOpenProjects
        _session = StateObject(wrappedValue: ProjectSession(store: store, meta: meta))
    }

    var body: some View {
        ZStack(alignment: .bottom) {
            ModularARView(experience: session.experience, epoch: session.experienceEpoch,
                          streamModel: session.streamModel,
                          repositionEpoch: session.repositionEpoch,
                          onStatus: { session.status = $0 })
                .ignoresSafeArea()
            chat
        }
        .overlay(alignment: .topLeading) {
            Button(action: onOpenProjects) {
                Image(systemName: "square.stack.3d.up.fill").font(.title2)
                    .padding(10).background(.ultraThinMaterial, in: Circle())
            }.padding()
        }
        .overlay(alignment: .top) {
            if !session.title.isEmpty {
                Text(session.title).font(.headline).foregroundStyle(.white)
                    .padding(.horizontal, 12).padding(.vertical, 6)
                    .background(.ultraThinMaterial, in: Capsule()).padding(.top, 6)
            }
        }
        .overlay(alignment: .topTrailing) {
            // §7 comfort: recentering when placement feels awkward — rescan the live
            // room and re-place the scene (solver against the current RoomModel).
            if session.experience != nil {
                Button { session.repositionEpoch += 1 } label: {
                    Image(systemName: "arrow.triangle.2.circlepath")
                        .font(.title2)
                        .padding(10).background(.ultraThinMaterial, in: Circle())
                }.padding()
            }
        }
        .onAppear {
            session.load()
            if let p = initialPrompt, session.conversation.isEmpty {
                session.send(p)
            } else {
                DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) { session.restreamCached() }
            }
        }
    }

    private var chat: some View {
        VStack(spacing: 8) {
            if !session.status.isEmpty {
                HStack(spacing: 6) {
                    if session.busy { ProgressView().tint(.white) }
                    Text(session.status).font(.caption).foregroundStyle(.white)
                }.padding(8).background(.ultraThinMaterial, in: Capsule())
            }
            if !session.conversation.isEmpty {
                ScrollView {
                    VStack(alignment: .leading, spacing: 6) {
                        ForEach(session.conversation.suffix(6)) { t in
                            Text(t.text).font(.caption)
                                .foregroundStyle(t.role == "user" ? .white : .white.opacity(0.75))
                                .frame(maxWidth: .infinity,
                                       alignment: t.role == "user" ? .trailing : .leading)
                        }
                    }
                }.frame(maxHeight: 110)
            }
            HStack(spacing: 10) {
                // Stream choice: viewfinder ON = overlay the real object (tracked);
                // OFF = place the experience in your space (design-system placement).
                Button { session.trackObject.toggle() } label: {
                    Image(systemName: session.trackObject ? "viewfinder.circle.fill" : "viewfinder.circle")
                        .font(.title2)
                        .foregroundStyle(session.trackObject ? .teal : .white.opacity(0.7))
                }
                TextField(session.experience == nil ? (session.trackObject
                                                       ? "What's the object in front of you?"
                                                       : "Describe an experience to build here…")
                                                    : "Prompt to change the scene…",
                          text: $input, axis: .vertical)
                    .textFieldStyle(.roundedBorder).lineLimit(1...3)
                    .submitLabel(.send).onSubmit(sendInput)
                Button(action: sendInput) {
                    Image(systemName: session.busy ? "hourglass" : "arrow.up.circle.fill").font(.title2)
                }.disabled(session.busy || input.trimmingCharacters(in: .whitespaces).isEmpty)
            }
            .padding(10).background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 14))
        }
        .padding()
    }

    private func sendInput() { let p = input; input = ""; session.send(p) }
}
#endif
