"""
spatail_mesh_region.py — transport for sub-mesh selections.

glTF and USD cannot carry Blender vertex groups, so a sub-mesh selection made
by spatail_mesh_select has to be turned into something an exporter and the
runtime can actually see. Two complementary mechanisms:

  1. SIDECAR JSON (always)      regions.json — pure data: per-region index sets
     plus world-space centroid / bbox / radius. Small. Lets the runtime place a
     halo / pin at the region without needing the overlay geometry, and lets a
     contract reference the region by id.

  2. OVERLAY MESH BAKE (opt-in) extracts the selected faces into a *separate*
     thin, offset mesh named `spatail_region__<mesh>__<id>` with an emissive
     highlight material. Because it is its own mesh, the existing whole-mesh
     runtime can show / hide / highlight it with no new GPU path — a sub-region
     becomes a whole-mesh in transport. This is the "highlight this vein /
     this muscle / this seam" path.

USAGE in Blender:
    import sys; sys.path.insert(0, r"C:/SPATAIL_MAX/pipeline/blender")
    import importlib, spatail_mesh_select as ms, spatail_mesh_region as mr
    importlib.reload(ms); importlib.reload(mr)

    sel = ms.region_from_phrase("LH_Tower", "top rim")
    mr.bake_region_overlay("LH_Tower", "top_rim", sel, label="Lantern deck rim")
    mr.emit_region_sidecar(r"C:/SPATAIL_MAX/.../regions.json",
                           [dict(sel, id="top_rim", label="Lantern deck rim")])
"""
import bpy
import bmesh
import json
import os
from datetime import datetime, timezone
from mathutils import Vector

import spatail_mesh_select as _ms


OVERLAY_PREFIX = "spatail_region__"
SIDECAR_SCHEMA = "0.1.0-spatail-regions"


# ─────────────────────────────────────────────────────────────────────────
# Overlay mesh bake
# ─────────────────────────────────────────────────────────────────────────

def _highlight_material(color):
    """Get/create a shared emissive highlight material for a given RGBA."""
    key = "SPATAIL_RegionHL_%02x%02x%02x" % (
        int(color[0] * 255), int(color[1] * 255), int(color[2] * 255))
    mat = bpy.data.materials.get(key)
    if mat:
        return mat
    mat = bpy.data.materials.new(key)
    mat.use_nodes = True
    mat.blend_method = "BLEND"
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    emi = nt.nodes.new("ShaderNodeEmission")
    emi.inputs["Color"].default_value = (color[0], color[1], color[2], 1.0)
    emi.inputs["Strength"].default_value = 2.0
    transp = nt.nodes.new("ShaderNodeBsdfTransparent")
    mix = nt.nodes.new("ShaderNodeMixShader")
    mix.inputs["Fac"].default_value = color[3] if len(color) > 3 else 0.85
    nt.links.new(transp.outputs[0], mix.inputs[1])
    nt.links.new(emi.outputs[0], mix.inputs[2])
    nt.links.new(mix.outputs[0], out.inputs["Surface"])
    return mat


def overlay_name(mesh_name, region_id):
    return f"{OVERLAY_PREFIX}{mesh_name}__{region_id}"


def bake_region_overlay(mesh_name, region_id, selection,
                        color=(0.27, 0.56, 1.0, 0.85),
                        offset_frac=0.012, label=None, hide_by_default=True):
    """Extract the selected faces of a mesh into a separate offset overlay mesh.

    selection : a Selection dict from spatail_mesh_select (needs non-empty
                'faces'; verts/edges-only regions can't form a surface — use the
                sidecar halo path for those).
    offset_frac : push the overlay out along vertex normals by this fraction of
                  the source mesh's world diagonal, to avoid z-fighting.
    Returns a summary dict, or {"skipped": "no faces"} when not bakeable.
    """
    src = _ms._get_mesh(mesh_name)
    faces = selection.get("faces") or []
    if not faces:
        return {"skipped": "no faces", "meshId": mesh_name, "region": region_id}

    data = src.data
    wlo, whi = _ms._world_bbox(src)
    diag = (whi - wlo).length or 1.0
    scl = src.matrix_world.to_scale()
    avg_scl = (abs(scl.x) + abs(scl.y) + abs(scl.z)) / 3.0 or 1.0
    off_local = (offset_frac * diag) / avg_scl

    name = overlay_name(mesh_name, region_id)
    # Replace any prior bake of the same region.
    old = bpy.data.objects.get(name)
    if old:
        m = old.data
        bpy.data.objects.remove(old, do_unlink=True)
        if m and m.users == 0:
            bpy.data.meshes.remove(m)

    bm = bmesh.new()
    vmap = {}
    for fi in faces:
        poly = data.polygons[fi]
        nv = []
        for vi in poly.vertices:
            if vi not in vmap:
                v = data.vertices[vi]
                co = v.co + (v.normal * off_local)
                vmap[vi] = bm.verts.new(co)
            nv.append(vmap[vi])
        try:
            bm.faces.new(nv)
        except ValueError:
            pass  # duplicate face — skip
    new_mesh = bpy.data.meshes.new(name)
    bm.to_mesh(new_mesh)
    bm.free()

    obj = bpy.data.objects.new(name, new_mesh)
    obj.matrix_world = src.matrix_world.copy()
    new_mesh.materials.append(_highlight_material(color))

    obj["spatail_region"] = region_id
    obj["spatail_source"] = mesh_name
    if label:
        obj["spatail_label"] = label

    coll = src.users_collection[0] if src.users_collection else bpy.context.scene.collection
    coll.objects.link(obj)

    # hide_set needs the object to be in a view layer → after link.
    obj.hide_render = hide_by_default
    try:
        obj.hide_set(hide_by_default)
    except RuntimeError:
        obj.hide_viewport = hide_by_default

    return {
        "overlayMesh": name,
        "meshId": mesh_name,
        "region": region_id,
        "n_faces": len(faces),
        "n_verts": len(vmap),
        "offset_local": round(off_local, 6),
    }


def clear_region_overlays(mesh_name=None):
    """Remove overlay meshes (all, or just those baked from one source mesh)."""
    removed = []
    for o in list(bpy.data.objects):
        if not o.name.startswith(OVERLAY_PREFIX):
            continue
        if mesh_name and o.get("spatail_source") != mesh_name:
            continue
        m = o.data
        removed.append(o.name)
        bpy.data.objects.remove(o, do_unlink=True)
        if m and m.users == 0:
            bpy.data.meshes.remove(m)
    return {"removed": removed}


# ─────────────────────────────────────────────────────────────────────────
# Sidecar JSON
# ─────────────────────────────────────────────────────────────────────────

def _region_record(sel, include_indices=True):
    """Normalize a Selection (optionally tagged with id/label) into a sidecar
    record. A region dict may also carry 'overlayMesh' from a prior bake."""
    rid = sel.get("id") or sel.get("region") or "region"
    rec = {
        "id": rid,
        "label": sel.get("label") or rid.replace("_", " ").title(),
        "meshId": sel["meshId"],
        "counts": sel.get("counts", {}),
        "centroidWorld": sel.get("centroid_world"),
        "bboxWorld": sel.get("bbox_world"),
        "radiusWorld": sel.get("radius_world"),
    }
    if "overlayMesh" in sel:
        rec["overlayMesh"] = sel["overlayMesh"]
    else:
        ov = overlay_name(sel["meshId"], rid)
        if bpy.data.objects.get(ov):
            rec["overlayMesh"] = ov
    if include_indices:
        rec["indices"] = {
            "vertices": sel.get("vertices", []),
            "faces": sel.get("faces", []),
            "edges": sel.get("edges", []),
        }
    return rec


def emit_region_sidecar(path, regions, asset_id=None, include_indices=True):
    """Write regions.json. `regions` is a list of Selection dicts, each tagged
    with at least an 'id' (and ideally a 'label')."""
    payload = {
        "schemaVersion": SIDECAR_SCHEMA,
        "assetId": asset_id,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "regions": [_region_record(r, include_indices) for r in regions],
    }
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return {"path": path, "n_regions": len(payload["regions"])}


print("[spatail_mesh_region] module loaded.")
