// ARSceneRenderer.swift
//
// Top-level builder: takes a SpatialExperienceContract, dispatches each
// element to the right per-mode builder, and returns a root Entity
// plus the small bookkeeping the coordinator needs (which entities
// can be exploded / highlighted, which one is the implicit "target").
//
// This is the only file in /Reality that knows the contract schema.
// Every per-mode builder takes a SpatialElement and returns an Entity.

import Foundation
import RealityKit
import simd

/// Protocol surfaced by ExplodableEntity and HighlightableEntity wrappers
/// so the coordinator can flip them without knowing their concrete type.
protocol Highlightable: AnyObject {
    func setHighlighted(_ on: Bool)
}

protocol Explodable: AnyObject {
    func explode()
    func collapse()
}

final class ARSceneRenderer {
    struct RenderResult {
        let root: Entity
        /// Entities that respond to highlight toggle (highlighted_target).
        let highlightables: [Highlightable]
        /// Map of explodable-view entity id -> Explodable wrapper.
        let explodableTargets: [(String, Explodable)]
    }

    func build(for contract: SpatialExperienceContract) -> RenderResult {
        let root = Entity()
        root.name = "SpatialExperienceRoot"

        var highlightables: [Highlightable] = []
        var explodables: [(String, Explodable)] = []

        // First pass: pull out targets so anchored elements can resolve
        // their target's world position.
        let elementsById = Dictionary(uniqueKeysWithValues:
            contract.spatialElements.map { ($0.id, $0) })

        // Domain-specific scene composer hooks. They add scenery
        // (engine bay base / table base) so the room reads right.
        switch contract.detectedDomain.name {
        case "vehicle_maintenance":
            MustangServiceSceneBuilder().addScenery(to: root)
        case "corporate_review":
            CorporateReviewSceneBuilder().addScenery(to: root)
        default:
            break
        }

        // Per-element rendering.
        for el in contract.spatialElements {
            let entity = renderElement(el, allElements: elementsById)
            entity.name = el.id
            applyTransform(el, to: entity)
            root.addChild(entity)

            if let h = entity as? Highlightable {
                highlightables.append(h)
            }
            if let ex = entity as? Explodable {
                explodables.append((el.id, ex))
            }
        }

        return RenderResult(root: root,
                            highlightables: highlightables,
                            explodableTargets: explodables)
    }

    // MARK: - Dispatch

    private func renderElement(_ el: SpatialElement,
                               allElements: [String: SpatialElement]) -> Entity {
        switch el.representationModeEnum {
        case .two_d_panel:
            return SpatialPanelBuilder().build(for: el, style: .standard)
        case .wall_dashboard:
            return WallPanelBuilder().build(for: el)
        case .floating_decision_card:
            return SpatialPanelBuilder().build(for: el, style: .decision)
        case .anchored_callout:
            return CalloutBuilder().build(for: el)
        case .diagnostic_overlay:
            return SpatialPanelBuilder().build(for: el, style: .diagnostic)
        case .tabletop_model:
            return TabletopModelBuilder().build(for: el)
        case .three_d_model:
            return TabletopModelBuilder().build(for: el)
        case .highlighted_target:
            return AnchoredObjectBuilder().buildTarget(for: el)
        case .exploded_view:
            // Detect Mustang's air-filter specifically via target ref;
            // future assemblies use a generic builder.
            if (el.sourceContent?.assetGroupRef?.contains("air-filter") ?? false)
                || el.sourceContent?.components?.contains(where: { $0.name.lowercased().contains("filter") }) ?? false {
                return AirFilterAssemblyBuilder().build(for: el)
            }
            return AirFilterAssemblyBuilder().build(for: el)  // generic fallback for now
        case .floor_timeline:
            return FloorTimelineBuilder().build(for: el)
        case .guide_line:
            return GuideLineBuilder().build(for: el)
        }
    }

    private func applyTransform(_ el: SpatialElement, to entity: Entity) {
        entity.position = el.placement.simdPosition
        let r = el.placement.simdRotation
        entity.orientation = simd_quatf(angle: r.y, axis: SIMD3<Float>(0, 1, 0))
            * simd_quatf(angle: r.x, axis: SIMD3<Float>(1, 0, 0))
            * simd_quatf(angle: r.z, axis: SIMD3<Float>(0, 0, 1))
    }
}
