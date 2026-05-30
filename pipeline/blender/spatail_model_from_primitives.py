"""
spatail_model_from_primitives.py — generative modeling stage for SPATAIL XR.

When a manual has no matching library asset, we BUILD the asset. This module is
the deterministic executor for that: it takes a *build plan* (a JSON description
of parts as primitives + transforms + roles) and constructs the parts in a
fresh, dedicated Blender scene — never touching whatever scene is already open.

Pipeline position (the user's workflow):
    manual → deep understanding → per-part PLAN  ← (an LLM emits this)
           → BUILD parts from primitives  ← THIS MODULE
           → combine meshes
           → multiview refine (spatail_mesh_select)
           → step-by-step interactive demo

It also emits a part-registry dict in the same shape walkthrough.py / the
contract author consume (parts{name:{role,aliases}}, kinematicGroups, aliases,
bbox, director_hints), so a generated asset flows through the rest of the stack
exactly like a curated one.

Build-plan schema (units are whatever `units` says; we author in cm):
    {
      "assetId": "shelving_unit",
      "kind": "shelving unit",
      "units": "cm",
      "up_axis": "z",
      "parts": [
        {"name": "side_left", "role": "side_panel",
         "aliases": ["left side", "left panel"],
         "primitive": "box", "size": [2, 30, 90], "location": [-15, 0, 45]},
        {"name": "shelf_1", "role": "shelf", "primitive": "box",
         "size": [28, 30, 2], "location": [0, 0, 30]},
        ...
      ],
      "groups": [{"group_id": "frame", "members": ["side_left", "side_right"]}],
      "assembly_order": ["bottom", "side_left", "side_right", "top", "shelf_1"]
    }

Primitives: box {size:[x,y,z]}, cylinder {radius, depth, axis:'x'|'y'|'z'},
            tube {radius, inner_radius, depth, axis}.

USAGE in Blender:
    import sys; sys.path.insert(0, r"C:/SPATAIL_MAX/pipeline/blender")
    import importlib, json, spatail_model_from_primitives as mp
    importlib.reload(mp)
    plan = json.load(open(r"C:/.../shelving_unit.plan.json"))
    res = mp.build_from_plan(plan)
    json.dump(res["registry"], open(r"C:/.../shelving_unit_part_registry.json","w"), indent=2)
"""
import bpy
import bmesh
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from mathutils import Vector, Matrix


# A small, distinct palette so generated parts read apart from each other.
_ROLE_COLORS = {
    "side_panel": (0.62, 0.46, 0.32, 1.0),
    "panel":      (0.62, 0.46, 0.32, 1.0),
    "shelf":      (0.78, 0.62, 0.44, 1.0),
    "top":        (0.55, 0.40, 0.28, 1.0),
    "bottom":     (0.55, 0.40, 0.28, 1.0),
    "back":       (0.42, 0.42, 0.46, 1.0),
    "back_panel": (0.42, 0.42, 0.46, 1.0),
    "door":       (0.70, 0.55, 0.40, 1.0),
    "leg":        (0.30, 0.30, 0.33, 1.0),
    "fastener":   (0.80, 0.80, 0.82, 1.0),
    "_default":   (0.70, 0.70, 0.72, 1.0),
}

_AXIS_VEC = {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}


# ─────────────────────────────────────────────────────────────────────────
# Scene management (non-destructive: build into a dedicated scene)
# ─────────────────────────────────────────────────────────────────────────

def _get_or_make_scene(name, clear=True):
    scn = bpy.data.scenes.get(name)
    if scn is None:
        scn = bpy.data.scenes.new(name)
    elif clear:
        for o in list(scn.collection.objects):
            m = o.data
            scn.collection.objects.unlink(o)
            if o.users == 0:
                bpy.data.objects.remove(o)
            if m and getattr(m, "users", 1) == 0:
                try:
                    bpy.data.meshes.remove(m)
                except Exception:
                    pass
    return scn


def _role_material(role):
    key = f"SPATAIL_gen_{role}"
    mat = bpy.data.materials.get(key)
    if mat:
        return mat
    mat = bpy.data.materials.new(key)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    color = _ROLE_COLORS.get(role, _ROLE_COLORS["_default"])
    if bsdf:
        bsdf.inputs["Base Color"].default_value = color
        if "Roughness" in bsdf.inputs:
            bsdf.inputs["Roughness"].default_value = 0.7
    mat.diffuse_color = color
    return mat


# ─────────────────────────────────────────────────────────────────────────
# Primitive builders → return a mesh datablock (origin at geometry centre)
# ─────────────────────────────────────────────────────────────────────────

def _mesh_box(name, size):
    sx, sy, sz = size
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=Vector((sx, sy, sz)), verts=bm.verts)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    return me


def _mesh_cylinder(name, radius, depth, axis="z", segments=32):
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=segments,
                          radius1=radius, radius2=radius, depth=depth)
    _orient_along_axis(bm, axis)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    return me


def _mesh_tube(name, radius, inner_radius, depth, axis="z", segments=32):
    """Annular tube: outer cylinder minus inner, capped as a ring."""
    bm = bmesh.new()
    # outer + inner rings at +/- depth/2, bridged into walls + end rings.
    half = depth / 2.0
    for z in (-half, half):
        for r in (radius, inner_radius):
            bmesh.ops.create_circle(bm, cap_ends=False, segments=segments,
                                    radius=r, matrix=Matrix.Translation((0, 0, z)))
    # Simpler robust approach: build via two cones and boolean is heavy; instead
    # just return a solid cylinder when inner_radius<=0, else a thin-walled tube
    # approximated by an outer cylinder (inner detail is rarely load-bearing for
    # an assembly walkthrough). Keep it a solid cylinder for determinism.
    bm.free()
    return _mesh_cylinder(name, radius, depth, axis, segments)


def _orient_along_axis(bm, axis):
    if axis == "z":
        return
    if axis == "x":
        rot = Matrix.Rotation(math.radians(90), 4, "Y")
    elif axis == "y":
        rot = Matrix.Rotation(math.radians(-90), 4, "X")
    else:
        return
    bmesh.ops.transform(bm, matrix=rot, verts=bm.verts)


def _build_mesh(part):
    name = part["name"]
    prim = part.get("primitive", "box")
    if prim == "box":
        return _mesh_box(name, part["size"])
    if prim == "cylinder":
        return _mesh_cylinder(name, part["radius"], part["depth"],
                              part.get("axis", "z"), part.get("segments", 32))
    if prim == "tube":
        return _mesh_tube(name, part["radius"], part.get("inner_radius", 0),
                          part["depth"], part.get("axis", "z"),
                          part.get("segments", 32))
    raise ValueError(f"Unknown primitive {prim!r} for part {name!r}")


def _mesh_from_cad_payload(name, mesh_path):
    """Build a mesh datablock from a baked CAD payload (.npz: verts Nx3 + faces).

    The payload is produced by pipeline/cad/spatail_cad_build.py: vertices are
    already in Blender's Z-up frame, recentred on the origin, and in METRES, so
    this drops in exactly where a primitive mesh would — no ops, no transforms,
    safe in a non-active scene. Returns a mesh datablock, or None on failure so
    the caller can fall back to the primitive.
    """
    try:
        import numpy as np
        with np.load(mesh_path) as data:
            verts = np.asarray(data["verts"], dtype=float)
            faces = np.asarray(data["faces"], dtype=int)
        if verts.size == 0 or faces.size == 0:
            return None
        me = bpy.data.meshes.new(name)
        me.from_pydata([tuple(v) for v in verts.tolist()], [],
                       [tuple(f) for f in faces.tolist()])
        me.update()
        me.validate(verbose=False)
        # Smooth shading reads the eased CAD edges better than faceted flat shading.
        try:
            for poly in me.polygons:
                poly.use_smooth = True
        except Exception:
            pass
        return me
    except Exception as e:
        print(f"[spatail_model_from_primitives] CAD payload load failed for "
              f"{name!r} ({mesh_path}): {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────
# Build plan → scene + registry
# ─────────────────────────────────────────────────────────────────────────

def _load_cad_manifest(cad_manifest):
    """Accept a manifest dict, a path to a manifest JSON, or None.
    Returns {part_name: entry} (possibly empty)."""
    if not cad_manifest:
        return {}
    data = cad_manifest
    if isinstance(cad_manifest, (str, bytes)) or hasattr(cad_manifest, "__fspath__"):
        try:
            data = json.loads(Path(cad_manifest).read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[spatail_model_from_primitives] could not read CAD manifest "
                  f"{cad_manifest}: {e}")
            return {}
    if not isinstance(data, dict):
        return {}
    return data.get("parts", {}) or {}


def build_from_plan(plan, scene_name=None, clear=True, make_active=False,
                    cad_manifest=None):
    """Construct every part of a build plan into a dedicated scene.

    make_active : if True, switch Blender's active scene to the new one (needed
                  before render/export). Default False so the user's open scene
                  is left in front.
    cad_manifest: optional manifest (dict or path) from the CAD generation stage
                  (pipeline/cad/spatail_cad_build.py). Parts present there with a
                  baked mesh payload are built as REAL CAD geometry; parts absent
                  fall back to their primitive. Default None → 100% primitives.
    Returns {scene, n_parts, n_cad, registry, bbox}.
    """
    asset_id = plan.get("assetId", "generated_asset")
    scene_name = scene_name or f"SPATAIL_{asset_id}"
    scn = _get_or_make_scene(scene_name, clear=clear)

    cad_parts = _load_cad_manifest(cad_manifest)
    n_cad = 0
    n_objects = 0

    parts_meta = {}
    aliases = {}
    lo = Vector((float("inf"),) * 3)
    hi = Vector((-float("inf"),) * 3)

    def _seat(obj_name, mesh, loc, rot):
        """Create one object from `mesh`, seat it, link it, accumulate bbox.

        The new scene isn't the active view layer, so obj.matrix_world isn't
        evaluated yet — use matrix_basis (built synchronously from loc/rot/scale;
        generated parts have no parents)."""
        nonlocal lo, hi, n_objects
        obj = bpy.data.objects.new(obj_name, mesh)
        obj.location = Vector(loc)
        if rot:
            obj.rotation_euler = Vector(rot)
        obj["spatail_role"] = role
        scn.collection.objects.link(obj)
        mb = obj.matrix_basis
        for c in obj.bound_box:
            p = mb @ Vector(c)
            lo = Vector(map(min, lo, p))
            hi = Vector(map(max, hi, p))
        n_objects += 1
        return obj

    for part in plan.get("parts", []):
        name = part["name"]
        role = part.get("role", "part")
        cad_entry = cad_parts.get(name) or {}
        cad_mesh_path = cad_entry.get("mesh")
        me = None
        is_cad = False
        if cad_mesh_path and os.path.exists(cad_mesh_path):
            me = _mesh_from_cad_payload(name, cad_mesh_path)
            is_cad = me is not None
        if me is None:
            me = _build_mesh(part)        # primitive fallback
        if is_cad:
            n_cad += 1
        me.materials.append(_role_material(role))

        al = part.get("aliases", []) or []
        instances = part.get("instances")
        if instances:
            # One prototype mesh placed N times (e.g. 12 dowels). Objects share
            # the mesh datablock (cheap; glTF exports it once, instanced by node).
            placed = []
            for k, inst in enumerate(instances):
                if isinstance(inst, dict):
                    iloc = inst.get("location", [0, 0, 0])
                    irot = inst.get("rotation_euler")
                else:
                    iloc, irot = inst, None
                # First instance keeps the bare name so the registry entry has a
                # matching GLB node; the rest are <name>_NN.
                obj_name = name if k == 0 else f"{name}_{k + 1:02d}"
                _seat(obj_name, me, iloc, irot)
                placed.append(iloc)
            cen = ([round(sum(p[i] for p in placed) / len(placed), 4) for i in range(3)]
                   if placed else [0, 0, 0])
            parts_meta[name] = {
                "role": role,
                "aliases": al,
                "primitive": part.get("primitive", "box"),
                "cad": bool(is_cad),
                "location": cen,
                "instances": len(placed),
                "_hardware": bool(part.get("_hardware")),
            }
        else:
            loc = part.get("location", [0, 0, 0])
            _seat(name, me, loc, part.get("rotation_euler"))
            parts_meta[name] = {
                "role": role,
                "aliases": al,
                "primitive": part.get("primitive", "box"),
                "cad": bool(is_cad),
                "location": [round(c, 4) for c in loc],
                "_hardware": bool(part.get("_hardware")),
            }
        for a in al:
            aliases[a.lower()] = name

    # kinematicGroups from plan groups (default: all parts in a "frame" group,
    # no motion — an assembly is static unless the plan says otherwise).
    groups = []
    for g in plan.get("groups", []):
        groups.append({
            "group_id": g["group_id"],
            "members": g.get("members", []),
            "driven_by_action": g.get("driven_by_action"),
            "pivot_world": g.get("pivot_world"),
            "rotation_axis_world": g.get("rotation_axis_world"),
        })

    size = (hi - lo)
    bbox = {
        "min": [round(c, 4) for c in lo],
        "max": [round(c, 4) for c in hi],
        "size": [round(c, 4) for c in size],
        "center": [round(c, 4) for c in (lo + hi) * 0.5],
    }

    registry = {
        "asset": f"{asset_id}.glb",
        "assetId": asset_id,
        "_generated": True,
        "_generatedAt": datetime.now(timezone.utc).isoformat(),
        "units": plan.get("units", "cm"),
        "up_axis": plan.get("up_axis", "z"),
        "kind": plan.get("kind", asset_id),
        "parts": parts_meta,
        "aliases": aliases,
        "kinematicGroups": groups,
        "engine_bbox": bbox,
        "assembly_order": plan.get("assembly_order",
                                   [p["name"] for p in plan.get("parts", [])]),
        "director_hints": plan.get("director_hints", {
            "asset_kind": plan.get("kind", asset_id),
            "background_default": "#F5F4EF",
            "narration_tone": "concise, instructional — this is an assembly walkthrough",
        }),
    }

    if make_active:
        bpy.context.window.scene = scn

    registry["_n_cad_parts"] = n_cad
    registry["_n_objects"] = n_objects
    return {
        "scene": scene_name,
        "n_parts": len(parts_meta),
        "n_cad": n_cad,
        "n_objects": n_objects,
        "registry": registry,
        "bbox": bbox,
    }


def scene_summary(scene_name):
    """Lightweight check: list objects + bbox of a built scene."""
    scn = bpy.data.scenes.get(scene_name)
    if not scn:
        return {"error": "no scene", "scene": scene_name}
    objs = [{"name": o.name, "role": o.get("spatail_role"),
             "loc": [round(c, 3) for c in o.location],
             "verts": len(o.data.vertices)}
            for o in scn.collection.objects if o.type == "MESH"]
    return {"scene": scene_name, "n": len(objs), "objects": objs}


print("[spatail_model_from_primitives] module loaded.")
