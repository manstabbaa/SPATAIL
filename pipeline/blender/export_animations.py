"""
export_animations.py — Blender authoring → SPATAIL animation sidecar.

Run via:
    blender --background <file>.blend \
        --python pipeline/blender/export_animations.py \
        -- <output_dir> <asset_id>

What it does
------------
Walks every Object in the scene that has an animation_data block, looks at
its NLA tracks, and emits two artefacts under <output_dir>:

  <asset_id>.glb               # standard glTF export — baked transform tracks
                               # for any `transform_keyframes` primitive ride
                               # along inside this file
  <asset_id>.animations.json   # sidecar listing every primitive call the
                               # author wrote, with params + start/end frames
                               # converted to seconds

Authoring convention
--------------------
One NLA track per primitive invocation. Name the track:

    <primitive>__<targetElementId>[__paramOverrides]

Examples:

    explode__air_filter_assembly
    highlight_pulse__elem_dirty_filter_finding_6
    fade__elem_kpi_summary_0__from=0&to=1

The script splits on `__`, takes the first token as the primitive name,
the second as the contract element id, and parses any remaining
`k=v&k=v` segment into params. If the primitive is `transform_keyframes`,
the actual keyframes get baked into the glTF — no params needed.

Why this lives in pipeline/blender (not blender_tools/)
-------------------------------------------------------
`blender_tools/analyze_asset.py` is the headless import probe used by the
asset normalizer. This script is for the *animation authoring* flow,
which is a separate axis. Keeping them apart means "fix the importer"
and "add a new animation primitive" don't fight in the same file.
"""

import json
import os
import sys
from urllib.parse import parse_qs

try:
    import bpy  # type: ignore
except ImportError:
    bpy = None  # makes the file importable for linting outside Blender


# Closed set of primitive names. Kept in lockstep with the contract's
# ANIMATION_PRIMITIVES vocab (pipeline/spatail/experience_contract.js).
PRIMITIVES = {
    "transform_keyframes",
    "explode",
    "assemble",
    "highlight_pulse",
    "fade",
    "set_visible",
    "attention_camera_hint",
}


def parse_track_name(name: str):
    """Returns (primitive, target_element_id, params_dict) or None."""
    parts = name.split("__")
    if len(parts) < 2:
        return None
    primitive = parts[0].strip()
    target = parts[1].strip()
    if primitive not in PRIMITIVES or not target:
        return None
    params = {}
    if len(parts) >= 3:
        for key, values in parse_qs(parts[2]).items():
            value = values[0]
            if value.lower() in ("true", "false"):
                params[key] = value.lower() == "true"
            else:
                try:
                    params[key] = float(value)
                except ValueError:
                    params[key] = value
    return primitive, target, params


def collect_animations(fps: float):
    """Walks every object with NLA tracks and emits one animation entry
    per parsed track. Returns a list of dicts ready for the sidecar JSON.
    """
    if bpy is None:
        return []
    animations = []
    for obj in bpy.data.objects:
        if not obj.animation_data:
            continue
        for track in obj.animation_data.nla_tracks:
            parsed = parse_track_name(track.name)
            if not parsed:
                continue
            primitive, target, params = parsed
            # Time range from the leftmost / rightmost strip on the track.
            if not track.strips:
                continue
            f0 = min(s.frame_start for s in track.strips)
            f1 = max(s.frame_end for s in track.strips)
            duration = max((f1 - f0) / fps, 0.0)
            animations.append({
                "id": "anim.%s.%s" % (primitive, target),
                "primitive": primitive,
                "target": target,
                "duration": round(duration, 4),
                "easing": params.pop("easing", "ease-out-cubic"),
                "params": params,
                "_authoring": {
                    "objectName": obj.name,
                    "trackName": track.name,
                    "frameStart": int(f0),
                    "frameEnd": int(f1),
                },
            })
    return animations


def export_glb(output_path: str):
    if bpy is None:
        return False
    bpy.ops.export_scene.gltf(
        filepath=output_path,
        export_format="GLB",
        export_apply=True,
        export_animations=True,
        export_force_sampling=True,
        export_nla_strips=True,
    )
    return True


def main():
    if bpy is None:
        print("[export_animations] must be run inside Blender (--python)", file=sys.stderr)
        sys.exit(2)

    # Blender swallows everything before "--"; the rest is ours.
    try:
        argv = sys.argv[sys.argv.index("--") + 1:]
    except ValueError:
        argv = []
    if len(argv) < 2:
        print("usage: blender --background <file>.blend --python export_animations.py "
              "-- <output_dir> <asset_id>", file=sys.stderr)
        sys.exit(2)
    output_dir, asset_id = argv[0], argv[1]
    os.makedirs(output_dir, exist_ok=True)

    fps = bpy.context.scene.render.fps / max(bpy.context.scene.render.fps_base, 1e-6)
    animations = collect_animations(fps)

    glb_path = os.path.join(output_dir, "%s.glb" % asset_id)
    sidecar = os.path.join(output_dir, "%s.animations.json" % asset_id)

    glb_ok = export_glb(glb_path)

    with open(sidecar, "w", encoding="utf-8") as fh:
        json.dump({
            "assetId": asset_id,
            "fps": fps,
            "glb": os.path.basename(glb_path) if glb_ok else None,
            "animations": animations,
        }, fh, indent=2)

    print("[export_animations] wrote %d animation entries to %s" % (
        len(animations), sidecar))
    if glb_ok:
        print("[export_animations] wrote glTF to %s" % glb_path)


if __name__ == "__main__":
    main()
