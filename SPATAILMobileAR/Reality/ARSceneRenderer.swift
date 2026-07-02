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
///
/// `@MainActor`: every conformer subclasses RealityKit's `Entity`, which is
/// main-actor-isolated, so the implementing methods are main-actor-isolated
/// too. Isolating the protocol to the main actor lets those methods satisfy
/// the requirements (a main-actor method cannot satisfy a nonisolated one)
/// and matches reality — these are only ever flipped from the AR coordinator
/// on the main thread.
@MainActor
protocol Highlightable: AnyObject {
    func setHighlighted(_ on: Bool)
}

@MainActor
protocol Explodable: AnyObject {
    func explode()
    func collapse()
}

@MainActor
final class ARSceneRenderer {
    struct RenderResult {
        let root: Entity
        /// Entities that respond to highlight toggle (highlighted_target).
        let highlightables: [Highlightable]
        /// Map of explodable-view entity id -> Explodable wrapper.
        let explodableTargets: [(String, Explodable)]
    }

    /// Where the user is NOW, for `user_relative` placements. The brain's
    /// stand-in branch (in_front_of_user) emits positions in a user frame —
    /// x right / y above floor / -z ahead — not world space. Without this
    /// context those elements land at the AR session origin, which after a
    /// room sweep can be behind the user or inside a wall.
    struct PlacementContext {
        let cameraTransform: simd_float4x4?
        /// World y of the real floor if known (scanned floor surface).
        let floorY: Float?
        static let none = PlacementContext(cameraTransform: nil, floorY: nil)
    }

    func build(for contract: SpatialExperienceContract,
               context: PlacementContext = .none) -> RenderResult {
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
            applyTransform(el, to: entity, context: context)
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
        // Fusion-brain surface padding reuses the generic three_d_model
        // representation, so it routes by placement kind (edge / corner) to its
        // own builder rather than the tabletop-model path.
        if el.placementKindEnum == .surface_edge || el.placementKindEnum == .surface_corner {
            return SurfacePaddingBuilder().build(for: el)
        }
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

    private func applyTransform(_ el: SpatialElement, to entity: Entity,
                                context: PlacementContext) {
        let isUserRelative = el.anchorStrategyEnum == .user_relative
            || el.placementKindEnum == .in_front_of_user
        if isUserRelative, let cam = context.cameraTransform {
            let rel = el.placement.simdPosition
            let camPos = SIMD3<Float>(cam.columns.3.x, cam.columns.3.y, cam.columns.3.z)
            // Yaw-only heading: pitch shouldn't tilt "1 m ahead" into the
            // floor or ceiling.
            var fwd = SIMD3<Float>(-cam.columns.2.x, 0, -cam.columns.2.z)
            fwd = simd_length(fwd) > 1e-4 ? simd_normalize(fwd) : SIMD3(0, 0, -1)
            let right = simd_cross(fwd, SIMD3<Float>(0, 1, 0))
            let xz = camPos + right * rel.x + fwd * (-rel.z)
            // rel.y is height above the floor in the brain's user frame;
            // without a scanned floor assume the phone rides ~1.4 m up.
            let baseY = context.floorY ?? (camPos.y - 1.4)
            entity.position = SIMD3(xz.x, baseY + rel.y, xz.z)
            // Face the user (yaw toward the camera).
            entity.orientation = simd_quatf(angle: atan2(fwd.x, fwd.z),
                                            axis: SIMD3<Float>(0, 1, 0))
            return
        }
        entity.position = el.placement.simdPosition
        let r = el.placement.simdRotation
        entity.orientation = simd_quatf(angle: r.y, axis: SIMD3<Float>(0, 1, 0))
            * simd_quatf(angle: r.x, axis: SIMD3<Float>(1, 0, 0))
            * simd_quatf(angle: r.z, axis: SIMD3<Float>(0, 0, 1))
    }
}
