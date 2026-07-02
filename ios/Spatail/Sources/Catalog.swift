import Foundation

// Loads every SPATAIL demo bundled as <name>.usdz + <name>_metadata.json in the
// app Resources. The studio copies its out/*.usdz + *_metadata.json here (see
// studio/ios_sync.py). New topics appear automatically — no code change.

enum Catalog {
    static func load() -> [CatalogEntry] {
        var entries: [CatalogEntry] = []
        let bundle = Bundle.main
        let metaURLs = bundle.urls(forResourcesWithExtension: "json",
                                   subdirectory: nil)?
            .filter { $0.lastPathComponent.hasSuffix("_metadata.json") } ?? []

        for url in metaURLs {
            guard let data = try? Data(contentsOf: url),
                  let meta = try? JSONDecoder().decode(SpatailMetadata.self, from: data)
            else { continue }
            // single-exhibit metadata has exactly one beat; the usdz name matches
            guard let beat = meta.beats.first else { continue }
            let usdzName = (meta.usdz as NSString?)?.lastPathComponent
                .replacingOccurrences(of: ".usdz", with: "")
                ?? "studio_\(beat.id)"
            // only list it if the USDZ is actually bundled
            guard bundle.url(forResource: usdzName, withExtension: "usdz") != nil
            else { continue }
            entries.append(CatalogEntry(
                id: beat.id,
                title: beat.title,
                subtitle: "\(beat.law ?? "") · \(beat.subtitle ?? "")",
                usdzName: usdzName,
                footprint: beat.footprint_m,
                narration: beat.narration ?? ""))
        }
        return entries.sorted { $0.id < $1.id }
    }
}
