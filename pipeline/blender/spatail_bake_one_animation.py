"""
spatail_bake_one_animation.py — single-clip bake driver.

Called by the orchestrator's bake bridge (subprocess Blender --background).
Reads a JSON bake spec from sys.argv[1], runs the requested motion bake on
the named parts, exports the GLB, writes a result JSON.

Bake spec:
  {
    "asset_id": "fan",
    "blend_path": ".../fan.blend",
    "glb_path":   ".../fan.glb",
    "name":       "axial_flutter",
    "parts":      ["mesh48", "mesh55", ...],
    "motion":     "reciprocate" | "orbit" | "spin",
    "axis":       [0, 1, 0],            # null → read from registry
    "magnitude_m": 0.005,
    "cycles_per_loop": 1,
    "frames": 120
  }

Result JSON (sidecar at <blend_path>.bake_result.json):
  {"ok": true, "action_name": "...", "glb_path": "..."}
or {"ok": false, "error": "..."}
"""
import bpy
import json
import math
import os
import sys
from pathlib import Path
from mathutils import Vector, Matrix


def _ensure_object_action(obj, name):
    prev = bpy.data.actions.get(name)
    if prev:
        bpy.data.actions.remove(prev, do_unlink=True)
    if obj.animation_data:
        obj.animation_data_clear()
    obj["__pending_action_name__"] = name


def _rename_object_action(obj):
    name = obj.get("__pending_action_name__")
    if name and obj.animation_data and obj.animation_data.action:
        obj.animation_data.action.name = name
        del obj["__pending_action_name__"]
    return obj.animation_data.action if obj.animation_data else None


def _action_fcurves(act):
    if not hasattr(act, "layers"):
        try: return list(act.fcurves)
        except AttributeError: return []
    fcurves = []
    for layer in act.layers:
        for strip in layer.strips:
            for cb in strip.channelbags:
                fcurves.extend(cb.fcurves)
    return fcurves


def _find_or_create_pivot_empty(name, location):
    """Get-or-create an Empty parent for the orbit/spin pattern."""
    emp = bpy.data.objects.get(name)
    if emp is None:
        emp = bpy.data.objects.new(name, None)
        emp.empty_display_type = "ARROWS"
        emp.empty_display_size = 0.02
        bpy.context.collection.objects.link(emp)
    emp.location = location
    emp.rotation_mode = "XYZ"
    emp.rotation_euler = (0, 0, 0)
    bpy.context.view_layer.update()
    return emp


def bake_reciprocate(parts_objs, name, *, axis_world, magnitude_m,
                    cycles_per_loop=1, frames=120):
    """Each named part oscillates along axis_world by ±magnitude_m/2.
    Useful for pistons, valves, levers."""
    half = magnitude_m * 0.5
    sample_step = 2
    for obj in parts_objs:
        rest = obj.matrix_world.translation.copy()
        action_name = f"{name}__{obj.name}"
        parent_inv = (obj.parent.matrix_world.inverted() if obj.parent else Matrix.Identity(4))
        _ensure_object_action(obj, action_name)
        act = None
        for f in range(1, frames + 2, sample_step):
            t = (f - 1) / frames
            offset = math.sin(2 * math.pi * cycles_per_loop * t) * half
            new_world = rest + Vector(axis_world) * offset
            local = parent_inv @ new_world
            obj.location = (local.x, local.y, local.z)
            obj.keyframe_insert(data_path="location", frame=f, index=-1)
            if act is None:
                act = _rename_object_action(obj)
        if act:
            for fc in _action_fcurves(act):
                for kp in fc.keyframe_points:
                    kp.interpolation = "LINEAR"
        rest_local = parent_inv @ rest
        obj.location = (rest_local.x, rest_local.y, rest_local.z)


def bake_orbit(parts_objs, name, *, axis_world, pivot_world=None,
              cycles_per_loop=1, frames=120):
    """Parts orbit a shared rotation axis as a rigid group. Implementation:
    create / reuse an Empty at pivot_world oriented with identity rotation,
    parent the parts to it (preserving world transforms), keyframe the
    empty's rotation around the appropriate Euler channel.

    Identity rest rotation on the empty is CRITICAL — Blender's 5.x
    quaternion export collapses non-identity rest poses combined with
    Euler keyframes."""
    if not parts_objs:
        return None
    if pivot_world is None:
        # Centroid of part world bounding boxes
        cx = cy = cz = 0
        for obj in parts_objs:
            bb = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
            for c in bb:
                cx += c.x; cy += c.y; cz += c.z
        n = len(parts_objs) * 8
        pivot_world = (cx / n, cy / n, cz / n)

    empty = _find_or_create_pivot_empty(f"__pivot__{name}__", Vector(pivot_world))
    # Parent each part preserving world
    for obj in parts_objs:
        mw = obj.matrix_world.copy()
        obj.parent = empty
        obj.matrix_world = mw
    bpy.context.view_layer.update()

    # Pick the world Euler channel that corresponds to axis_world
    A = Vector(axis_world).normalized()
    axis_idx = max(range(3), key=lambda i: abs(A[i]))
    sign = 1.0 if A[axis_idx] >= 0 else -1.0

    _ensure_object_action(empty, name)
    act = None
    sample_step = 4
    for f in range(1, frames + 2, sample_step):
        t = (f - 1) / frames
        theta = sign * 2 * math.pi * cycles_per_loop * t
        rot = [0.0, 0.0, 0.0]
        rot[axis_idx] = theta
        empty.rotation_euler = rot
        empty.keyframe_insert(data_path="rotation_euler", frame=f, index=axis_idx)
        if act is None:
            act = _rename_object_action(empty)
    if act:
        for fc in _action_fcurves(act):
            for kp in fc.keyframe_points:
                kp.interpolation = "LINEAR"
    empty.rotation_euler = (0, 0, 0)
    bpy.context.view_layer.update()
    return act


def export_glb(target_path):
    target_path = str(target_path)
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    # Active object required for the gltf exporter
    obj = next((o for o in bpy.context.scene.objects), None)
    if obj:
        for o in bpy.context.scene.objects: o.select_set(False)
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
    win = bpy.context.window_manager.windows[0] if bpy.context.window_manager.windows else None
    if win:
        screen = win.screen
        area = next((a for a in screen.areas if a.type == "VIEW_3D"), None)
        region = next((r for r in area.regions if r.type == "WINDOW"), None) if area else None
        if area and region:
            with bpy.context.temp_override(window=win, screen=screen, area=area, region=region,
                                           active_object=obj, selected_objects=[obj]):
                bpy.ops.export_scene.gltf(
                    filepath=target_path, export_format="GLB",
                    export_animations=True, export_animation_mode="ACTIONS",
                    export_apply=False, export_yup=True,
                    export_skins=False, export_morph=False,
                )
                return target_path
    bpy.ops.export_scene.gltf(
        filepath=target_path, export_format="GLB",
        export_animations=True, export_animation_mode="ACTIONS",
        export_apply=False, export_yup=True,
        export_skins=False, export_morph=False,
    )
    return target_path


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"ok": False, "error": "no spec path passed"}))
        return
    # Blender appends '--python' arg, real argv is after '--'
    if "--" in sys.argv:
        spec_path = sys.argv[sys.argv.index("--") + 1]
    else:
        spec_path = sys.argv[-1]
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))

    blend_path = spec["blend_path"]
    bpy.ops.wm.open_mainfile(filepath=blend_path)

    motion = spec.get("motion", "spin")
    parts_objs = [bpy.data.objects.get(n) for n in spec.get("parts", [])]
    parts_objs = [o for o in parts_objs if o is not None]
    if not parts_objs:
        Path(blend_path + ".bake_result.json").write_text(json.dumps({
            "ok": False, "error": f"no parts resolved from {spec.get('parts')}",
        }), encoding="utf-8")
        return

    axis_world = spec.get("axis") or [0, 1, 0]
    frames = int(spec.get("frames", 120))
    cycles = int(spec.get("cycles_per_loop", 1))
    name = spec["name"]

    try:
        if motion == "reciprocate":
            bake_reciprocate(parts_objs, name,
                axis_world=axis_world,
                magnitude_m=float(spec.get("magnitude_m", 0.04)),
                cycles_per_loop=cycles, frames=frames)
        elif motion in ("orbit", "spin"):
            bake_orbit(parts_objs, name,
                axis_world=axis_world,
                pivot_world=spec.get("pivot_world"),
                cycles_per_loop=cycles, frames=frames)
        else:
            raise ValueError(f"unknown motion={motion!r}")

        # Save updated blend + re-export GLB
        bpy.ops.wm.save_as_mainfile(filepath=blend_path, copy=False)
        glb_out = export_glb(spec["glb_path"])

        Path(blend_path + ".bake_result.json").write_text(json.dumps({
            "ok": True, "action_name": name, "glb_path": glb_out,
            "frames": frames, "motion": motion,
        }), encoding="utf-8")
    except Exception as e:
        import traceback
        Path(blend_path + ".bake_result.json").write_text(json.dumps({
            "ok": False, "error": str(e), "trace": traceback.format_exc(),
        }), encoding="utf-8")


main()
