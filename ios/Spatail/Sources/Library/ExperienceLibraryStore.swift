// ExperienceLibraryStore.swift — saved experiences, the legacy Project.swift
// pattern trimmed to what the Lens app needs.
//
// Every /modular contract that lands gets persisted VERBATIM (raw server JSON —
// ModularContract is tolerant-Decodable only, so the bytes are the archive) plus
// a small meta record for the grid. Layout on disk:
//
//   Documents/SpatailLibrary/<experienceId>/
//     meta.json        — SavedExperience
//     contract.json    — the raw /modular response, byte-for-byte
//
// Re-asking the same subject overwrites the entry (experienceId is a stable
// subject slug server-side) and bumps updatedAt — the library shows living
// experiences, not a log. Wire-in point (composition root):
//
//   ExperienceLibraryStore.shared.record(prompt: prompt, contract: contract,
//                                        contractData: rawData)
//
// right where AppModel.ask receives the contract.

import Foundation

struct SavedExperience: Codable, Identifiable, Equatable {
    let id: String              // experienceId
    var title: String
    var prompt: String
    var createdAt: Date
    var updatedAt: Date
    var stepCount: Int
    var assetCount: Int
    var subject: String
    var anchoringMode: String   // "object" | "world"
}

@MainActor
final class ExperienceLibraryStore: ObservableObject {

    static let shared = ExperienceLibraryStore()

    /// Newest first — the grid binds straight to this.
    @Published private(set) var entries: [SavedExperience] = []

    private let root: URL
    private let fm = FileManager.default

    init() {
        let docs = fm.urls(for: .documentDirectory, in: .userDomainMask)[0]
        root = docs.appendingPathComponent("SpatailLibrary", isDirectory: true)
        try? fm.createDirectory(at: root, withIntermediateDirectories: true)
        reload()
    }

    // MARK: Record / read / delete

    /// Persist a received contract (raw bytes are the source of truth).
    func record(prompt: String, contract: ModularContract, contractData: Data) {
        let id = contract.experienceId
        guard !id.isEmpty else { return }
        let dir = self.dir(id)
        try? fm.createDirectory(at: dir, withIntermediateDirectories: true)

        let existing = entries.first { $0.id == id }
        let meta = SavedExperience(
            id: id,
            title: contract.title,
            prompt: prompt,
            createdAt: existing?.createdAt ?? Date(),
            updatedAt: Date(),
            stepCount: contract.sequence.count,
            assetCount: contract.assets.count,
            subject: contract.understanding.subject,
            anchoringMode: contract.anchoring.mode)

        try? contractData.write(to: dir.appendingPathComponent("contract.json"))
        if let data = try? JSONEncoder().encode(meta) {
            try? data.write(to: dir.appendingPathComponent("meta.json"))
        }
        reload()
    }

    /// Convenience for call sites that only hold the raw response.
    func record(prompt: String, contractData: Data) {
        guard let contract = try? JSONDecoder().decode(ModularContract.self,
                                                       from: contractData) else { return }
        record(prompt: prompt, contract: contract, contractData: contractData)
    }

    func contractData(for id: String) -> Data? {
        try? Data(contentsOf: dir(id).appendingPathComponent("contract.json"))
    }

    /// Decode the saved contract for re-placement.
    func contract(for id: String) -> ModularContract? {
        guard let data = contractData(for: id) else { return nil }
        return try? JSONDecoder().decode(ModularContract.self, from: data)
    }

    func delete(_ id: String) {
        try? fm.removeItem(at: dir(id))
        reload()
    }

    // MARK: Disk

    private func dir(_ id: String) -> URL {
        root.appendingPathComponent(id, isDirectory: true)
    }

    private func reload() {
        let ids = (try? fm.contentsOfDirectory(atPath: root.path)) ?? []
        var metas: [SavedExperience] = []
        for id in ids {
            let url = dir(id).appendingPathComponent("meta.json")
            if let data = try? Data(contentsOf: url),
               let meta = try? JSONDecoder().decode(SavedExperience.self, from: data) {
                metas.append(meta)
            }
        }
        entries = metas.sorted { $0.updatedAt > $1.updatedAt }
    }
}
