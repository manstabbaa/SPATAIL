// Manifest.swift
// Decoder for manifest.json (top-level bundle pointers).
// Spec: docs/xr/IOS_BUNDLE_SPEC.md §2

import Foundation

public struct BundleManifest: Codable, Sendable {
    public let schemaVersion: String
    public let experienceId: String
    public let title: String
    public let createdAt: String
    public let source: Source
    public let files: Files
    public let scene: Scene
    public let narrationLanguages: [String]?

    public struct Source: Codable, Sendable {
        public let asset: String
        public let prompt: String
    }

    public struct Files: Codable, Sendable {
        public let experience: String
        public let scene: String
        public let primsIndex: String
        public let thumbnail: String
    }

    public struct Scene: Codable, Sendable {
        public let unitScale: Float
        public let upAxis: String
        public let boundingBoxMeters: [Float]
        public let defaultViewerDistanceMeters: Float
        public let supportsRealScale: Bool
        public let supportsTabletop: Bool
    }

    /// The bundle-side schema version the iOS app supports. Keep this in
    /// lockstep with the server's `bundleSchemaVersion` in `session.ready`
    /// and with the version emitted by `spatail_export_xr.py`.
    ///
    /// Sync rule: see docs/xr/SYNC_WORKFLOW.md §4
    public static let supportedSchemaVersions: [String] = [
        "0.5.0-spatail-bundle"
    ]

    public var isSupported: Bool {
        Self.supportedSchemaVersions.contains(schemaVersion)
    }
}
