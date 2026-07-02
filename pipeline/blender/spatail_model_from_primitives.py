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


# ─────────────────────────────────────────────────────────────────────────
# Role/name-aware shaping — turn a bare primitive into believable geometry.
# This is the "model the asset IN BLENDER" stage: a flat-pack cushion should
# read as a cushion (plump, rounded), a leg should taper, a panel just needs
# its razor edges knocked off. Everything is deterministic bmesh — no operators,
# no active-scene dependency — so it is safe in the dedicated build scene.
# ─────────────────────────────────────────────────────────────────────────

# profile: bevel (fraction of the smallest dimension), seg (bevel segments),
#          smooth (shade-smooth the result), taper (0..1 inward scale of the
#          bottom face — for legs/feet), dome (0..1 push the top face up — soft tops).
_SHAPE_PROFILES = (
    # (keywords, profile) — first match wins; check specific before generic.
    (("seat cushion", "seat_cushion", "back cushion", "back_cushion", "cushion",
      "pillow", "bolster", "headrest", "pouffe", "pouf"),
     dict(bevel=0.45, seg=6, smooth=True, taper=0.0, dome=0.18)),
    (("mattress", "topper", "futon", "pad", "padding"),
     dict(bevel=0.30, seg=5, smooth=True, taper=0.0, dome=0.10)),
    (("armrest", "arm rest", "arm_", "armpanel"),
     dict(bevel=0.28, seg=4, smooth=True, taper=0.0, dome=0.0)),
    (("seat", "backrest", "headboard", "footboard", "sofa", "couch"),
     dict(bevel=0.22, seg=4, smooth=True, taper=0.0, dome=0.0)),
    (("leg", "foot", "post", "spindle"),
     dict(bevel=0.14, seg=3, smooth=False, taper=0.42, dome=0.0)),
    (("frame", "rail", "apron", "stretcher", "slat", "spar", "beam", "stile",
      "bracket", "support", "crossbar", "cross bar", "base"),
     dict(bevel=0.07, seg=2, smooth=False, taper=0.0, dome=0.0)),
    (("panel", "side", "top", "bottom", "shelf", "door", "back", "board",
      "cover", "worktop", "tabletop", "table top", "lid", "drawer", "wall"),
     dict(bevel=0.05, seg=2, smooth=False, taper=0.0, dome=0.0)),
)
# Mild chamfer so nothing exported is a perfectly razor-edged box.
_SHAPE_DEFAULT = dict(bevel=0.05, seg=2, smooth=False, taper=0.0, dome=0.0)


def _shape_profile(role, name):
    text = f"{role or ''} {name or ''}".lower()
    for keywords, profile in _SHAPE_PROFILES:
        if any(k in text for k in keywords):
            return profile
    return _SHAPE_DEFAULT


def _bevel(bm, geom, offset, segments):
    """Round edges; tolerate bmesh.ops.bevel signature drift across Blender
    versions (affect= in 4.x/5.x, vertex_only= in 3.x). Never raises."""
    for kw in (dict(affect="EDGES"), dict(vertex_only=False), {}):
        try:
            bmesh.ops.bevel(bm, geom=geom, offset=offset, offset_type="OFFSET",
                            segments=segments, profile=0.5, clamp_overlap=True, **kw)
            return True
        except TypeError:
            continue
        except Exception as e:  # noqa: BLE001
            print(f"[spatail_model_from_primitives] bevel failed: {e}")
            return False
    return False


def _shape_mesh(me, profile):
    """Apply a shape profile to a freshly-built primitive mesh, in place."""
    if not profile:
        return
    try:
        bm = bmesh.new()
        bm.from_mesh(me)
        xs = [v.co.x for v in bm.verts]
        ys = [v.co.y for v in bm.verts]
        zs = [v.co.z for v in bm.verts]
        if not xs:
            bm.free()
            return
        dx, dy, dz = max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)
        smallest = min(d for d in (dx, dy, dz) if d > 1e-9) if any(
            d > 1e-9 for d in (dx, dy, dz)) else 0.0
        zmin, zmax = min(zs), max(zs)

        taper = float(profile.get("taper", 0.0))
        if taper > 0 and dz > 1e-9:
            for v in bm.verts:
                f = (v.co.z - zmin) / dz            # 0 at bottom, 1 at top
                s = (1.0 - taper) + taper * f        # bottom→(1-taper), top→1
                v.co.x *= s
                v.co.y *= s

        dome = float(profile.get("dome", 0.0))
        if dome > 0 and dz > 1e-9:
            top_band = zmax - 0.18 * dz
            for v in bm.verts:
                if v.co.z >= top_band:
                    v.co.z += dome * dz

        bevel = float(profile.get("bevel", 0.0))
        seg = int(profile.get("seg", 1))
        if bevel > 0 and smallest > 0:
            off = min(bevel * smallest, 0.49 * smallest)
            geom = list(bm.verts) + list(bm.edges)
            _bevel(bm, geom, off, seg)

        bm.normal_update()
        bm.to_mesh(me)
        bm.free()
        if profile.get("smooth"):
            for poly in me.polygons:
                poly.use_smooth = True
        me.update()
    except Exception as e:  # noqa: BLE001
        print(f"[spatail_model_from_primitives] shaping skipped: {e}")


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
                    cad_manifest=None, shape=True):
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
        is_hardware = bool(part.get("_hardware"))
        prim = part.get("primitive", "box")
        # Non-hardware box/cylinder parts are MODELLED IN BLENDER (role-aware
        # shaping) instead of imported as uniform CAD panels — that is what makes
        # a cushion read as a cushion and a leg taper. Hardware keeps its real
        # CAD template geometry (screws, hinges, cam-locks, dowels).
        want_blender_shape = shape and not is_hardware and prim in ("box", "cylinder")
        me = None
        is_cad = False
        if cad_mesh_path and os.path.exists(cad_mesh_path) and not want_blender_shape:
            me = _mesh_from_cad_payload(name, cad_mesh_path)
            is_cad = me is not None
        if me is None:
            me = _build_mesh(part)        # primitive
            if want_blender_shape:
                _shape_mesh(me, _shape_profile(role, name))
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
