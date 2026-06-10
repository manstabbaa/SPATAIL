"""preview.py — render a fast QA preview.png of the normalized asset (bpy).

Goal is QA, not beauty: did it import, is it centred, is it scaled, is it visibly
broken? So we default to the Workbench engine, an orthographic 3/4 camera
auto-framed to the normalized bounds, neutral lighting and a neutral background,
768x768. Cycles is only used if explicitly configured. Preview failures are
non-fatal — the caller records a warning and keeps the asset a success.
"""
from __future__ import annotations

import math
from pathlib import Path

import bpy
from mathutils import Vector

_ENGINE_MAP = {
    "workbench": ("BLENDER_WORKBENCH",),
    "eevee": ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"),
    "cycles": ("CYCLES",),
}


def render_preview(out_path, final_bbox: dict, *, engine: str = "workbench",
                   resolution: int = 768, use_gpu: bool = True) -> str:
    """Render the current scene's meshes to out_path. Returns the path on success."""
    scene = bpy.context.scene
    _set_engine(scene, engine, use_gpu)
    _neutral_world(scene)

    size = final_bbox.get("size", [1, 1, 1])
    center = Vector(final_bbox.get("center", [0, 0, 0]))
    max_dim = max(size) or 1.0

    cam = _make_camera(center, max_dim)
    scene.camera = cam
    _add_key_light(center, max_dim)

    scene.render.engine = scene.render.engine          # no-op; keep set value
    scene.render.resolution_x = int(resolution)
    scene.render.resolution_y = int(resolution)
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = False
    scene.render.image_settings.file_format = "PNG"

    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    scene.render.filepath = str(p)
    bpy.ops.render.render(write_still=True)
    return str(p) if p.exists() else ""


def _set_engine(scene, pref: str, use_gpu: bool) -> None:
    try:
        avail = set(bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items.keys())
    except Exception:
        avail = {"BLENDER_WORKBENCH"}
    chosen = next((e for e in _ENGINE_MAP.get(pref, ("BLENDER_WORKBENCH",)) if e in avail),
                  "BLENDER_WORKBENCH" if "BLENDER_WORKBENCH" in avail else next(iter(avail)))
    scene.render.engine = chosen

    if chosen == "BLENDER_WORKBENCH":
        shading = scene.display.shading
        shading.light = "STUDIO"
        shading.color_type = "TEXTURE" if _any_textures() else "MATERIAL"
        shading.show_object_outline = False
        shading.show_shadows = True
    elif chosen == "CYCLES" and use_gpu:
        _try_enable_cycles_gpu(scene)


def _any_textures() -> bool:
    return any(img.has_data and img.type not in {"RENDER_RESULT", "COMPOSITING"}
               for img in bpy.data.images)


def _try_enable_cycles_gpu(scene) -> None:
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
        for backend in ("OPTIX", "CUDA", "HIP", "METAL", "ONEAPI"):
            try:
                prefs.compute_device_type = backend
                break
            except TypeError:
                continue
        for dev in prefs.devices:
            dev.use = True
        scene.cycles.device = "GPU"
        scene.cycles.samples = 16
    except Exception:
        pass


def _neutral_world(scene) -> None:
    world = scene.world or bpy.data.worlds.new("AF_World")
    scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs[0].default_value = (0.5, 0.5, 0.52, 1.0)
        bg.inputs[1].default_value = 1.0


def _make_camera(center: Vector, max_dim: float):
    cam_data = bpy.data.cameras.new("AF_PreviewCam")
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = max_dim * 1.6
    cam_data.clip_start = 0.001
    cam_data.clip_end = max_dim * 50 + 100
    cam = bpy.data.objects.new("AF_PreviewCam", cam_data)
    bpy.context.scene.collection.objects.link(cam)

    direction = Vector((1.0, -1.0, 0.7)).normalized()
    cam.location = center + direction * (max_dim * 3 + 1.0)
    cam.rotation_euler = (center - cam.location).to_track_quat("-Z", "Y").to_euler()
    return cam


def _add_key_light(center: Vector, max_dim: float):
    light_data = bpy.data.lights.new("AF_Key", type="SUN")
    light_data.energy = 3.0
    light = bpy.data.objects.new("AF_Key", light_data)
    light.location = center + Vector((1.0, -0.8, 1.4)).normalized() * (max_dim * 3 + 1.0)
    light.rotation_euler = (center - light.location).to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.collection.objects.link(light)
    return light
