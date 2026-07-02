"""normalize_bounds.py — fit an asset into fixed bounds + seat its origin (bpy).

The core SPATAIL rule: every exported asset must fit inside the configured target
bounds volume. We scale the whole group about the WORLD origin (by multiplying each
object's location and scale, then applying), so multi-part assets keep their
relative layout, then translate so the origin sits where `origin_mode` asks.

Bounds modes
  fit_longest_axis (default) : uniform scale so the largest source dimension equals
                               the largest target dimension — preserves proportions.
  fit_inside_box             : uniform scale by the most-constraining axis so the
                               asset fits inside x/y/z without exceeding any axis.
  stretch_to_box             : non-uniform scale to exactly fill x/y/z (can distort).

Origin modes
  bottom_center (default)    : x/y centred on the origin, bbox bottom on z = 0.
  center                     : bbox centre at the world origin.
"""
from __future__ import annotations

import bpy

from inspect_asset import world_bbox  # type: ignore
from validation import within_bounds   # type: ignore

_EPS = 1e-9


def normalize(meshes, target_bounds: dict, bounds_mode: str, origin_mode: str) -> dict:
    """Scale + seat the meshes; return a normalization record for the report."""
    original = world_bbox(meshes)
    s_vec, s_scalar = _scale_vector(original["size"], target_bounds, bounds_mode)
    _scale_group(meshes, s_vec)

    scaled = world_bbox(meshes)
    offset = _origin_offset(scaled, origin_mode)
    _translate_group(meshes, offset)

    final = world_bbox(meshes)
    ok = within_bounds(final["size"], target_bounds)

    return {
        "bounds_mode": bounds_mode,
        "origin_mode": origin_mode,
        "target_bounds_m": dict(target_bounds),
        "original_bbox": original,
        "scaled_bbox": scaled,
        "final_bbox": final,
        "scale_vector": [round(v, 6) for v in s_vec],
        "scale_factor_applied": round(s_scalar, 6),
        "origin_offset_applied": [round(v, 6) for v in offset],
        "fits_in_target_bounds": bool(ok),
    }


def _scale_vector(size, tb: dict, mode: str):
    """Return (per-axis scale vector, representative scalar) for the mode."""
    sx, sy, sz = (max(d, _EPS) for d in size)
    tx, ty, tz = tb.get("x", 1.0), tb.get("y", 1.0), tb.get("z", 1.0)

    if mode == "stretch_to_box":
        s = (tx / sx, ty / sy, tz / sz)
        return s, max(s)
    if mode == "fit_inside_box":
        s = min(tx / sx, ty / sy, tz / sz)
        return (s, s, s), s
    # fit_longest_axis (default): longest source dim → longest target dim
    longest_src = max(size) or _EPS
    longest_tgt = max(tx, ty, tz)
    s = longest_tgt / longest_src
    return (s, s, s), s


def _origin_offset(bbox: dict, origin_mode: str):
    mn, c = bbox["min"], bbox["center"]
    if origin_mode == "center":
        return (-c[0], -c[1], -c[2])
    return (-c[0], -c[1], -mn[2])           # bottom_center (default)


def _select(meshes):
    bpy.ops.object.select_all(action="DESELECT")
    for o in meshes:
        o.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]


def _scale_group(meshes, s) -> None:
    """Uniformly/non-uniformly scale the group ABOUT THE WORLD ORIGIN.

    Setting location' = s ⊙ location and scale' = s makes every world point map to
    s ⊙ world; applying scale bakes it in while leaving the (already-scaled)
    location, so the group scales as one rigid layout about (0,0,0).
    """
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for o in meshes:
        loc = o.location
        o.location = (loc.x * s[0], loc.y * s[1], loc.z * s[2])
        o.scale = (o.scale.x * s[0], o.scale.y * s[1], o.scale.z * s[2])
    bpy.context.view_layer.update()
    _select(meshes)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    bpy.context.view_layer.update()


def _translate_group(meshes, offset) -> None:
    for o in meshes:
        loc = o.location
        o.location = (loc.x + offset[0], loc.y + offset[1], loc.z + offset[2])
    bpy.context.view_layer.update()
    _select(meshes)
    bpy.ops.object.transform_apply(location=True, rotation=False, scale=False)
    bpy.context.view_layer.update()
