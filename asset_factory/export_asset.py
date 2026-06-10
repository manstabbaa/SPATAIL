"""export_asset.py — export the normalized asset as a predictable GLB (bpy).

Export settings mirror the proven ones in studio/library/bake_assets.py (Blender
5.1 / iOS): GLB, Y-up, transforms applied, materials + embedded textures. Only the
cleaned mesh selection is exported — never the import-leftover cameras/lights.
Optional collision proxy (a bounding box) and LOD chain are produced on request.
"""
from __future__ import annotations

from pathlib import Path

import bpy


def _enable_gltf():
    try:
        bpy.ops.preferences.addon_enable(module="io_scene_gltf2")
    except Exception:
        pass


def _select_only(objs):
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
    if objs:
        bpy.context.view_layer.objects.active = objs[0]


def export_glb(objs, path, *, draco: bool = False) -> bool:
    """Export the given objects as a GLB (selection only, Y-up, transforms applied)."""
    _enable_gltf()
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    _select_only(objs)
    common = dict(filepath=str(p), export_format="GLB", export_yup=True,
                  export_apply=True, use_selection=True, export_cameras=False,
                  export_lights=False, export_materials="EXPORT")
    if draco:
        common.update(export_draco_mesh_compression_enable=True,
                      export_draco_mesh_compression_level=6)
    # Some kwargs vary across Blender builds — degrade gracefully on TypeError.
    for extra in (dict(use_visible=True), {}):
        try:
            bpy.ops.export_scene.gltf(**common, **extra)
            return p.exists()
        except TypeError:
            continue
    return False


def export_normalized(meshes, out_dir, asset_id: str) -> dict:
    """Export the canonical <asset_id>.normalized.glb. Raises on failure."""
    glb = Path(out_dir) / f"{asset_id}.normalized.glb"
    if not export_glb(meshes, glb):
        raise RuntimeError("normalized GLB export failed")
    return {"glb": glb.name, "glb_path": str(glb), "glb_bytes": glb.stat().st_size}


def export_usdz(objs, path) -> bool:
    """Export the cleaned selection as a RealityKit-ready USDZ (metres, Y-up).

    Mirrors studio/library/bake_assets._export_usdz: filter the kwargs through the
    operator's RNA so it stays compatible across Blender builds. USDZ is SPATAIL's
    iOS delivery format (AR Quick Look / RealityKit) — GLB is not viewable on iOS.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    _select_only(objs)
    try:
        rna = set(bpy.ops.wm.usd_export.get_rna_type().properties.keys())
    except Exception:
        rna = set()
    desired = {
        "filepath": str(p), "selected_objects_only": True, "export_uvmaps": True,
        "export_normals": True, "export_materials": True, "export_meshes": True,
        "generate_preview_surface": True, "convert_orientation": True,
        "export_global_forward_selection": "NEGATIVE_Z", "export_global_up_selection": "Y",
        "meters_per_unit": 1.0, "root_prim_path": "/Scene", "overwrite_textures": True,
    }
    kwargs = {k: v for k, v in desired.items() if k in rna}
    kwargs["filepath"] = str(p)
    try:
        bpy.ops.wm.usd_export(**kwargs)
        return p.exists()
    except Exception as e:  # noqa: BLE001 — USDZ is optional; never fail the asset on it
        print(f"[asset_factory] USDZ export failed for {p.name}: {e!r}")
        return False


def export_collision_proxy(final_bbox: dict, out_dir, asset_id: str) -> dict:
    """A simple bounding-box collision proxy GLB sized to the final bounds."""
    size = final_bbox.get("size", [0, 0, 0])
    center = final_bbox.get("center", [0, 0, 0])
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=tuple(center))
    box = bpy.context.active_object
    box.name = f"{asset_id}_collision"
    box.dimensions = (max(size[0], 1e-4), max(size[1], 1e-4), max(size[2], 1e-4))
    bpy.context.view_layer.update()
    _select_only([box])
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    glb = Path(out_dir) / f"{asset_id}.collision.glb"
    ok = export_glb([box], glb)
    bpy.data.objects.remove(box, do_unlink=True)
    return {"collision_glb": glb.name if ok else "",
            "proxy_type": "bounding_box",
            "proxy_bounds_m": {"size": size, "center": center}}


def export_lods(meshes, out_dir, asset_id: str) -> dict:
    """lod0 (full), lod1 (~50%), lod2 (~25%) GLBs via the Decimate modifier on
    throwaway duplicates — the real meshes are never modified."""
    out: dict = {}
    for level, ratio in (("lod0", 1.0), ("lod1", 0.5), ("lod2", 0.25)):
        dupes = _duplicate(meshes)
        if ratio < 1.0:
            for d in dupes:
                m = d.modifiers.new("af_lod", "DECIMATE")
                m.decimate_type = "COLLAPSE"
                m.ratio = ratio
                _select_only([d])
                try:
                    bpy.ops.object.modifier_apply(modifier=m.name)
                except Exception:
                    pass
        glb = Path(out_dir) / f"{asset_id}.{level}.glb"
        if export_glb(dupes, glb):
            out[level] = glb.name
        for d in dupes:
            if d.name in bpy.data.objects:
                bpy.data.objects.remove(d, do_unlink=True)
    return out


def _duplicate(meshes) -> list:
    _select_only(meshes)
    bpy.ops.object.duplicate()
    return list(bpy.context.selected_objects)
