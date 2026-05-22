// VocabSyncTests.swift
// Smoke tests that fail if the generated Vocab.swift drifts from the JS
// contract or from the bundle spec. These run on the macOS host (no
// RealityKit/ARKit deps) and on CI.

import XCTest
@testable import SpatailPlayer

final class VocabSyncTests: XCTestCase {

    /// The schemaVersion baked into Vocab.swift must be a non-empty,
    /// well-formed token. If this fails, the codegen is broken.
    func testSchemaVersionFormat() {
        let v = SpatailContract.schemaVersion
        XCTAssertFalse(v.isEmpty, "schemaVersion is empty")
        XCTAssertTrue(v.contains("-spatail"), "schemaVersion missing -spatail suffix: \(v)")
    }

    /// Every shipped mechanic listed in IOS_BUNDLE_SPEC.md §5 must exist
    /// as a Swift case. Adding a new shipped mechanic = update this list.
    func testShippedMechanicsCoverage() {
        let shipped: [MechanicKind] = [
            .annotatedCallouts,
            .highlightedRegion,
            .explodedView,
            .crossSection,
            .assemblySequence,
            .timeline,
            .ghostedInternal,
        ]
        // If a case below fails to compile, the codegen lost it.
        XCTAssertEqual(Set(shipped).count, shipped.count)
    }

    /// All 7 animation primitives the iOS app supports must be present.
    func testAnimationPrimitiveCoverage() {
        let primitives: [AnimationPrimitive] = [
            .transformKeyframes,
            .explode,
            .assemble,
            .highlightPulse,
            .fade,
            .setVisible,
            .attentionCameraHint,
        ]
        XCTAssertEqual(Set(primitives).count, 7)
    }

    /// Bundle manifest version must match the value used by the server.
    func testBundleSchemaVersion() {
        let expected = "0.5.0-spatail-bundle"
        XCTAssertTrue(
            BundleManifest.supportedSchemaVersions.contains(expected),
            "Manifest.swift dropped support for \(expected)")
    }
}
