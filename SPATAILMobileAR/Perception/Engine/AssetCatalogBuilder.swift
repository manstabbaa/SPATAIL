// AssetCatalogBuilder.swift
//
// Populates a SpatailAssetCatalog from the USDZ assets actually available to
// the app — bundled resources plus anything downloaded into Documents. The
// engine then emits a real `.model` overlay (with an assetRef) when a
// detection's label matches an available asset; otherwise the runtime draws
// the placeholder. No assets present → empty catalog, placeholders
// everywhere — the pipeline still runs.
//
// Pure Foundation.

import Foundation

enum AssetCatalogBuilder {

    /// Scan the bundle + Documents for `.usdz` and build a catalog keyed by
    /// the asset's base filename (lowercased). Bundle assets are referenced
    /// by resource name (loadModelAsync(named:)); Documents assets by full
    /// path (loadModelAsync(contentsOf:)).
    static func build(styleTag: String = "holographic") -> SpatailAssetCatalog {
        var map: [String: String] = [:]

        // Bundled USDZ → reference by resource name.
        if let bundled = Bundle.main.urls(forResourcesWithExtension: "usdz", subdirectory: nil) {
            for url in bundled {
                let name = url.deletingPathExtension().lastPathComponent
                map[name.lowercased()] = name
            }
        }

        // Documents USDZ (e.g. downloaded assets) → reference by full path.
        if let docs = try? FileManager.default.url(
            for: .documentDirectory, in: .userDomainMask, appropriateFor: nil, create: false) {
            let found = (try? FileManager.default.contentsOfDirectory(
                at: docs, includingPropertiesForKeys: nil)) ?? []
            for url in found where url.pathExtension.lowercased() == "usdz" {
                let name = url.deletingPathExtension().lastPathComponent
                map[name.lowercased()] = url.path
            }
        }

        var catalog = SpatailAssetCatalog()
        catalog.modelRefsByLabel = map
        catalog.styleTag = styleTag
        return catalog
    }
}
