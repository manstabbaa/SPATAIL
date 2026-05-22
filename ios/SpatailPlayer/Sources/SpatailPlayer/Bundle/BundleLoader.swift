// BundleLoader.swift
// Unzips and validates a .spatail bundle into a folder URL the rest of the
// app reads from. Same output shape whether the source was offline (Files /
// AirDrop) or live (downloaded via asset.url + experience.delta).
//
// Spec: docs/xr/IOS_BUNDLE_SPEC.md

import Foundation
import ZIPFoundation

public struct LoadedBundle: Sendable {
    public let folder: URL
    public let manifest: BundleManifest
    public let experience: ExperienceContract
    public let primsIndex: PrimsIndex
}

public enum BundleLoaderError: Error, CustomStringConvertible {
    case unzipFailed(String)
    case missingFile(String)
    case schemaUnsupported(String)

    public var description: String {
        switch self {
        case .unzipFailed(let m):       return "Unzip failed: \(m)"
        case .missingFile(let p):       return "Bundle missing required file: \(p)"
        case .schemaUnsupported(let v): return "Unsupported schemaVersion: \(v)"
        }
    }
}

public enum BundleLoader {

    /// Unzip a `.spatail` (or a folder if already extracted) into the app's
    /// caches directory and decode the three JSON manifests.
    public static func load(from sourceURL: URL) throws -> LoadedBundle {
        let folder: URL
        if sourceURL.hasDirectoryPath {
            folder = sourceURL
        } else {
            folder = try unzip(sourceURL)
        }

        let manifest = try decode(BundleManifest.self,
                                   from: folder.appendingPathComponent("manifest.json"))
        guard manifest.isSupported else {
            throw BundleLoaderError.schemaUnsupported(manifest.schemaVersion)
        }

        let experience = try decode(ExperienceContract.self,
                                     from: folder.appendingPathComponent(manifest.files.experience))
        let primsIndex = try decode(PrimsIndex.self,
                                     from: folder.appendingPathComponent(manifest.files.primsIndex))

        return LoadedBundle(folder: folder,
                             manifest: manifest,
                             experience: experience,
                             primsIndex: primsIndex)
    }

    // MARK: -

    private static func decode<T: Decodable>(_ type: T.Type, from url: URL) throws -> T {
        guard FileManager.default.fileExists(atPath: url.path) else {
            throw BundleLoaderError.missingFile(url.lastPathComponent)
        }
        let data = try Data(contentsOf: url)
        return try JSONDecoder().decode(T.self, from: data)
    }

    private static func unzip(_ sourceURL: URL) throws -> URL {
        let fm = FileManager.default
        let dest = fm.urls(for: .cachesDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("bundles", isDirectory: true)
            .appendingPathComponent(sourceURL.deletingPathExtension().lastPathComponent,
                                     isDirectory: true)
        if fm.fileExists(atPath: dest.path) {
            try fm.removeItem(at: dest)
        }
        try fm.createDirectory(at: dest, withIntermediateDirectories: true)

        do {
            try fm.unzipItem(at: sourceURL, to: dest)
        } catch {
            throw BundleLoaderError.unzipFailed(error.localizedDescription)
        }
        return dest
    }
}
