import Foundation

// A saved SPATAIL project: a LIVING XR scene you reopen and keep prompting.
// Persisted as a local bundle under Documents/SpatailProjects/<id>/:
//   meta.json          — ProjectMeta (title, dates, cached asset map)
//   contract.json      — the latest scene contract, RAW server JSON (verbatim)
//   conversation.json  — [ConversationTurn]
//   assets/<id>.usdz   — cached generated models so the project reopens offline

struct ConversationTurn: Codable, Identifiable, Hashable {
    var id: String = UUID().uuidString
    var role: String                 // "user" | "assistant"
    var text: String
    var date: Date = Date()
}

struct ProjectMeta: Codable, Identifiable, Hashable {
    let id: String
    var title: String
    var createdAt: Date
    var updatedAt: Date
    var firstPrompt: String
    var cachedAssets: [String: String] = [:]   // assetId -> local usdz filename
}

@MainActor
final class ProjectStore: ObservableObject {
    @Published private(set) var projects: [ProjectMeta] = []
    let root: URL

    init() {
        let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        root = docs.appendingPathComponent("SpatailProjects", isDirectory: true)
        try? FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        reload()
    }

    func dir(_ id: String) -> URL { root.appendingPathComponent(id, isDirectory: true) }
    func assetsDir(_ id: String) -> URL { dir(id).appendingPathComponent("assets", isDirectory: true) }

    func reload() {
        let fm = FileManager.default
        let ids = (try? fm.contentsOfDirectory(atPath: root.path)) ?? []
        var metas: [ProjectMeta] = []
        for id in ids {
            let u = dir(id).appendingPathComponent("meta.json")
            if let data = try? Data(contentsOf: u),
               let meta = try? JSONDecoder().decode(ProjectMeta.self, from: data) {
                metas.append(meta)
            }
        }
        projects = metas.sorted { $0.updatedAt > $1.updatedAt }
    }

    @discardableResult
    func create(title: String, firstPrompt: String) -> ProjectMeta {
        let id = UUID().uuidString
        try? FileManager.default.createDirectory(at: assetsDir(id), withIntermediateDirectories: true)
        let meta = ProjectMeta(id: id, title: title, createdAt: Date(), updatedAt: Date(),
                               firstPrompt: firstPrompt)
        saveMeta(meta); reload(); return meta
    }

    func delete(_ id: String) { try? FileManager.default.removeItem(at: dir(id)); reload() }

    func saveMeta(_ m: ProjectMeta) {
        if let d = try? JSONEncoder().encode(m) {
            try? d.write(to: dir(m.id).appendingPathComponent("meta.json"))
        }
    }

    func saveContract(_ data: Data, for id: String) {
        try? data.write(to: dir(id).appendingPathComponent("contract.json"))
    }
    func loadContract(_ id: String) -> Data? {
        try? Data(contentsOf: dir(id).appendingPathComponent("contract.json"))
    }

    func saveConversation(_ turns: [ConversationTurn], for id: String) {
        if let d = try? JSONEncoder().encode(turns) {
            try? d.write(to: dir(id).appendingPathComponent("conversation.json"))
        }
    }
    func loadConversation(_ id: String) -> [ConversationTurn] {
        guard let d = try? Data(contentsOf: dir(id).appendingPathComponent("conversation.json")) else { return [] }
        return (try? JSONDecoder().decode([ConversationTurn].self, from: d)) ?? []
    }

    /// Copy a streamed generated USDZ into the project so it reopens offline.
    func cacheAsset(_ src: URL, assetId: String, projectId: String) -> URL? {
        let dst = assetsDir(projectId).appendingPathComponent("\(assetId).usdz")
        try? FileManager.default.removeItem(at: dst)
        do { try FileManager.default.copyItem(at: src, to: dst); return dst } catch { return nil }
    }
}
