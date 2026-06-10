"""cleanup.py — conservative, non-destructive cleanup of an imported asset (bpy).

The rule is *do not destroy the asset*: every step is safe-by-default and wrapped
so one failing op is recorded as a warning and the rest continue. No aggressive
remeshing. Returns the cleaned mesh objects plus a log of operations performed and
warnings raised, which the report surfaces for QA.
"""
from __future__ import annotations

import bpy

# Merge only vertices that are essentially coincident (0.1 mm) — never enough to
# visibly weld an AI mesh, just enough to drop exact duplicates from triangulation.
_MERGE_DISTANCE_M = 1e-4


def _object_mode():
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        try:
            bpy.ops.object.mode_set(mode="OBJECT")
        except Exception:
            pass


def _activate(obj):
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def cleanup(objs, asset_id: str) -> tuple[list, list, list]:
    """Run the conservative cleanup pass. Returns (mesh_objects, operations, warnings)."""
    ops: list = []
    warns: list = []

    def step(name, fn):
        try:
            detail = fn()
            ops.append({"op": name, "detail": detail if detail is not None else "ok"})
        except Exception as e:  # noqa: BLE001 — a failed cleanup step must not abort
            warns.append(f"cleanup.{name} failed: {e}")

    _object_mode()

    # 1) scene units → meters
    step("set_units_meters", _set_units_meters)

    # 2) drop import-leftover cameras / lights / empties (not part of the asset)
    step("remove_non_geometry", lambda: _remove_non_geometry(objs))

    meshes = [o for o in objs if o.type == "MESH" and o.name in bpy.data.objects]
    if not meshes:
        warns.append("cleanup: no mesh objects after non-geometry removal")
        return meshes, ops, warns

    # 3) ensure everything exportable is visible
    step("ensure_visible", lambda: _ensure_visible(meshes))

    # 4) apply rotation + scale so geometry sits in a clean basis (keep location)
    step("apply_transforms", lambda: _apply_transforms(meshes))

    # 5) per-mesh edit-mode hygiene: recalc normals, merge coincident verts, drop loose
    step("recalc_normals_outside", lambda: _edit_each(meshes, _recalc_normals))
    step("merge_close_vertices", lambda: _edit_each(meshes, _merge_by_distance))
    step("remove_loose_geometry", lambda: _edit_each(meshes, _delete_loose))

    # 6) materials: drop unused slots, merge obvious duplicates, stabilise names
    step("remove_unused_material_slots", lambda: _remove_unused_slots(meshes))
    step("dedupe_obvious_materials", lambda: _dedupe_materials(meshes))
    step("stabilize_material_names", lambda: _stabilize_material_names(meshes, asset_id))

    # 7) stabilise object names
    step("stabilize_object_names", lambda: _stabilize_object_names(meshes, asset_id))

    return meshes, ops, warns


# ── individual steps ─────────────────────────────────────────────────────────
def _set_units_meters():
    scn = bpy.context.scene
    scn.unit_settings.system = "METRIC"
    scn.unit_settings.scale_length = 1.0
    scn.unit_settings.length_unit = "METERS"
    return "METRIC / scale_length=1.0"


def _remove_non_geometry(objs):
    removed = []
    for o in list(objs):
        if o.name not in bpy.data.objects:
            continue
        if o.type in {"CAMERA", "LIGHT", "LIGHT_PROBE", "SPEAKER"}:
            removed.append(o.name)
            bpy.data.objects.remove(o, do_unlink=True)
        elif o.type == "EMPTY" and not o.children:
            removed.append(o.name)
            bpy.data.objects.remove(o, do_unlink=True)
    return f"removed {len(removed)}: {removed}" if removed else "none"


def _ensure_visible(meshes):
    for o in meshes:
        o.hide_viewport = False
        o.hide_render = False
        o.hide_set(False)
    return f"{len(meshes)} mesh objects visible"


def _apply_transforms(meshes):
    _object_mode()
    bpy.ops.object.select_all(action="DESELECT")
    for o in meshes:
        o.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    return f"applied rotation+scale on {len(meshes)} objects"


def _edit_each(meshes, fn) -> str:
    """Run a bmesh-style edit-mode op on each mesh; aggregate a short detail string."""
    details = []
    for o in meshes:
        _object_mode()
        _activate(o)
        try:
            bpy.ops.object.mode_set(mode="EDIT")
            bpy.ops.mesh.select_all(action="SELECT")
            details.append(fn(o))
        finally:
            _object_mode()
    return "; ".join(d for d in details if d) or "ok"


def _recalc_normals(o) -> str:
    bpy.ops.mesh.normals_make_consistent(inside=False)
    return ""


def _merge_by_distance(o) -> str:
    bpy.ops.mesh.remove_doubles(threshold=_MERGE_DISTANCE_M)
    return ""


def _delete_loose(o) -> str:
    # Only loose verts/edges; keep all faces (never deletes real surface geometry).
    bpy.ops.mesh.delete_loose(use_verts=True, use_edges=True, use_faces=False)
    return ""


def _remove_unused_slots(meshes):
    n = 0
    for o in meshes:
        _object_mode()
        _activate(o)
        try:
            bpy.ops.object.material_slot_remove_unused()
            n += 1
        except Exception:
            pass
    return f"cleaned slots on {n} objects"


def _dedupe_materials(meshes):
    """Merge materials whose names differ only by Blender's .00N suffix AND share a
    base colour — the common 'Material.001 == Material' import artefact. Conservative:
    we never merge across different base colours."""
    by_base: dict[str, list] = {}
    for mat in bpy.data.materials:
        base = mat.name.rsplit(".", 1)[0] if _has_numeric_suffix(mat.name) else mat.name
        by_base.setdefault(base, []).append(mat)

    merged = 0
    for base, mats in by_base.items():
        if len(mats) < 2:
            continue
        canonical = mats[0]
        for dup in mats[1:]:
            if _same_base_color(canonical, dup):
                _reassign_material(meshes, dup, canonical)
                merged += 1
    return f"merged {merged} duplicate materials" if merged else "none"


def _has_numeric_suffix(name: str) -> bool:
    tail = name.rsplit(".", 1)
    return len(tail) == 2 and tail[1].isdigit()


def _same_base_color(a, b) -> bool:
    ca, cb = _base_color(a), _base_color(b)
    if ca is None or cb is None:
        return False
    return all(abs(ca[i] - cb[i]) < 1e-3 for i in range(4))


def _base_color(mat):
    if not (mat.use_nodes and mat.node_tree):
        return tuple(mat.diffuse_color)
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf and "Base Color" in bsdf.inputs:
        return tuple(bsdf.inputs["Base Color"].default_value)
    return tuple(mat.diffuse_color)


def _reassign_material(meshes, old, new):
    for o in meshes:
        for slot in o.material_slots:
            if slot.material == old:
                slot.material = new


def _stabilize_material_names(meshes, asset_id):
    used = []
    for o in meshes:
        for slot in o.material_slots:
            if slot.material and slot.material not in used:
                used.append(slot.material)
    for i, mat in enumerate(used):
        mat.name = f"{asset_id}_mat{i}" if i else f"{asset_id}_mat"
    return f"named {len(used)} materials"


def _stabilize_object_names(meshes, asset_id):
    for i, o in enumerate(meshes):
        o.name = asset_id if (len(meshes) == 1) else f"{asset_id}_part{i}"
        if o.data:
            o.data.name = f"{o.name}_mesh"
    return f"named {len(meshes)} objects"
