"""
bootstrap_wheel_blend.py — programmatic stand-in for the human authoring pass.

v2 — uses the libraries pipeline:

  1. Import the wheel GLB (or OBJ fallback).
  2. Split the single mesh into ~6 named bounding-box regions
     (left_grip, right_grip, rim_bar, center_console, top_left_bay,
     top_right_bay, mercedes_star).
  3. Append PBR materials from assets_authoring/materials/spatail_pbr_library.blend
     and assign each region a material picked by spatail_authoring_classifier.
  4. Append the rig.exploded_assembly template, parent each region under
     a rig_slot.* empty, rewrite the slot drivers so each part moves
     outward from its own centroid when the master `explode_amount`
     property goes 0 → 1.
  5. Author a tiny NLA story on top: intro fade-in, slow look-at rotation,
     explode (driving the master prop), and assemble. No camera moves —
     SPATAIL never moves the user's viewport.
  6. Save assets_authoring/wheel.blend.

Run:
    blender --background --factory-startup \
        --python pipeline/blender/bootstrap_wheel_blend.py \
        -- <output.blend> <source_asset_path>
"""

import math
import os
import sys

# Make sibling modules importable when Blender runs us with --python.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from spatail_authoring_classifier import classify  # noqa: E402

try:
    import bmesh   # type: ignore
    import bpy     # type: ignore
    from mathutils import Vector  # type: ignore
except ImportError:
    bmesh = None
    bpy = None
    Vector = None


PROJECT_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."),
)
MAT_LIB = os.path.join(PROJECT_ROOT, "assets_authoring", "materials", "spatail_pbr_library.blend")
RIG_LIB = os.path.join(PROJECT_ROOT, "assets_authoring", "rigs", "rig.exploded_assembly.blend")

SEQUENCE_ID = "steering_walkthrough"

# Bounding-box partition. Each region picks faces whose centroid is in
# its box (X / Y / Z thresholds, in metres relative to the wheel's
# already-normalised local frame). The split is deliberately coarse —
# 5–7 chunks is plenty for the demo, and the classifier maps each
# region's name to a PBR class.
def region_for_face(c):
    x, y, z = c.x, c.y, c.z
    # The Mercedes star sits near the absolute center, roughly Y in [-0.005, 0.02].
    if abs(x) < 0.025 and abs(z) < 0.025 and abs(y) < 0.02:
        return "mercedes_star"
    # Bottom rim bar — the cross-piece at the wheel's lower edge.
    if y < -0.012:
        return "rim_bar"
    # Top control bays — split left / right.
    if y > 0.012:
        return "top_left_bay" if x < 0 else "top_right_bay"
    # Mid-height: grips on the outer X, center console near origin.
    if x < -0.045: return "left_grip"
    if x >  0.045: return "right_grip"
    return "center_console"


REGION_ORDER = [
    "left_grip", "right_grip", "rim_bar",
    "center_console", "top_left_bay", "top_right_bay", "mercedes_star",
]


# ---------------------------------------------------------------------------
# Scene / import helpers
# ---------------------------------------------------------------------------

def clear_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for coll in (bpy.data.meshes, bpy.data.materials, bpy.data.cameras,
                 bpy.data.lights, bpy.data.images, bpy.data.actions,
                 bpy.data.collections, bpy.data.armatures):
        for b in list(coll):
            try: coll.remove(b, do_unlink=True)
            except Exception: pass


def import_asset(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=path)
    elif ext == ".obj":
        try: bpy.ops.wm.obj_import(filepath=path)
        except Exception: bpy.ops.import_scene.obj(filepath=path)
    else:
        raise SystemExit("unsupported asset extension: %s" % ext)
    meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    if not meshes:
        raise SystemExit("import produced no meshes")
    meshes.sort(key=lambda m: len(m.data.vertices), reverse=True)
    root = meshes[0]
    # Normalise to ~0.4 m wide so the demo room is reasonable.
    bpy.context.view_layer.objects.active = root
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    bbox = [Vector(c) for c in root.bound_box]
    centroid = sum(bbox, Vector()) / 8.0
    root.location -= centroid
    size = max((root.dimensions.x, root.dimensions.y, root.dimensions.z, 1e-6))
    if size > 0.001:
        target = 0.4 / size
        root.scale = (target, target, target)
        bpy.ops.object.transform_apply(scale=True)
    root.name = "wheel_full"
    return root


# ---------------------------------------------------------------------------
# Region split
# ---------------------------------------------------------------------------

def split_into_regions(root):
    """Returns dict region_name → new mesh Object. Removes the original mesh."""
    bm = bmesh.new()
    bm.from_mesh(root.data)
    bm.faces.ensure_lookup_table()

    buckets = {name: [] for name in REGION_ORDER}
    for face in bm.faces:
        c = face.calc_center_median()
        buckets[region_for_face(c)].append(face)

    new_objects = {}
    for name in REGION_ORDER:
        faces = buckets[name]
        if not faces:
            continue
        new_bm = bmesh.new()
        vert_map = {}
        for f in faces:
            new_verts = []
            for v in f.verts:
                if v not in vert_map:
                    vert_map[v] = new_bm.verts.new(v.co)
                new_verts.append(vert_map[v])
            try:
                new_bm.faces.new(new_verts)
            except ValueError:
                pass  # duplicate face from shared edges — skip
        new_bm.normal_update()
        new_mesh = bpy.data.meshes.new(name + "_mesh")
        new_bm.to_mesh(new_mesh)
        new_bm.free()
        obj = bpy.data.objects.new(name, new_mesh)
        bpy.context.scene.collection.objects.link(obj)
        # Re-origin to the region's geometric center so rotation/scale
        # animate around the right point and the rig slot can sit at
        # the region's centroid.
        bbox = [Vector(c) for c in obj.bound_box]
        centroid = sum(bbox, Vector()) / 8.0
        new_mesh.transform(_translation_matrix(-centroid))
        obj.location = centroid
        new_objects[name] = obj

    bm.free()
    bpy.data.objects.remove(root, do_unlink=True)
    print("[bootstrap] split into regions: %s" % ", ".join(new_objects.keys()))
    return new_objects


def _translation_matrix(v):
    from mathutils import Matrix
    return Matrix.Translation(v)


# ---------------------------------------------------------------------------
# Material library append + per-region assignment
# ---------------------------------------------------------------------------

def append_material_library():
    """Append all materials from the PBR library into the current file.
    Returns dict class_name → bpy.types.Material."""
    out = {}
    if not os.path.exists(MAT_LIB):
        print("[bootstrap] WARNING: material library missing at %s" % MAT_LIB)
        return out
    with bpy.data.libraries.load(MAT_LIB, link=False) as (src, dst):
        dst.materials = list(src.materials)
    for mat in bpy.data.materials:
        cls = mat.get("spatail_class")
        if cls:
            out[cls] = mat
    print("[bootstrap] appended %d materials from library" % len(out))
    return out


def assign_materials(regions, material_map):
    """Pick a material class per region via the classifier, attach it as
    the region's material slot 0. Also stores the chosen class on the
    object as a custom property for downstream inspection."""
    for name, obj in regions.items():
        c = classify(name, group="steering_wheel")
        mat = material_map.get(c["material"]) or material_map.get("placeholder_neutral")
        if mat is None:
            continue
        if obj.data.materials:
            obj.data.materials[0] = mat
        else:
            obj.data.materials.append(mat)
        obj["spatail_material_class"] = c["material"]
        obj["spatail_classifier_haystack"] = c["_haystack"]
        print("[bootstrap]   %-18s → %s" % (name, c["material"]))


# ---------------------------------------------------------------------------
# Rig graft — append rig.exploded_assembly, parent regions, rewrite drivers
# ---------------------------------------------------------------------------

def append_exploded_assembly_rig():
    if not os.path.exists(RIG_LIB):
        print("[bootstrap] WARNING: rig library missing at %s" % RIG_LIB)
        return None, []
    before = set(bpy.data.objects.keys())
    with bpy.data.libraries.load(RIG_LIB, link=False) as (src, dst):
        dst.objects = list(src.objects)
    appended = [bpy.data.objects[n] for n in bpy.data.objects.keys() if n not in before]
    for obj in appended:
        if obj.users == 0:
            bpy.context.scene.collection.objects.link(obj)
    template_root = next((o for o in appended if o.name.startswith("template_root")), None)
    slots = [o for o in appended if o.name.startswith("rig_slot.")]
    slots.sort(key=lambda o: o.name)
    if template_root is not None:
        template_root.name = "wheel_assembly"
        template_root["spatail_rig_kind"] = "parented_group"
        template_root["spatail_classifier_haystack"] = "steering_wheel_assembly"
    print("[bootstrap] appended rig.exploded_assembly: 1 template_root + %d slots" % len(slots))
    return template_root, slots


def graft_regions_onto_rig(regions, template_root, slots):
    """Parent each region's mesh to a slot positioned at the region's
    centroid. Records the exploded target position on the slot via a
    custom property so the NLA-authoring pass below can read it back
    when keyframing the explode beat.

    We deliberately AVOID setting Blender drivers on the slot here —
    drivers on custom properties don't survive glTF export, and the
    viewer plays the GLB clips directly through THREE.AnimationMixer.
    Direct location keyframes on the slots survive the round-trip.
    """
    if template_root is None or not slots:
        return []
    paired = list(zip(REGION_ORDER, slots))
    SPREAD = 1.4
    grafted = []
    for region_name, slot in paired:
        obj = regions.get(region_name)
        if obj is None:
            continue
        centroid = obj.location.copy()
        slot.location = centroid

        # Compute the exploded destination: straight up by SPREAD*0.35
        # + a small radial nudge in the wheel's plane so adjacent parts
        # don't end up stacked on top of each other.
        dir_vec = Vector((centroid.x, max(centroid.y, 0.001), centroid.z))
        if dir_vec.length > 1e-5:
            dir_vec.normalize()
        else:
            dir_vec = Vector((0, 1, 0))
        exploded = Vector((
            centroid.x + SPREAD * 0.5  * dir_vec.x,
            centroid.y + SPREAD * 0.35 + SPREAD * 0.4 * dir_vec.y,
            centroid.z + SPREAD * 0.5  * dir_vec.z,
        ))

        # Strip any drivers the rig library installed — we keyframe directly.
        if slot.animation_data:
            for axis in (0, 1, 2):
                try: slot.driver_remove("location", axis)
                except Exception: pass

        # Store rest + exploded for the NLA-authoring pass below.
        slot["rest_location"] = list(centroid)
        slot["exploded_location"] = list(exploded)

        # Parent the region's mesh to the slot.
        obj.parent = slot
        obj.matrix_parent_inverse = slot.matrix_world.inverted()
        obj.location = (0, 0, 0)
        grafted.append(slot)
    return grafted


# ---------------------------------------------------------------------------
# Authoring: SPATAIL_meta + NLA tracks
# ---------------------------------------------------------------------------

def create_meta_empty(asset_id, template_root):
    empty = bpy.data.objects.new("SPATAIL_meta", None)
    bpy.context.scene.collection.objects.link(empty)
    empty.empty_display_size = 0.05
    empty["assetId"] = asset_id
    empty["assetGroupRef"] = "car-engine"
    empty["targetElementId"] = "elem_steering_wheel_4"
    empty["defaultSequenceId"] = "seq.%s" % SEQUENCE_ID
    empty["loopDefault"] = True
    # No camera collection in v2 — the principle is "object animates, not camera".
    return empty


def keyframe_action(obj, name, frame_pairs):
    if obj.animation_data is None:
        obj.animation_data_create()
    action = bpy.data.actions.new(name=name)
    obj.animation_data.action = action
    for frame, attrs in frame_pairs:
        bpy.context.scene.frame_set(int(frame))
        for path, val in attrs.items():
            setattr(obj, path, val)
            obj.keyframe_insert(data_path=path, frame=int(frame))
    obj.animation_data.action = None
    return action


def keyframe_prop_action(obj, prop_name, name, frame_pairs):
    """Keyframe a single custom property (e.g. 'explode_amount')."""
    if obj.animation_data is None:
        obj.animation_data_create()
    action = bpy.data.actions.new(name=name)
    obj.animation_data.action = action
    for frame, val in frame_pairs:
        bpy.context.scene.frame_set(int(frame))
        obj[prop_name] = float(val)
        obj.keyframe_insert(data_path='["%s"]' % prop_name, frame=int(frame))
    obj.animation_data.action = None
    return action


def push_to_nla(obj, action, track_name, start_frame):
    if obj.animation_data is None:
        obj.animation_data_create()
    track = obj.animation_data.nla_tracks.new()
    track.name = track_name
    track.strips.new(track_name, int(start_frame), action)


def keyframe_slot_locations(slots, name, frame_pairs):
    """Keyframe each slot's location across frame_pairs = [(frame, key)]
    where key is either 'rest' or 'exploded'. Pushes the resulting action
    to an NLA strip named `name` on EACH slot — the glTF exporter then
    bakes the per-slot tracks into clips named after the strip."""
    for slot in slots:
        rest = Vector(slot.get("rest_location", [0, 0, 0]))
        ex   = Vector(slot.get("exploded_location", [0, 0, 0]))
        if slot.animation_data is None:
            slot.animation_data_create()
        action = bpy.data.actions.new(name="act_%s_%s" % (name, slot.name))
        slot.animation_data.action = action
        for frame, kind in frame_pairs:
            target = rest if kind == "rest" else ex
            bpy.context.scene.frame_set(int(frame))
            slot.location = target
            slot.keyframe_insert(data_path="location", frame=int(frame))
        slot.animation_data.action = None
        push_to_nla(slot, action, name, int(frame_pairs[0][0]))


def author_sequence(template_root, slots, wheel_objects):
    """Lay down a Lego-manual story: intro, look, explode, assemble.
    All beats live as NLA strips on template_root (transforms) AND on
    the slots (per-slot location keyframes for explode/assemble) so the
    glTF exporter emits one clip per beat with all the right tracks.
    No camera moves — the user owns the camera."""
    cursor = 1

    # Beat 0 — intro fade-in: scale template_root 0 → 1
    length = 30
    a = keyframe_action(template_root, "act_intro", [
        (cursor, {"scale": (0.0, 0.0, 0.0)}),
        (cursor + length, {"scale": (1.0, 1.0, 1.0)}),
    ])
    push_to_nla(template_root, a, "seq.%s.00.intro_fade_in" % SEQUENCE_ID, cursor)
    bpy.context.scene.timeline_markers.new("cue.%s.intro_done" % SEQUENCE_ID,
                                            frame=cursor + length)
    cursor += length + 5

    # Beat 1 — look_at_whole_wheel: gentle Y rotation on template_root
    length = 45
    a = keyframe_action(template_root, "act_look", [
        (cursor, {"rotation_euler": (0, 0, 0)}),
        (cursor + length, {"rotation_euler": (0, math.radians(20), 0)}),
    ])
    push_to_nla(template_root, a, "seq.%s.01.look_at_whole_wheel" % SEQUENCE_ID, cursor)
    cursor += length + 5

    # Beat 2 — explode: each slot rest → exploded over `length` frames.
    length = 60
    strip_name = "seq.%s.02.explode_radial" % SEQUENCE_ID
    keyframe_slot_locations(slots, strip_name, [
        (cursor, "rest"),
        (cursor + length, "exploded"),
    ])
    # Update template_root's own explode_amount custom prop too — useful
    # for a human author who later swaps the keyframes for a driver-based
    # rig, even though glTF doesn't carry the prop itself.
    a = keyframe_prop_action(template_root, "explode_amount", "act_explode_master", [
        (cursor, 0.0),
        (cursor + length, 1.0),
    ])
    push_to_nla(template_root, a, strip_name + "__master", cursor)
    bpy.context.scene.timeline_markers.new("cue.%s.exploded_peak" % SEQUENCE_ID,
                                            frame=cursor + length)
    cursor += length + 5

    # Beat 3 — assemble: each slot exploded → rest.
    length = 60
    strip_name = "seq.%s.03.assemble_finale" % SEQUENCE_ID
    keyframe_slot_locations(slots, strip_name, [
        (cursor, "exploded"),
        (cursor + length, "rest"),
    ])
    a = keyframe_prop_action(template_root, "explode_amount", "act_assemble_master", [
        (cursor, 1.0),
        (cursor + length, 0.0),
    ])
    push_to_nla(template_root, a, strip_name + "__master", cursor)
    cursor += length + 5

    return cursor


def author_default_loop(template_root):
    length = 360
    a = keyframe_action(template_root, "act_idle_rotation", [
        (1, {"rotation_euler": (0, 0, 0)}),
        (length, {"rotation_euler": (0, math.radians(360), 0)}),
    ])
    push_to_nla(template_root, a, "default.idle_rotation", 1)


def author_interaction(template_root):
    """A small wiggle as the example tap-rim interaction."""
    name = "interaction.tap.rim"
    a = keyframe_action(template_root, "act_tap_rim", [
        (1,  {"scale": (1.00, 1.00, 1.00)}),
        (8,  {"scale": (1.07, 1.07, 1.07)}),
        (16, {"scale": (1.00, 1.00, 1.00)}),
    ])
    push_to_nla(template_root, a, name, 1)


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

def main():
    if bpy is None:
        print("[bootstrap_wheel_blend] run inside Blender", file=sys.stderr)
        sys.exit(2)
    try:
        argv = sys.argv[sys.argv.index("--") + 1:]
    except ValueError:
        argv = []
    if len(argv) < 2:
        print("usage: blender --background --factory-startup --python "
              "bootstrap_wheel_blend.py -- <output.blend> <asset_path>",
              file=sys.stderr)
        sys.exit(2)
    out_blend, asset_path = argv[0], argv[1]
    os.makedirs(os.path.dirname(out_blend) or ".", exist_ok=True)

    clear_scene()
    raw = import_asset(asset_path)
    regions = split_into_regions(raw)
    materials = append_material_library()
    assign_materials(regions, materials)
    template_root, slots = append_exploded_assembly_rig()
    grafted_slots = graft_regions_onto_rig(regions, template_root, slots)
    create_meta_empty("wheel", template_root)
    end = author_sequence(template_root, grafted_slots, regions)
    author_default_loop(template_root)
    author_interaction(template_root)

    bpy.context.scene.frame_start = 1
    bpy.context.scene.frame_end = end

    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(out_blend))
    print("[bootstrap_wheel_blend] wrote %s" % out_blend)
    print("  regions: %d, NLA beats: 4 + default + interaction, frames: %d" %
          (len(regions), end))


if __name__ == "__main__":
    main()
