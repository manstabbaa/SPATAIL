"""
spatail_build_from_plan_driver.py — headless build driver for the generative
manual->XR path. Mirrors spatail_bake_one_animation.py: run by the generative
bridge as `blender --background --python <this> -- <spec.json>`.

It reads a build spec, constructs every part of the plan into a dedicated
Blender scene (never touching any open scene), computes per-part ASSEMBLY
OFFSETS (the exploded entrance vector, in the EXPORTED glTF frame so the web
runtime can tween parts in/out of place), exports a meters-scale Y-up GLB, and
writes the part registry + an animation library carrying the assembly metadata.

Spec JSON (sys.argv after '--'):
  {
    "assetId":       "gen_kallax",
    "plan":          { ...build plan, authored in cm... },
    "glb_path":      ".../engine/gen_kallax.glb",
    "registry_path": ".../engine/gen_kallax_part_registry.json",
    "anim_path":     ".../engine/gen_kallax_animation_library.json",
    "result_path":   ".../gen_kallax.build_result.json"   # optional
  }

Result JSON (sidecar at result_path or <glb_path>.build_result.json):
  {"ok": true, "assetId", "glb_path", "registry_path", "anim_path",
   "n_parts", "bbox_m", "camera_presets", "assembly_offsets"}
or {"ok": false, "error": "...", "trace": "..."}

The plan is authored in CENTIMETRES (the segment agent's schema); we scale it
to METRES here so the GLB drops straight into the meters/Y-up web runtime.
"""
import bpy
import json
import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, r"C:/SPATAIL_MAX/pipeline/blender")
import importlib
import spatail_model_from_primitives as mp
importlib.reload(mp)

CM_TO_M = 0.01


def _argv_spec_path():
    if "--" in sys.argv:
        return sys.argv[sys.argv.index("--") + 1]
    return sys.argv[-1]


def plan_to_meters(plan):
    """Deep-copy the plan with every length scaled cm -> m."""
    out = json.loads(json.dumps(plan))
    out["units"] = "m"
    for p in out.get("parts", []):
        if "size" in p:
            p["size"] = [v * CM_TO_M for v in p["size"]]
        if "location" in p:
            p["location"] = [v * CM_TO_M for v in p["location"]]
        for k in ("radius", "inner_radius", "depth"):
            if k in p:
                p[k] = p[k] * CM_TO_M
        # Hardware parts carry many placements (instances); scale each.
        if "instances" in p and isinstance(p["instances"], list):
            scaled = []
            for inst in p["instances"]:
                if isinstance(inst, dict):
                    d = dict(inst)
                    if "location" in d:
                        d["location"] = [v * CM_TO_M for v in d["location"]]
                    scaled.append(d)
                else:
                    scaled.append([v * CM_TO_M for v in inst])
            p["instances"] = scaled
    return out


def compute_assembly_offsets(registry):
    """Per-part exploded entrance vector, expressed in the EXPORTED glTF frame
    (metres, Y-up). Blender Z-up (x,y,z) maps to glTF (x, z, -y).

    Role-aware so the explode reads like a real flat-pack: side panels slide
    out sideways, the top lifts up, the bottom drops down, shelves slide out
    the front. Everything else pushes out along its dominant offset axis.
    """
    bb = registry["engine_bbox"]
    sx, sy, sz = bb["size"]          # Blender: x=width, y=depth, z=height
    cx, cy, cz = bb["center"]
    gsx, gsy, gsz = sx, sz, sy       # glTF sizes: X=width, Y=height, Z=depth
    gc = (cx, cz, -cy)               # asset centre in glTF frame
    maxdim = max(gsx, gsy, gsz) or 1.0

    offsets = {}
    for name, meta in registry["parts"].items():
        bx, by, bz = meta["location"]            # metres, Blender frame
        g = (bx, bz, -by)                        # seated position, glTF frame
        role = (meta.get("role") or "").lower()
        # Hardware/fasteners stay put (they're laid out in a tray, not part of
        # the explode/assemble of the structural shell).
        if role in ("fastener", "hardware") or meta.get("_hardware"):
            offsets[name] = [0.0, 0.0, 0.0]
            continue
        if role in ("side_panel", "side", "panel"):
            o = [(1.0 if g[0] >= 0 else -1.0) * gsx * 1.1, 0.0, 0.0]
        elif role == "top":
            o = [0.0, gsy * 0.55, 0.0]
        elif role == "bottom":
            o = [0.0, -gsy * 0.55, 0.0]
        elif role == "shelf":
            o = [0.0, 0.0, gsz * 1.6]
        elif role in ("back", "back_panel"):
            o = [0.0, 0.0, -gsz * 1.4]
        else:
            rel = [g[i] - gc[i] for i in range(3)]
            axis = max(range(3), key=lambda i: abs(rel[i]))
            mag = 0.6 * maxdim
            o = [0.0, 0.0, 0.0]
            o[axis] = (1.0 if rel[axis] >= 0 else -1.0) * mag if abs(rel[axis]) > 1e-6 else mag
        offsets[name] = [round(v, 5) for v in o]
    return offsets


def compute_camera_presets(registry):
    """Asset-scaled camera presets (glTF frame, metres, Y-up). A 1.5 m shelf
    needs to stand back far more than a 0.5 m engine, so we derive distances
    from the asset's exported bounding box rather than reusing engine presets."""
    import math
    bb = registry["engine_bbox"]
    sx, sy, sz = bb["size"]
    cx, cy, cz = bb["center"]
    gsx, gsy, gsz = sx, sz, sy             # glTF sizes
    gcx, gcy, gcz = cx, cz, -cy            # glTF centre
    fov = 34.0
    maxdim = max(gsx, gsy, gsz) or 0.5
    dist = (maxdim * 1.45) / (2 * math.tan(math.radians(fov / 2)))
    cy_ = round(gcy, 4)
    to = [round(gcx, 4), cy_, round(gcz, 4)]

    def at(dirvec):
        import math as _m
        n = _m.sqrt(sum(d * d for d in dirvec)) or 1.0
        u = [d / n for d in dirvec]
        return [round(to[i] + u[i] * dist, 4) for i in range(3)]

    return {
        "hero_threequarter": {"from": at([1.0, 0.55, 1.0]), "to": to, "fov": fov},
        "hero_front":        {"from": at([0.0, 0.35, 1.4]), "to": to, "fov": fov},
        "topdown":           {"from": [to[0], round(to[1] + dist, 4), to[2] + 0.01],
                              "to": to, "fov": fov},
        "section_side":      {"from": at([1.4, 0.3, 0.0]), "to": to, "fov": fov},
    }


def export_glb(scn, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    vl = scn.view_layers[0] if scn.view_layers else None
    kwargs = dict(
        filepath=path, export_format="GLB",
        use_active_scene=True, use_selection=False,
        use_visible=False,            # include any hidden region overlays
        export_apply=True, export_yup=True, export_cameras=False,
    )
    try:
        with bpy.context.temp_override(scene=scn, view_layer=vl):
            bpy.ops.export_scene.gltf(**kwargs)
    except Exception:
        # Fallback: make the scene active the old-fashioned way if possible.
        try:
            if bpy.context.window:
                bpy.context.window.scene = scn
        except Exception:
            pass
        bpy.ops.export_scene.gltf(**kwargs)
    return path


def main():
    spec_path = _argv_spec_path()
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    glb_path = spec["glb_path"]
    result_path = spec.get("result_path") or (glb_path + ".build_result.json")

    try:
        plan = spec["plan"]
        asset_id = spec.get("assetId") or plan.get("assetId", "generated_asset")
        plan["assetId"] = asset_id
        plan_m = plan_to_meters(plan)

        # Optional CAD manifest (from pipeline/cad/spatail_cad_build.py): parts
        # listed there are imported as REAL build123d/OpenCascade geometry; the
        # rest fall back to primitives. The manifest's baked meshes are already
        # metres + Z-up + centred, so they seat by plan location like primitives.
        cad_manifest = spec.get("cad_manifest")

        # Build every part into a dedicated scene (non-destructive).
        res = mp.build_from_plan(plan_m, make_active=False, cad_manifest=cad_manifest)
        scn = bpy.data.scenes.get(res["scene"])
        registry = res["registry"]

        # Enrich the registry with assembly metadata (used by explode/assemble).
        offsets = compute_assembly_offsets(registry)
        order = registry.get("assembly_order") or list(registry["parts"].keys())
        for name, off in offsets.items():
            if name in registry["parts"]:
                registry["parts"][name]["assembly_offset"] = off
        registry["assembly"] = {"order": order, "offsets": offsets}
        camera_presets = compute_camera_presets(registry)
        registry["camera_presets"] = camera_presets

        # Export the static GLB (metres, Y-up).
        export_glb(scn, glb_path)

        # Write the registry + an animation library that carries the assembly
        # metadata. The assembly is a RUNTIME tween (explode/assemble actions),
        # so there are no baked glTF clips — animations stays empty.
        registry_path = spec["registry_path"]
        anim_path = spec["anim_path"]
        Path(registry_path).write_text(json.dumps(registry, indent=2), encoding="utf-8")
        anim_lib = {
            "assetId": asset_id,
            "_notes": "Generated flat-pack asset. Assembly is a runtime tween "
                      "(explode/assemble actions over per-part offsets), so no "
                      "baked glTF clips.",
            "animations": {},
            "assembly": {"order": order, "offsets": offsets},
        }
        Path(anim_path).write_text(json.dumps(anim_lib, indent=2), encoding="utf-8")

        Path(result_path).write_text(json.dumps({
            "ok": True, "assetId": asset_id, "glb_path": glb_path,
            "registry_path": registry_path, "anim_path": anim_path,
            "n_parts": res["n_parts"], "n_cad": res.get("n_cad", 0),
            "bbox_m": registry["engine_bbox"],
            "camera_presets": camera_presets, "assembly_offsets": offsets,
        }, indent=2), encoding="utf-8")
        print(f"[build_driver] OK {asset_id}: {res['n_parts']} parts "
              f"({res.get('n_cad', 0)} CAD) -> {glb_path}")
    except Exception as e:
        Path(result_path).write_text(json.dumps({
            "ok": False, "error": str(e), "trace": traceback.format_exc(),
        }, indent=2), encoding="utf-8")
        print(f"[build_driver] FAILED: {e}")


main()
