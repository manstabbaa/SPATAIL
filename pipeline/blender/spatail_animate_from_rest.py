"""
spatail_animate_from_rest.py — bake per-part animations using the rest-pose
data sheets produced by spatail-blender-director.

Strategy:
  * Pistons: keyframe `location` along the bore axis (derived from each
    piston's principal_axis transformed by its rest_world rotation), so the
    piston reciprocates from rest +/- stroke/2 over one cycle.
  * Crank throws: parent all 4 throws to a single empty at the crank axis
    centroid, keyframe the empty's rotation. Throws orbit the axis as a
    rigid group — kinematically correct for a V8 crankshaft.
  * Fan: leave the existing baked fan_spin alone (external part, no rest
    dependency).

Every keyframed animation lives in its own Action, so the GLB exporter
emits one named clip per action (the three.js runtime uses the names).

Public entry point:
  bake_animations_from_rest(rest_dir, ...)  → re-bake every clip

Idempotent: drops any prior actions with the names we're about to create.

See skills/spatail-blender-director/SKILL.md for the rest-pose schema.
"""
import bpy
import json
import math
import os
from pathlib import Path
from mathutils import Vector, Matrix


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_rest(rest_dir):
    """Load every *.rest.json keyed by object_name (the Blender name)."""
    rest_dir = Path(rest_dir)
    out = {}
    for f in sorted(rest_dir.glob("*.rest.json")):
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
            out[rec["object_name"]] = rec
        except Exception as e:
            print(f"[anim] skip {f.name}: {e}")
    return out


def _index_by_role(rest_records):
    """Return {role_hint: [rest_record, ...]} for parts that have a role."""
    by_role = {}
    for rec in rest_records.values():
        role = rec.get("role_hint")
        if not role:
            continue
        by_role.setdefault(role, []).append(rec)
    return by_role


def _world_axis_from_rest(rec):
    """principal_axis is in local frame; multiply by the rest world rotation
    to get the bore direction in world space.

    NOTE: PCA picks the longest dimension of the mesh, which for piston
    crowns is the disc-diameter (across the bore) not the bore axis. Use
    `_bore_axis_piston_to_throw` for pistons if a throw record is available.
    """
    mat = _matrix_from_record(rec)
    axis_local = Vector(rec["geometry_local"]["principal_axis"])
    axis_world = mat.to_3x3() @ axis_local
    axis_world.normalize()
    return axis_world


def _bore_axis_piston_to_throw(piston_rec, throw_rec):
    """The bore is the line from the piston down to its crankshaft journal.
    For an angled V8 bank this gives a tilted up-axis — the *correct* bore
    direction — instead of the piston crown's flat-disc diameter that PCA
    would return.

    Returns a UNIT vector in WORLD space pointing FROM the throw TOWARD
    the piston (so positive offset = piston moves UP, away from crank).
    """
    p_loc = Vector(piston_rec["rest_transform"]["location_world"])
    t_loc = Vector(throw_rec["rest_transform"]["location_world"])
    axis = (p_loc - t_loc)
    if axis.length < 1e-6:
        # Degenerate — fall back to PCA
        return _world_axis_from_rest(piston_rec)
    return axis.normalized()


def _match_piston_to_throw(piston_role, throws_by_role):
    """piston_1A → crank_throw_1 by parsing the cylinder number."""
    if not piston_role or not piston_role.startswith("piston_"):
        return None
    # piston_1A → 1
    suffix = piston_role[len("piston_"):]
    cyl_num = "".join(ch for ch in suffix if ch.isdigit())
    if not cyl_num:
        return None
    return throws_by_role.get(f"crank_throw_{cyl_num}")


def _matrix_from_record(rec):
    flat = rec["rest_transform"]["matrix_world_rest_rowmajor"]
    return Matrix((flat[0:4], flat[4:8], flat[8:12], flat[12:16]))


def _ensure_object_action(obj, action_name):
    """Get a fresh action attached to `obj`, named `action_name`.

    Strategy: let Blender's keyframe_insert auto-create the action on
    first call, then rename. This sidesteps the Blender-5.x layered
    action API (slots / channelbags / strips) — going through
    keyframe_insert wires everything up correctly.

    Caller must immediately follow with at least one keyframe_insert.
    """
    # Drop any previous action with the target name so the rename succeeds
    prev = bpy.data.actions.get(action_name)
    if prev:
        bpy.data.actions.remove(prev, do_unlink=True)
    # Clear any prior action on this object so first insert creates anew
    if obj.animation_data:
        obj.animation_data_clear()
    # Use a sentinel attribute the bake_* functions inspect to rename
    obj["__pending_action_name__"] = action_name
    return None  # bake function must call _rename_object_action(obj) after first keyframe


def _rename_object_action(obj):
    """After the first keyframe_insert created an auto-named action, rename
    it to the pending name stashed by _ensure_object_action."""
    name = obj.get("__pending_action_name__")
    if name and obj.animation_data and obj.animation_data.action:
        obj.animation_data.action.name = name
        del obj["__pending_action_name__"]
    return obj.animation_data.action if obj.animation_data else None


def _action_fcurves(act):
    """Return the list of FCurves attached to an Action.

    Blender 5.x moved fcurves under the layered system:
    Action → Layer → Strip → Channelbag → fcurves. The legacy
    Action.fcurves attribute is gone. Walk every layer/strip and
    collect every channelbag's fcurves.
    """
    # In Blender 5.x `Action.fcurves` is GONE (raises AttributeError when
    # accessed). hasattr returns False. Detect via `layers` instead — if
    # the action has layers, it's the new system.
    if not hasattr(act, "layers"):
        # Pre-5.x fallback
        try:
            return list(act.fcurves)
        except AttributeError:
            return []
    fcurves = []
    for layer in getattr(act, "layers", []):
        for strip in getattr(layer, "strips", []):
            for cb in getattr(strip, "channelbags", []):
                fcurves.extend(cb.fcurves)
    return fcurves


def _key_location(obj, frame, value_xyz):
    obj.location = value_xyz
    obj.keyframe_insert(data_path="location", frame=frame, index=-1)


def _key_rotation_euler(obj, frame, value_xyz):
    # Use Euler so the GLB exporter writes simple rotation tracks
    obj.rotation_mode = "XYZ"
    obj.rotation_euler = value_xyz
    obj.keyframe_insert(data_path="rotation_euler", frame=frame, index=-1)


# ---------------------------------------------------------------------------
# Piston: reciprocating along bore axis
# ---------------------------------------------------------------------------

def bake_piston_stroke(rec, *, frames=120, stroke_m=0.04, bore_axis=None):
    """Bake a one-cycle reciprocating animation along the piston's bore axis.

    The piston is keyframed in world units, but Blender records location in
    parent-relative coordinates. We bake by setting matrix_world directly
    each frame, then re-reading location to keyframe — that handles any
    parent chain correctly.

    stroke_m: total peak-to-peak distance (meters). Real V8 stroke ≈ 8-10cm
    but the piston meshes are crowns only, so 4cm is enough to read on screen.
    """
    obj = bpy.data.objects.get(rec["object_name"])
    if obj is None:
        print(f"[anim] piston object missing: {rec['object_name']}")
        return None
    action_name = f"{rec['role_hint']}_stroke"
    _ensure_object_action(obj, action_name)

    rest_loc = Vector(rec["rest_transform"]["location_world"])
    # Prefer the caller-supplied bore axis (e.g. piston→throw direction).
    # Fall back to PCA if not supplied — note PCA picks the crown's diameter,
    # not the bore, so the motion will be sideways instead of up-and-down.
    axis = bore_axis if bore_axis is not None else _world_axis_from_rest(rec)
    half = stroke_m * 0.5

    # The piston object may have a parent transform — its `location`
    # property is stored in PARENT-local space. Setting it to a world
    # value breaks. Set `matrix_world.translation` instead and let
    # Blender derive the proper local components.
    parent_inv = (obj.parent.matrix_world.inverted() if obj.parent else Matrix.Identity(4))

    # Bake N+1 samples so frame 1 and frame frames+1 line up (loopable)
    cycles_per_loop = 2
    sample_step = 2
    act = None
    for f in range(1, frames + 2, sample_step):
        t = (f - 1) / frames
        offset = math.sin(2 * math.pi * cycles_per_loop * t) * half
        new_world = rest_loc + axis * offset
        # Convert world → parent-local for obj.location
        local = parent_inv @ new_world
        obj.location = (local.x, local.y, local.z)
        obj.keyframe_insert(data_path="location", frame=f, index=-1)
        if act is None:
            act = _rename_object_action(obj)

    # Linear interpolation between samples (Blender default is bezier)
    if act:
        for fc in _action_fcurves(act):
            for kp in fc.keyframe_points:
                kp.interpolation = "LINEAR"

    # Reset to rest after baking so the scene at frame 0 looks identical
    rest_local = parent_inv @ rest_loc
    obj.location = (rest_local.x, rest_local.y, rest_local.z)
    return action_name


# ---------------------------------------------------------------------------
# Crank rotation: throws orbit a common axis as a rigid group
# ---------------------------------------------------------------------------

def bake_crank_rotation(throws, *, frames=120, revs=2):
    """Animate all crank throws orbiting their common axis.

    Strategy: create a parent empty `crank_axis` at the throws' centroid,
    parent the throws to it (preserving each throw's WORLD transform), then
    keyframe the empty's rotation around the crank axis. All throws then
    rotate together via parent inheritance — one Action on one object, one
    glTF animation, four tracks consolidated into the parent's rotation curve.

    Axis is derived from throw positions: the axis direction is whichever
    world axis has the largest spread across throw centers (X in a V8).
    """
    if not throws:
        return None
    # All throws share the same Y, Z (centred on the crank axis); they
    # differ only in X. Confirm + pick axis direction.
    centers = [Vector(r["rest_transform"]["location_world"]) for r in throws]
    cx = sum(c.x for c in centers) / len(centers)
    cy = sum(c.y for c in centers) / len(centers)
    cz = sum(c.z for c in centers) / len(centers)
    centroid = Vector((cx, cy, cz))
    # Direction with largest spread among centers
    spread = [max(abs(c[i] - centroid[i]) for c in centers) for i in range(3)]
    axis_idx = spread.index(max(spread))
    axis_dir = Vector((0, 0, 0))
    axis_dir[axis_idx] = 1.0

    action_name = "crank_rotation"

    # Drop any existing crank_axis empty + crank_rotation actions
    prev_empty = bpy.data.objects.get("crank_axis")
    if prev_empty:
        # Unparent any throws still attached to the old empty (keep their world transform)
        for child in list(prev_empty.children):
            mw = child.matrix_world.copy()
            child.parent = None
            child.matrix_world = mw
        bpy.data.objects.remove(prev_empty, do_unlink=True)
    while True:
        prev = bpy.data.actions.get(action_name)
        if not prev:
            break
        bpy.data.actions.remove(prev, do_unlink=True)

    # Create the parent empty AT THE CENTROID with IDENTITY rotation —
    # critical: any non-identity rest rotation interferes with the
    # rotation_euler keyframe → quaternion export pipeline and the
    # rotation collapses to zero in the GLB.
    empty = bpy.data.objects.new("crank_axis", None)
    empty.empty_display_type = "ARROWS"
    empty.empty_display_size = 0.05
    bpy.context.collection.objects.link(empty)
    empty.location = centroid
    empty.rotation_mode = "XYZ"     # set BEFORE any rotation is touched
    empty.rotation_euler = (0, 0, 0)
    empty.scale = (1, 1, 1)
    bpy.context.view_layer.update()

    # Parent each throw to the empty while keeping its world transform intact
    for throw in throws:
        obj = bpy.data.objects.get(throw["object_name"])
        if obj is None:
            continue
        mw = obj.matrix_world.copy()
        obj.parent = empty
        obj.matrix_world = mw
    bpy.context.view_layer.update()

    # The crank axis direction in WORLD space is `axis_dir` (the world axis
    # with largest spread among throw centers — for this V8 it's X). Since
    # the empty has identity rotation, its local axes coincide with world
    # axes, so we keyframe whichever Euler channel matches axis_dir.
    axis_idx_to_channel = {0: 0, 1: 1, 2: 2}  # local 0/1/2 = world X/Y/Z
    rot_channel = axis_idx_to_channel[axis_idx]

    _ensure_object_action(empty, action_name)
    act = None
    sample_step = 4
    for f in range(1, frames + 2, sample_step):
        t = (f - 1) / frames
        theta = 2 * math.pi * revs * t
        rot = [0.0, 0.0, 0.0]
        rot[rot_channel] = theta
        empty.rotation_euler = rot
        empty.keyframe_insert(data_path="rotation_euler", frame=f, index=rot_channel)
        if act is None:
            act = _rename_object_action(empty)
    if act:
        for fc in _action_fcurves(act):
            for kp in fc.keyframe_points:
                kp.interpolation = "LINEAR"
    # Reset to rest
    empty.rotation_euler = (0, 0, 0)
    bpy.context.view_layer.update()

    return action_name


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def bake_animations_from_rest(rest_dir,
                              piston_stroke_m=0.04,
                              cycle_frames=120,
                              crank_revs=2):
    """Bake the full animation library against the rest data.

    Args:
      rest_dir: directory of *.rest.json files from spatail-blender-director.
      piston_stroke_m: peak-to-peak piston travel in meters.
      cycle_frames: frames per loop (default 120 = 5s at 24fps).
      crank_revs: crank revolutions per loop (default 2 for a 4-stroke cycle).

    Returns: {actions_baked, pistons, throws}.
    """
    rest = _load_rest(rest_dir)
    by_role = _index_by_role(rest)

    # Configure scene range so the exporter picks up the full loop
    scene = bpy.context.scene
    scene.frame_start = 1
    scene.frame_end = cycle_frames + 1

    # Build a {role → throw_record} map so each piston can resolve its bore axis
    throws_by_role = {role: recs[0] for role, recs in by_role.items()
                      if role.startswith("crank_throw_")}

    pistons_baked = []
    for role, recs in sorted(by_role.items()):
        if not role.startswith("piston_"):
            continue
        rec = recs[0]
        throw_rec = _match_piston_to_throw(role, throws_by_role)
        bore = _bore_axis_piston_to_throw(rec, throw_rec) if throw_rec else None
        name = bake_piston_stroke(rec, frames=cycle_frames,
                                  stroke_m=piston_stroke_m, bore_axis=bore)
        if name:
            pistons_baked.append(name)
    print(f"[anim] baked {len(pistons_baked)} piston strokes (bore axis from piston→throw)")

    # All throws together
    throws = []
    for role, recs in by_role.items():
        if role.startswith("crank_throw_"):
            throws.append(recs[0])
    throws.sort(key=lambda r: r.get("role_hint", ""))
    crank_name = bake_crank_rotation(throws, frames=cycle_frames, revs=crank_revs)
    print(f"[anim] baked crank_rotation across {len(throws)} throws")

    bpy.context.view_layer.update()
    return {
        "actions_baked": pistons_baked + ([crank_name] if crank_name else []),
        "pistons": pistons_baked,
        "throws_in_crank": len(throws),
    }


def export_glb(target_path):
    """Export the current scene to GLB. Forces ACTIONS mode so each Blender
    Action becomes one named glTF animation clip."""
    target_path = str(target_path)
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    bpy.ops.export_scene.gltf(
        filepath=target_path,
        export_format="GLB",
        export_animations=True,
        export_animation_mode="ACTIONS",
        export_apply=False,            # don't bake transforms; keeps rest matrices
        export_yup=True,               # match three.js convention
        export_skins=False,
        export_morph=False,
        use_visible=False,
        use_renderable=False,
        use_active_collection=False,
    )
    return target_path


if __name__ == "__main__":
    rest_dir = r"C:/SPATAIL_MAX/assets_processed/rest_poses/v8_engine"
    bake_animations_from_rest(rest_dir)
    export_glb(r"C:/SPATAIL_MAX/engineexplainer/engine/v8_engine.glb")
