import SwiftUI
#if os(iOS)

// A project = a living XR scene + a persistent chat. The FIRST prompt creates the
// scene; every prompt after EVOLVES it (regenerate-from-prior, then re-present).
// v1 re-presents the whole scene on each edit; incremental edit-ops are the next
// step. Generated models stream in over placeholders and are cached for reopen.

@MainActor
final class ProjectSession: ObservableObject {
    @Published var conversation: [ConversationTurn] = []
    @Published var experience: ModularExperience?
    @Published var streamModel: StreamPayload?
    @Published var status = ""
    @Published var busy = false

    private var meta: ProjectMeta
    private let store: ProjectStore
    private let client = GenerativeClient()

    init(meta: ProjectMeta, store: ProjectStore) { self.meta = meta; self.store = store }

    func load() {
        conversation = store.loadConversation(meta.id)
        if let data = store.loadContract(meta.id),
           let exp = try? JSONDecoder().decode(ModularExperience.self, from: data) {
            experience = exp
        }
    }

    /// Context so the server evolves the CURRENT scene rather than starting over.
    private var priorContext: (subject: String, summary: String)? {
        guard let e = experience else { return nil }
        let subj = e.understanding.subject.isEmpty ? e.title : e.understanding.subject
        let sum = e.understanding.summary.isEmpty ? e.title : e.understanding.summary
        return (subj, sum)
    }

    func send(_ prompt: String) {
        let p = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !p.isEmpty, !busy else { return }
        let isFirst = experience == nil
        conversation.append(ConversationTurn(role: "user", text: p))
        store.saveConversation(conversation, for: meta.id)
        busy = true; status = isFirst ? "creating…" : "evolving the scene…"
        let prior = priorContext
        Task {
            do {
                let result = try await client.generateProject(text: p, prior: prior)
                experience = result.experience
                store.saveContract(result.raw, for: meta.id)
                meta.updatedAt = Date()
                if meta.title.isEmpty || isFirst { meta.title = result.experience.title }
                store.saveMeta(meta)
                conversation.append(ConversationTurn(role: "assistant",
                    text: "Updated — \(result.experience.title) · \(result.experience.mechanicsUsed.count) mechanics"))
                store.saveConversation(conversation, for: meta.id)
                busy = false; status = "ready — tap to step, or keep prompting"
                if let jid = result.experience.generationJobId, !jid.isEmpty {
                    status = "building a real model…"
                    await streamGenerated(jid, result.experience)
                }
            } catch {
                busy = false; status = "error: \(error.localizedDescription)"
                conversation.append(ConversationTurn(role: "assistant",
                    text: "Couldn't do that: \(error.localizedDescription)"))
                store.saveConversation(conversation, for: meta.id)
            }
        }
    }

    private func streamGenerated(_ jobId: String, _ exp: ModularExperience) async {
        do {
            let url = try await client.awaitGeneratedModel(jobId: jobId) { [weak self] s in
                Task { @MainActor in self?.status = "building: \(s)" }
            }
            let target = exp.assets.first(where: { $0.role == "primary_object" })?.id
                       ?? exp.assets.first?.id ?? ""
            if let cached = store.cacheAsset(url, assetId: target, projectId: meta.id) {
                meta.cachedAssets[target] = cached.lastPathComponent
                store.saveMeta(meta)
                streamModel = StreamPayload(assetId: target, url: cached)
            } else {
                streamModel = StreamPayload(assetId: target, url: url)
            }
            status = "real model ready"
        } catch { status = "model build failed: \(error.localizedDescription)" }
    }

    /// After the AR view presents the saved contract, re-apply cached generated models.
    func restreamCached() {
        for (assetId, file) in meta.cachedAssets {
            let u = store.assetsDir(meta.id).appendingPathComponent(file)
            if FileManager.default.fileExists(atPath: u.path) {
                streamModel = StreamPayload(assetId: assetId, url: u)
            }
        }
    }
}

struct ProjectView: View {
    let meta: ProjectMeta
    @ObservedObject var store: ProjectStore
    var initialPrompt: String?
    @StateObject private var session: ProjectSession
    @State private var input = ""

    init(meta: ProjectMeta, store: ProjectStore, initialPrompt: String? = nil) {
        self.meta = meta; self.store = store; self.initialPrompt = initialPrompt
        _session = StateObject(wrappedValue: ProjectSession(meta: meta, store: store))
    }

    var body: some View {
        ZStack(alignment: .bottom) {
            ModularARView(experience: session.experience, streamModel: session.streamModel,
                          onStatus: { session.status = $0 })
                .ignoresSafeArea()
            chat
        }
        .navigationTitle(meta.title.isEmpty ? "Experience" : meta.title)
        .navigationBarTitleDisplayMode(.inline)
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
                }
                .padding(8).background(.ultraThinMaterial, in: Capsule())
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
                TextField("Prompt to change the scene…", text: $input, axis: .vertical)
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

    private func sendInput() {
        let p = input; input = ""; session.send(p)
    }
}

#else
struct ProjectView: View {
    let meta: ProjectMeta
    var store: ProjectStore
    var initialPrompt: String? = nil
    var body: some View {
        Color.black.overlay(Text("Projects — available on iPhone").foregroundStyle(.white))
    }
}
#endif
