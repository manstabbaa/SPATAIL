"""
spatail_scale_normalize.py — bring an imported CAD scene to real-world cm.

See skills/spatail-scale-normalize/SKILL.md for the architectural rationale.

ENTRY POINT:
    exec(open(r"C:/SPATAIL_MAX/pipeline/blender/spatail_scale_normalize.py").read())
    result = normalize_scale_in_scene(
        target_diagonal_cm=32.0,        # OR asset_hint="steering_wheel"
        out_dir=r"C:/SPATAIL_MAX/assets_processed/treated/f1_wheel",
        asset_id="f1_wheel",
    )

DESIGN PRINCIPLES:
  - One factor applies to the whole scene. Scale normalization is global.
  - Bbox + class hint. No feature detection. No RANSAC. No CNN.
  - Idempotent: if already within +/-5% of target, no-op (still writes audit).
  - Refuse absurd factors (>1000x or <0.001x) — bad hint, not bad data.
"""

import bpy, json, math, os
from datetime import datetime, timezone
from mathutils import Vector


# ---------------------------------------------------------------------------
# Class hint table — coarse expected real-world diagonals (cm)
# ---------------------------------------------------------------------------

ASSET_HINT_DIAGONALS_CM = {
    "steering_wheel": 32.0,
    "engine":         80.0,
    "dashboard":     180.0,
    "hand_tool":      25.0,
    "full_car":      450.0,
    "chair":          90.0,
    "desk":          160.0,
}


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _world_bbox(obj):
    lo = Vector((float("inf"),) * 3)
    hi = Vector((-float("inf"),) * 3)
    for c in obj.bound_box:
        p = obj.matrix_world @ Vector(c)
        lo = Vector(map(min, lo, p))
        hi = Vector(map(max, hi, p))
    return lo, hi


def _scene_bbox(mesh_objs):
    lo = Vector((float("inf"),) * 3)
    hi = Vector((-float("inf"),) * 3)
    for obj in mesh_objs:
        olo, ohi = _world_bbox(obj)
        lo = Vector(map(min, lo, olo))
        hi = Vector(map(max, hi, ohi))
    return lo, hi


def _diagonal(lo, hi):
    return (hi - lo).length


# ---------------------------------------------------------------------------
# Target resolution
# ---------------------------------------------------------------------------

def _resolve_target(target_diagonal_cm, asset_hint):
    """Return (target_cm, source_dict). Raises ValueError on bad input."""
    if target_diagonal_cm is not None and target_diagonal_cm > 0:
        return float(target_diagonal_cm), {
            "source": "explicit",
            "target_diagonal_cm": float(target_diagonal_cm),
        }
    if asset_hint:
        if asset_hint not in ASSET_HINT_DIAGONALS_CM:
            raise ValueError(
                f"unknown asset_hint: {asset_hint!r}. "
                f"Known: {sorted(ASSET_HINT_DIAGONALS_CM)}. "
                f"Either add it to the table or pass target_diagonal_cm explicitly."
            )
        return ASSET_HINT_DIAGONALS_CM[asset_hint], {
            "source": "class_hint",
            "asset_hint": asset_hint,
            "target_diagonal_cm": ASSET_HINT_DIAGONALS_CM[asset_hint],
        }
    raise ValueError(
        "must supply either target_diagonal_cm=<float> or asset_hint=<str>"
    )


# ---------------------------------------------------------------------------
# Scale application
# ---------------------------------------------------------------------------

def _apply_uniform_scale(mesh_objs, factor):
    """Scale every mesh object uniformly about world origin and bake."""
    # Deselect everything first
    for o in bpy.context.selected_objects:
        o.select_set(False)
    # Select the mesh objects we want to scale
    bpy.context.view_layer.objects.active = mesh_objs[0]
    for obj in mesh_objs:
        obj.select_set(True)
    # Scale about the world origin so the bbox centre also moves to factor*centre.
    # This is fine — treat-mesh re-normalizes origins right after us.
    pivot_prev = bpy.context.scene.tool_settings.transform_pivot_point
    bpy.context.scene.tool_settings.transform_pivot_point = "CURSOR"
    cursor_prev = tuple(bpy.context.scene.cursor.location)
    bpy.context.scene.cursor.location = (0.0, 0.0, 0.0)
    try:
        bpy.ops.transform.resize(value=(factor, factor, factor))
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    finally:
        bpy.context.scene.cursor.location = cursor_prev
        bpy.context.scene.tool_settings.transform_pivot_point = pivot_prev


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def normalize_scale_in_scene(target_diagonal_cm=None,
                              asset_hint=None,
                              out_dir=None,
                              asset_id="asset",
                              tolerance=0.05,
                              absurd_min=0.001,
                              absurd_max=1000.0):
    """Scale the scene so its bbox diagonal matches target_diagonal_cm.

    Args:
        target_diagonal_cm: explicit target in cm. Preferred when known.
        asset_hint: lookup key into ASSET_HINT_DIAGONALS_CM. Used if no
            explicit target.
        out_dir: where to write scale_normalization.json. Defaults to
            C:/SPATAIL_MAX/assets_processed/treated/<asset_id>.
        asset_id: identifier baked into the audit JSON.
        tolerance: if |factor - 1| <= tolerance, treat as no-op.
        absurd_min/absurd_max: refuse factors outside this range.

    Returns:
        Result dict (also written to disk).
    """
    if out_dir is None:
        out_dir = f"C:/SPATAIL_MAX/assets_processed/treated/{asset_id}"

    mesh_objs = [o for o in bpy.data.objects if o.type == "MESH"]
    if not mesh_objs:
        result = {
            "assetId": asset_id,
            "schemaVersion": "0.1.0-spatail-scale-normalize",
            "normalizedAt": datetime.now(timezone.utc).isoformat(),
            "skipped": True,
            "warnings": ["no mesh objects in scene"],
        }
        _write(result, out_dir, asset_id)
        return result

    target_cm, target_meta = _resolve_target(target_diagonal_cm, asset_hint)

    lo0, hi0 = _scene_bbox(mesh_objs)
    diag0 = _diagonal(lo0, hi0)

    warnings = []
    skipped = False
    factor = 1.0

    if diag0 <= 0:
        warnings.append("scene bbox diagonal is zero — refusing to scale")
        skipped = True
    else:
        factor = target_cm / diag0
        if abs(factor - 1.0) <= tolerance:
            warnings.append(
                f"factor {factor:.4f} within +/-{tolerance:.0%} of 1.0 — no-op"
            )
            skipped = True
        elif factor < absurd_min or factor > absurd_max:
            warnings.append(
                f"factor {factor:.4g} outside sane range "
                f"[{absurd_min}, {absurd_max}] — refusing. "
                f"Check the asset_hint or pass target_diagonal_cm explicitly."
            )
            skipped = True

    if not skipped:
        _apply_uniform_scale(mesh_objs, factor)

    lo1, hi1 = _scene_bbox(mesh_objs)
    diag1 = _diagonal(lo1, hi1)

    result = {
        "assetId": asset_id,
        "schemaVersion": "0.1.0-spatail-scale-normalize",
        "normalizedAt": datetime.now(timezone.utc).isoformat(),
        "before": {
            "bbox_diagonal_blender_units": round(diag0, 4),
            "bbox_lo": [round(c, 4) for c in lo0],
            "bbox_hi": [round(c, 4) for c in hi0],
            "mesh_object_count": len(mesh_objs),
        },
        "target": target_meta,
        "factor_applied": round(factor, 6) if not skipped else 1.0,
        "after": {
            "bbox_diagonal_cm": round(diag1, 4),
            "bbox_lo": [round(c, 4) for c in lo1],
            "bbox_hi": [round(c, 4) for c in hi1],
        },
        "skipped": skipped,
        "warnings": warnings,
    }

    _write(result, out_dir, asset_id)
    print(f"[spatail_scale_normalize] factor={result['factor_applied']} "
          f"diag {diag0:.2f} -> {diag1:.2f} cm  skipped={skipped}")
    return result


def _write(result, out_dir, asset_id):
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "scale_normalization.json").replace("\\", "/")
    with open(path, "w") as f:
        json.dump(result, f, indent=2)
    result["_writtenTo"] = path
    return path


print("[spatail_scale_normalize] module loaded.")
