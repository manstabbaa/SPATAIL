// PlacementPlanner.swift — turns a PlacementSolver plan into WORLD transforms.
//
// The solver's room math runs in the spine frame contract (room_model.py): user at
// the origin, surface dead ahead, floor at y 0. ARKit feeds world-frame surfaces and
// a live pose instead, so the planner maps the solved arc into the world and PINS it
// onto the REAL surface: the table's detected centre (§6: centre on the detected
// table), or the floor's detected world height (ARKit world y=0 is the session-start
// pose, ~1.4 m above the real floor — never a place to stand content). Every nudge it
// applies is recorded as a human-readable `correction` for the placement report.
//
// Object/part targets come back from the solver already in world space — the planner
// just shapes them into the same layout structure.

import Foundation
import simd

/// A solved, world-frame layout ready for the runtime to materialize.
struct PlannedLayout {
    /// World transform of the experience root (at the hero slot, yaw facing the user).
    let root: simd_float4x4
    /// Per-asset local offsets from the root (root's frame).
    let locals: [String: SIMD3<Float>]
    /// Uniform fit scale for non-baked assets.
    let scale: Float
    /// The raw plan (reasons + diagnostics ride along into the report).
    let plan: PlacementSolver.Plan
    /// Human-readable nudges applied on top of the raw solve ("table-pin -0.02m").
    let corrections: [String]
    /// The room model the solve consumed (for the report's roomSummary).
    let room: RoomModel?
}

enum PlacementPlanner {

    /// Solve + pin a room-anchored experience. Returns nil when the room can't answer
    /// yet (no real surface) — the caller falls back to raycast placement (§10: always
    /// provide a fallback).
    static func planInRoom(surfaces: [RoomSurface],
                           objects: [SpatailObject],
                           userPosition: SIMD3<Float>,
                           userForward: SIMD3<Float>,
                           assets: [PlacementSolver.AssetReq],
                           anchorPreference: String,
                           scaleMode: String,
                           primary: String?,
                           coverage: Float) -> PlannedLayout? {
        guard !surfaces.isEmpty, !assets.isEmpty else { return nil }
        let rm = RoomModel(surfaces: surfaces, objects: objects,
                           userPosition: userPosition, userForward: userForward)
        let plan = PlacementSolver.solve(room: rm, assets: assets,
                                         anchorPreference: anchorPreference,
                                         scaleMode: scaleMode,
                                         primary: primary,
                                         coverage: coverage)
        // Centre on the detected surface even when the fit is TIGHT — a tight/!fits
        // result (or a spurious obstacle flag) must not drop the demo to a
        // raycast-at-aim placement. The solver already scaled the set to the surface;
        // centring on the real table beats landing wherever the camera points.
        guard let hero = plan.placements.first else { return nil }

        // solver frame (user at origin, looking -Z) → world
        let f = rm.user.forward
        let theta = atan2(-f.x, -f.z)
        let mapRot = simd_quatf(angle: theta, axis: SIMD3(0, 1, 0))
        func toWorld(_ s: SIMD3<Float>) -> SIMD3<Float> {
            let r = mapRot.act(SIMD3(s.x, 0, s.z))
            return SIMD3(rm.user.position.x + r.x, s.y, rm.user.position.z + r.z)
        }

        var corrections: [String] = []
        let heroArc = toWorld(hero.position)
        let pin: SIMD3<Float>
        if plan.anchor == "table", let t = rm.bestTable() {
            pin = SIMD3(t.center.x - heroArc.x, t.height - heroArc.y, t.center.z - heroArc.z)
            corrections.append(String(format: "table-pin (%.2f, %.2f, %.2f)m", pin.x, pin.y, pin.z))
        } else if plan.anchor == "floor", let fl = rm.floor() {
            pin = SIMD3(0, fl.height - heroArc.y, 0)   // ahead of the user, on the real floor
            if abs(pin.y) > 0.005 {
                corrections.append(String(format: "floor-pin %+.2fm", pin.y))
            }
        } else {
            return nil                                  // no real surface → raycast fallback
        }

        // root sits at the hero slot, rotated to face the user (matches the raycast
        // path's convention so captions/labels hang the same way)
        let heroW = heroArc + pin
        let toUser = rm.user.position - heroW
        let rootYaw = atan2(toUser.x, toUser.z)
        let rootRot = simd_quatf(angle: rootYaw, axis: SIMD3(0, 1, 0))
        let root = matrix(rotation: rootRot, translation: heroW)
        let inv = rootRot.inverse
        var locals: [String: SIMD3<Float>] = [:]
        for p in plan.placements {
            locals[p.assetId] = inv.act(toWorld(p.position) + pin - heroW)
        }
        return PlannedLayout(root: root, locals: locals, scale: hero.scale,
                             plan: plan, corrections: corrections, room: rm)
    }

    /// Solve an object/part-anchored experience. World-space already; the root lands
    /// on the landing pad, yawed to the parent so content sits oriented to it.
    static func planOnObject(target: PlacementTarget,
                             assets: [PlacementSolver.AssetReq],
                             primary: String?,
                             coverage: Float) -> PlannedLayout? {
        guard !assets.isEmpty else { return nil }
        let plan = PlacementSolver.solve(target: target, assets: assets,
                                         primary: primary, coverage: coverage)
        guard let hero = plan.placements.first else { return nil }
        let rootRot = simd_quatf(angle: hero.yaw, axis: SIMD3(0, 1, 0))
        let root = matrix(rotation: rootRot, translation: hero.position)
        let inv = rootRot.inverse
        var locals: [String: SIMD3<Float>] = [:]
        for p in plan.placements {
            locals[p.assetId] = inv.act(p.position - hero.position)
        }
        return PlannedLayout(root: root, locals: locals, scale: hero.scale,
                             plan: plan, corrections: [], room: nil)
    }

    private static func matrix(rotation: simd_quatf, translation: SIMD3<Float>) -> simd_float4x4 {
        var m = simd_float4x4(rotation)
        m.columns.3 = SIMD4(translation.x, translation.y, translation.z, 1)
        return m
    }
}
