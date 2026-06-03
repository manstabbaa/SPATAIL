"""scene_builder.py — RuntimeSceneBuilder: (plan, delivery package) → Runtime Scene Contract.

This is where LAYOUT happens — in SPATAIL, never in Blender. Every position comes
from studio/xr_design (the comfort single-source-of-truth): the stage distance,
the focal point / comfort arc, the yaw that faces each panel to the user, and the
no-head-turn comfort cone test. The emitted contract is shape-compatible with the
StudioSceneContract family (contract.py) — same top-level keys, same comfortGuides
block built from the same xr.* constants — bumped to a labelled sibling schema
version so it reads as a runtime contract, not a forgery of the Newton's-laws one.
"""
from __future__ import annotations

from datetime import datetime, timezone

import experience_spec as es
import xr_design as xr

SCHEMA_VERSION = "0.4.0-studio-runtime"

# plan asset.type → the contract's assets[].role label.
_ROLE = {
    "hero": "primary_object",
    "part": "part",
    "comparison_item": "comparison_object",
    "environment": "environment",
    "label_target": "label",
}


def _focal_yup(d: float) -> list:
    # centred focal point: Blender (0, d, baseline) -> y-up (0, baseline, -d)
    return [0.0, round(xr.baseline_height(d), 4), round(-d, 4)]


def _b2yup(p) -> list:
    """Blender (x, y, z) -> y-up (x, z, -y), as the glTF exporter maps it."""
    x, y, z = p
    return [round(x, 4), round(z, 4), round(-y, 4)]


def _conf_label(c: float) -> str:
    return "high" if c >= 0.6 else ("medium" if c >= 0.35 else "low")


class RuntimeSceneBuilder:
    def stage_distance(self, plan) -> float:
        anchor = plan.placement.anchor
        if anchor == "table":
            return xr.FOCAL_PLANE_M             # 0.74 m — manipulable objects
        if anchor == "free":
            return xr.READ_FAR_M                # 1.5 m — wall / floating panels
        return xr.clamp_distance(2.5)           # floor / room-scale

    def build(self, plan, pkg, *, scene_id: str | None = None,
              prompt: str = "") -> dict:
        eye = xr.EYE_HEIGHT_M
        d = self.stage_distance(plan)
        type_by_id = {a.id: a.type for a in plan.requiredAssets}

        # --- layout (xr_design is the only source of positions) --------------
        n = len(pkg.assets)
        if n <= 1:
            positions = [xr.focal_point(d)]
        else:
            positions = xr.arc_positions(n, d, spread_deg=xr.OCPA_CONE_DEG)

        placed = []
        for i, asset in enumerate(pkg.assets):
            pb = positions[i] if i < len(positions) else xr.focal_point(d)
            size = (asset.metadata.realWorldScale if asset.metadata else [0, 0, 0]) or [0, 0, 0]
            # Z-up size (x,y,z) -> y-up footprint (w=x, h=z, d=y)
            footprint = {"w": round(size[0], 4), "h": round(size[2], 4), "d": round(size[1], 4)}
            placed.append({
                "assetId": asset.assetId,
                "role": _ROLE.get(type_by_id.get(asset.assetId, "part"), "part"),
                "position_yup": _b2yup(pb),
                "yaw_rad": round(xr.yaw_to_face_user(pb[0], pb[1]), 4),
                "footprint_m": footprint,
                "in_comfort_cone": xr.in_comfort_cone(pb),
                "status": "processed" if asset.status in ("built", "cached", "library") else "placeholder",
                "processedPath": asset.path,
            })
        placed_by_id = {p["assetId"]: p for p in placed}

        # --- comfort guides (identical to contract.py:28-39) -----------------
        guides = {
            "eye_height_m": eye,
            "cone_deg": xr.OCPA_CONE_DEG,
            "gaze_down_deg": xr.GAZE_DOWN_DEG,
            "near_clip_m": xr.NEAR_CLIP_M,
            "focal_plane_m": xr.FOCAL_PLANE_M,
            "read_band_m": [xr.READ_NEAR_M, xr.READ_FAR_M],
            "far_max_m": xr.FAR_MAX_M,
            "focal_point_yup": _focal_yup(xr.FOCAL_PLANE_M),
            "stage_distance_m": d,
            "baseline_z_m": round(xr.baseline_height(d), 4),
        }

        # --- beats -----------------------------------------------------------
        beats = []
        for b in plan.experienceBeats:
            tgt = placed_by_id.get(b.focus)
            focus_yup = tgt["position_yup"] if tgt else _focal_yup(d)
            in_cone = tgt["in_comfort_cone"] if tgt else xr.in_comfort_cone(xr.focal_point(d))
            beats.append({
                "id": b.id, "title": b.title, "narration": b.narration,
                "focusTarget": focus_yup,
                "labelAnchor": [focus_yup[0], round(focus_yup[1] + 0.15, 4), focus_yup[2]],
                "distanceM": round(d, 4), "inComfortCone": in_cone, "reveal": b.reveal,
            })

        # --- interactions (types stay inside es.MECHANIC_TYPES) --------------
        interactions = []
        for it in plan.interactions:
            if it.type not in es.MECHANIC_TYPES:
                continue
            interactions.append({
                "id": it.id, "type": it.type, "label": it.label,
                "params": it.params, "trigger": _trigger_for(it.type),
            })

        # --- assets ----------------------------------------------------------
        assets_out = [{
            "id": p["assetId"], "role": p["role"], "status": p["status"],
            "processedPath": p["processedPath"], "footprint": p["footprint_m"],
            "position_yup": p["position_yup"], "yaw_rad": p["yaw_rad"],
            "inComfortCone": p["in_comfort_cone"],
        } for p in placed]

        # --- studio block (primary asset summary) ----------------------------
        primary = pkg.assets[0] if pkg.assets else None
        bbox_yup = self._bbox_yup(primary)
        anim0 = pkg.animations[0].animationId if pkg.animations else None
        subject_title = (plan.subject or scene_id or "Experience").strip().title()

        return {
            "schemaVersion": SCHEMA_VERSION,
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "sceneId": scene_id or plan.experienceId,
            "title": subject_title,
            "sourceBrief": {
                "prompt": prompt, "subject": plan.subject, "domain": plan.domain,
                "intent": plan.intent, "strategy": plan.strategy,
            },
            "domain": {"name": plan.domain,
                       "confidence": _conf_label(plan.domainConfidence),
                       "source": plan.source},
            "representation": {
                "domain": plan.domain, "intent": plan.intent,
                "strategy": plan.strategy, "subject": plan.subject,
                "reasoning": plan.reasoning,
                "domainConfidence": plan.domainConfidence,
                "intentConfidence": plan.intentConfidence,
            },
            "placement": {
                "anchor": plan.placement.anchor, "layout": plan.placement.layout,
                "facing": plan.placement.facing, "scaleMode": plan.placement.scale_mode,
            },
            "studio": {
                "glb": primary.path if primary else "",
                "frame": "y_up_m",
                "room": {"w": xr.ROOM_W, "d": xr.ROOM_D, "h": xr.ROOM_H},
                "user": {"eye_height_m": eye},
                "animation": {"clip": anim0, "fps": 30, "loop": True,
                              "count": len(pkg.animations)},
                "bbox": bbox_yup,
            },
            "comfortGuides": guides,
            "staging": {
                "distance_m": round(d, 4),
                "spread_deg": xr.OCPA_CONE_DEG if n > 1 else 0.0,
                "reasoning": (f"{n} exhibit(s) on a {plan.placement.layout} at "
                              f"{round(d, 2)} m, dropped {xr.GAZE_DOWN_DEG} deg along "
                              f"the natural gaze; anchor={plan.placement.anchor}."),
            },
            "beats": beats,
            "storySequence": [{
                "id": b["id"], "title": b["title"], "description": b["narration"],
                "suggestedAction": "next_beat",
            } for b in beats],
            "interactions": interactions,
            "assets": assets_out,
            "xrDesignCitations": xr.CITATIONS,
        }

    def _bbox_yup(self, primary) -> dict:
        if not primary or not primary.metadata:
            return {"min": [0, 0, 0], "max": [0, 0, 0]}
        bb = primary.metadata.boundingBoxMeters or {}
        mn = bb.get("min", [0, 0, 0])
        mx = bb.get("max", [0, 0, 0])
        # Blender (x,y,z) -> y-up (x, z, -y); recompute min/max after the swap.
        return {
            "min": [round(mn[0], 4), round(mn[2], 4), round(-mx[1], 4)],
            "max": [round(mx[0], 4), round(mx[2], 4), round(-mn[1], 4)],
        }


def _trigger_for(mechanic_type: str) -> str:
    return {
        "play_baked": "on_tap",
        "tap_reveal": "on_tap",
        "grab_physics": "on_grab",
        "quiz_panel": "on_focus",
    }.get(mechanic_type, "on_tap")
