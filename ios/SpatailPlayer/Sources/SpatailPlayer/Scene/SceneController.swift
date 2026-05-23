// SceneController.swift
// Owns the RealityKit Entity tree for the currently-loaded experience.
// Receives:
//   - a LoadedBundle (offline or live) → loads scene.usdz, instantiates
//     mechanic renderers per contract.mechanics[]
//   - experience.delta(patch) → applies a diff to the in-memory contract
//     and re-resolves placements (geometry untouched).

#if os(iOS) || os(visionOS)
import Foundation
import RealityKit

public final class SceneController {

    public private(set) var root: Entity = Entity()
    public private(set) var registry = EntityRegistry()

    /// Target size for `supportsTabletop` bundles, in meters along the longest axis.
    /// Scale is applied on a wrapper in `ARViewContainer`, not on the raw
    /// loaded entity, because USDZ animation tracks can override transforms
    /// written directly to `Entity(contentsOf:)`'s root.
    public static let tabletopTargetSizeMeters: Float = 0.25

    /// Compute the initial wrapper scale for a bundle, honoring `supportsTabletop`.
    public static func initialScale(for manifest: BundleManifest) -> Float {
        guard manifest.scene.supportsTabletop,
              let largest = manifest.scene.boundingBoxMeters.max(),
              largest > 0
        else { return 1.0 }
        return tabletopTargetSizeMeters / largest
    }

    /// Load a bundle into the scene. Returns the raw root entity from the USDZ;
    /// the caller is responsible for wrapping/anchoring/gesture installation.
    public func load(_ bundle: LoadedBundle) async throws -> Entity {
        let usdz = bundle.folder.appendingPathComponent(bundle.manifest.files.scene)
        let entity = try await Entity(contentsOf: usdz)
        entity.name = "spatail.scene"
        registry.bind(entity, primsIndex: bundle.primsIndex)
        root = entity
        return entity
    }

    /// Re-apply placements after an experience.delta(patch). v1 stub.
    public func replan(experience: ExperienceContract) {
        // TODO: walk experience.spatialElements, re-resolve placements
        // against the current anchor + room state, animate to new poses.
    }
}
#endif
