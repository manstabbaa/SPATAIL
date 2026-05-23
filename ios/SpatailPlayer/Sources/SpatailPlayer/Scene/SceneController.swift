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
    public static let tabletopTargetSizeMeters: Float = 0.25

    /// TEMP DIAGNOSTIC: when true, override the computed factor with an
    /// obviously tiny scale (~9 mm wheel) to test whether `entity.scale` is
    /// being honored at render time at all. Remove once root cause is found.
    public static let diagForceTinyScale: Bool = true

    /// Load a bundle into the scene. Returns the root entity to anchor.
    public func load(_ bundle: LoadedBundle) async throws -> Entity {
        let usdz = bundle.folder.appendingPathComponent(bundle.manifest.files.scene)
        let entity = try await Entity(contentsOf: usdz)
        entity.name = "spatail.scene"

        let bbox = bundle.manifest.scene.boundingBoxMeters
        let supportsTabletop = bundle.manifest.scene.supportsTabletop
        var appliedFactor: Float = 1.0
        if supportsTabletop, let largest = bbox.max(), largest > 0 {
            appliedFactor = Self.tabletopTargetSizeMeters / largest
        }
        if Self.diagForceTinyScale { appliedFactor = 0.005 }
        entity.scale = SIMD3<Float>(repeating: appliedFactor)

        print("[SceneController][SPATAIL-DIAG] loaded='\(bundle.manifest.title)' " +
              "supportsTabletop=\(supportsTabletop) bbox=\(bbox) " +
              "diagForceTinyScale=\(Self.diagForceTinyScale) " +
              "appliedScaleFactor=\(appliedFactor) entity.scale=\(entity.scale) " +
              "entity.type=\(type(of: entity)) name='\(entity.name)' " +
              "childCount=\(entity.children.count)")
        for (i, child) in entity.children.enumerated() {
            print("  [SPATAIL-DIAG] child[\(i)] type=\(type(of: child)) " +
                  "name='\(child.name)' scale=\(child.scale) " +
                  "position=\(child.position) grandchildCount=\(child.children.count)")
        }

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
