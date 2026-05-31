"""generator.py - prompt -> LIVE Blender (real LLM authoring) -> baked USDZ.

Flow for each job:
  1. PREPARE  - clear the live Blender scene completely and create a single Empty
                "gen_root" at the world origin (guarantees a clean slate per prompt).
  2. AUTHOR   - Claude (via llm_author, using the local `claude` CLI + the user's
                login) writes a Blender-Python script that MODELS a recognizable
                representation of the prompt and ANIMATES the described action as a
                seamless baked loop, parenting everything to gen_root. Executed in
                the LIVE Blender over the MCP socket bridge, with self-repair.
  3. EXPORT   - measure the animated bbox over all of gen_root's descendants, scale
                gen_root to a tabletop footprint, and export an AR-ready USDZ using
                the EXACT studio settings (Y-up, meters_per_unit=1,
                generate_preview_surface, baked animation) per
                docs/generative_ar_contract.md.

No headless Blender is spawned - the user's live Blender does the work and stays
open. There is NO primitive-fallback: if authoring fails, the job errors (we never
silently ship a grey box).
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import blender_bridge as bridge  # noqa: E402
import llm_author  # noqa: E402

FPS = 30
FRAMES = 120                 # 4.0 s seamless loop (matches studio CYCLE)
MAX_DIM_TARGET = 0.9         # <= 1 m tabletop footprint, incl. motion, w/ margin
ROOT_NAME = "gen_root"

# --- 1. PREPARE: wipe the scene, make a fresh gen_root --------------------------
# read_factory_settings is sandbox-blocked (would kill the MCP server), so we
# remove objects + orphan datablocks by hand, then recreate gen_root. This runs
# on EVERY prompt so nothing from a previous generation can leak through.
_PREPARE = r"""
import bpy
N_before = len(bpy.data.objects)
for ob in list(bpy.data.objects):
    bpy.data.objects.remove(ob, do_unlink=True)
for coll in (bpy.data.meshes, bpy.data.materials, bpy.data.curves,
             bpy.data.lights, bpy.data.cameras, bpy.data.images,
             bpy.data.node_groups, bpy.data.armatures, bpy.data.textures):
    for blk in list(coll):
        if blk.users == 0:
            try:
                coll.remove(blk)
            except Exception:
                pass
sc = bpy.context.scene
sc.render.fps = %(FPS)d
sc.frame_start = 1
sc.frame_end = %(FRAMES)d
sc.frame_set(1)
root = bpy.data.objects.new("%(ROOT)s", None)
root.empty_display_size = 0.05
sc.collection.objects.link(root)
result = {"cleared_from": N_before, "objects_now": [o.name for o in bpy.data.objects]}
""" % {"FPS": FPS, "FRAMES": FRAMES, "ROOT": ROOT_NAME}


# --- 3. EXPORT: measure over gen_root's descendants, scale to fit, write USDZ ----
# Operates on ALL mesh descendants of gen_root (so multi-part LLM scenes work),
# not a single hard-coded object. USDZ kwargs are byte-identical to
# studio/blender/build_studio.py::_export_usdz.
_EXPORT = r"""
import bpy, pathlib
from mathutils import Vector
sc = bpy.context.scene
root = bpy.data.objects.get("%(ROOT)s")
N = %(FRAMES)d
USDZ = %(USDZ)r
TARGET = %(TARGET)f

def mesh_descendants(obj):
    out = []
    for c in obj.children_recursive:
        if c.type == "MESH" and c.data and len(c.data.vertices) > 0:
            out.append(c)
    return out

meshes = mesh_descendants(root) if root else []
if not root or not meshes:
    result = {"exported": False, "error": "no mesh geometry parented to gen_root",
              "n_meshes": len(meshes)}
else:
    def animated_bbox():
        lo = [1e18, 1e18, 1e18]; hi = [-1e18, -1e18, -1e18]
        for f in range(1, N + 1):
            sc.frame_set(f); bpy.context.view_layer.update()
            for m in meshes:
                for c in m.bound_box:
                    w = m.matrix_world @ Vector(c)
                    for i in range(3):
                        if w[i] < lo[i]: lo[i] = w[i]
                        if w[i] > hi[i]: hi[i] = w[i]
        return lo, hi
    lo, hi = animated_bbox()
    dims = [hi[i] - lo[i] for i in range(3)]; mx = max(dims)
    if mx > TARGET and mx > 0:
        s = TARGET / mx
        root.scale = (root.scale[0]*s, root.scale[1]*s, root.scale[2]*s)
        bpy.context.view_layer.update()
        lo, hi = animated_bbox()
        dims = [hi[i] - lo[i] for i in range(3)]; mx = max(dims)
    # Blender Z-up -> Y-up (matches studio blender_to_yup: [x, z, -y])
    yup_min = [round(lo[0], 4), round(lo[2], 4), round(-hi[1], 4)]
    yup_max = [round(hi[0], 4), round(hi[2], 4), round(-lo[1], 4)]

    sc.frame_set(1)
    rna = set(bpy.ops.wm.usd_export.get_rna_type().properties.keys())
    desired = {
        "filepath": USDZ,
        "selected_objects_only": False,
        "export_animation": True,
        "export_uvmaps": True,
        "export_normals": True,
        "export_materials": True,
        "export_meshes": True,
        "generate_preview_surface": True,
        "convert_orientation": True,
        "export_global_forward_selection": "NEGATIVE_Z",
        "export_global_up_selection": "Y",
        "meters_per_unit": 1.0,
        "root_prim_path": "/Scene",
        "overwrite_textures": True,
    }
    kwargs = {k: v for k, v in desired.items() if k in rna}
    kwargs["filepath"] = USDZ
    ok = False; err = None
    try:
        bpy.ops.wm.usd_export(**kwargs); ok = True
    except Exception:
        import traceback as _tb
        err = _tb.format_exc()
    result = {"exported": bool(ok and pathlib.Path(USDZ).exists()), "error": err,
              "bbox_yup": {"min": yup_min, "max": yup_max},
              "max_dim": round(mx, 4), "n_meshes": len(meshes),
              "frames": N, "fps": %(FPS)d}
""" % {"ROOT": ROOT_NAME, "FRAMES": FRAMES, "FPS": FPS,
       "TARGET": MAX_DIM_TARGET, "USDZ": "{USDZ}"}


def generate(prompt: str, job_id: str, out_dir, on_stage=lambda s: None) -> dict:
    """Prepare -> author (LLM) -> export. Returns artifact names + bbox.
    Raises on failure (no primitive fallback)."""
    if bridge.ping() is None:
        raise bridge.BridgeError(
            "Blender MCP bridge not reachable on localhost:9876 - open Blender and "
            "make sure the 'MCP' add-on server is running.")
    if not llm_author.available():
        raise RuntimeError(
            "No authoring backend: the `claude` CLI was not found. Real generation "
            "requires it (set SPATAIL_CLAUDE_CLI to claude.exe).")

    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    usdz_path = out_dir / f"{job_id}.usdz"
    usdz_for_blender = str(usdz_path).replace("\\", "/")

    # 1. PREPARE — guaranteed clean scene every prompt
    on_stage("clearing scene")
    bridge.run_code(_PREPARE, timeout=60.0)

    # 2. AUTHOR — Claude writes + (self-)repairs the Blender script, run live
    authored = llm_author.author_scene(
        prompt, FRAMES, FPS, bridge.run_code, on_stage=on_stage)

    # 3. EXPORT — measure, scale to tabletop, write USDZ
    on_stage("exporting")
    export_code = _EXPORT.replace("{USDZ}", usdz_for_blender)
    res = bridge.run_code(export_code, timeout=180.0)
    if not res.get("exported"):
        raise bridge.BridgeError(f"USDZ export failed: {res.get('error')}")

    # metadata (reuses the studio metadata shape)
    meta = {
        "sceneId": job_id,
        "title": (prompt or "Generated object").strip()[:80],
        "prompt": prompt,
        "generator": "spatail.generative.v0.2-llm",
        "authoring": {"method": authored.get("method"),
                      "attempts": authored.get("attempts")},
        "frame": "y_up_m",
        "mode": "ar_single",
        "usdz": f"{job_id}.usdz",
        "animation": {"clip": "gen_demo", "fps": res["fps"], "frames": res["frames"],
                      "seconds": round(res["frames"] / res["fps"], 2), "loop": True},
        "bbox_yup_m": res["bbox_yup"],
        "max_dim_m": res["max_dim"],
        "blender_summary": authored.get("blender_result"),
    }
    meta_path = out_dir / f"{job_id}_metadata.json"
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    on_stage("ready")

    return {
        "usdz_name": usdz_path.name,
        "metadata_name": meta_path.name,
        "bbox_yup": res["bbox_yup"],
        "max_dim": res["max_dim"],
        "authoring": meta["authoring"],
        "blender_summary": authored.get("blender_result"),
    }
