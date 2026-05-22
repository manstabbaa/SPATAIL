// SpatialPlacementEngine.swift
//
// v1 stub. The bundled contracts already carry placement positions.
// This service exists for the v2 path where the iPhone plans on-device
// and needs to decide where each element lives in the user's room.
//
// Mirror of /pipeline/spatail/placement_engine.js — keep them in sync.

import Foundation
import simd

struct PlacementDecision: Hashable {
    let placement: Placement
    let anchor: String
    let position: SIMD3<Float>
    let scale: ScaleMode
    let reason: String
}

protocol SpatialPlacing {
    func place(_ element: SpatialElement,
               in layout: inout PlacementLayoutState) -> PlacementDecision
}

/// Stacked cursors so consecutive panels don't overlap. Equivalent to
/// `createLayoutState()` in the JS engine.
struct PlacementLayoutState {
    var leftOfUserCursorY: Float = 1.5
    var rightOfUserCursorY: Float = 1.5
    var wallCursorX: Float = -1.2
    var floorCursorX: Float = -1.5
    var nearUserCursorX: Float = -0.5
    var tabletopCounter: Int = 0
    var decisionCounter: Int = 0
    var physicalTargetCounter: Int = 0
}

final class SpatialPlacementEngine: SpatialPlacing {
    func place(_ element: SpatialElement,
               in layout: inout PlacementLayoutState) -> PlacementDecision {
        // For v1 we trust the contract's placement. This implementation
        // is a hook for v2.
        let kind = element.placementKindEnum ?? .in_front_of_user
        return PlacementDecision(
            placement: kind,
            anchor: element.placement.anchor ?? kind.rawValue,
            position: element.placement.simdPosition,
            scale: element.scaleModeEnum ?? .compact_panel,
            reason: element.whyThisPlacement,
        )
    }
}
