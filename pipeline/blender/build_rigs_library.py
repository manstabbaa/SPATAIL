"""
build_rigs_library.py — writes pre-rigged templates into assets_authoring/rigs/.

One .blend per RIG_KINDS entry, named rig.<kind>.blend. Each template
follows the convention:

    <template_root>          empty, custom-properties carry rig parameters
        <part proxy empties> empties named rig_slot.<n> with drivers wired
                             to the template_root's properties

The authoring import grafts an imported mesh into the right rig by
parenting it to the rig_slot.<n> that matches the mesh's region name.

This pass ships rough templates — quantity over depth. exploded_assembly
is the only one the wheel demo actually uses today; the others exist so
the next asset that lands has the rig ready when it does.

Run:
    blender --background --factory-startup \
        --python pipeline/blender/build_rigs_library.py \
        -- <output_dir>
"""

import math
import os
import sys

try:
    import bpy  # type: ignore
    from mathutils import Vector  # type: ignore
except ImportError:
    bpy = None
    Vector = None


RIGS_TO_BUILD = [
    "rig.transform_only",
    "rig.parented_group",
    "rig.hinged_panel",
    "rig.exploded_assembly",
]


def reset_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for coll in (bpy.data.objects, bpy.data.armatures, bpy.data.actions,
                 bpy.data.collections):
        for b in list(coll):
            try: coll.remove(b, do_unlink=True)
            except Exception: pass


def add_empty(name, location=(0, 0, 0), parent=None):
    e = bpy.data.objects.new(name, None)
    e.empty_display_size = 0.05
    e.location = location
    bpy.context.scene.collection.objects.link(e)
    if parent is not None:
        e.parent = parent
    return e


def add_driver(target_obj, target_path, target_index, source_obj, source_prop, expr):
    """Attach a driver on target_obj.target_path[index] that reads
    `source_obj["source_prop"]` and evaluates `expr` (which can reference `v`).
    """
    fcurve = target_obj.driver_add(target_path, target_index)
    drv = fcurve.driver
    drv.type = "SCRIPTED"
    var = drv.variables.new()
    var.name = "v"
    var.type = "SINGLE_PROP"
    var.targets[0].id = source_obj
    var.targets[0].data_path = '["%s"]' % source_prop
    drv.expression = expr
    return fcurve


def build_transform_only(out_dir):
    """Trivial: one root empty named template_root with no slots beyond
    the implicit "graft here" anchor. The import script parents the mesh
    directly to template_root."""
    reset_scene()
    root = add_empty("template_root")
    root["spatail_rig_kind"] = "transform_only"
    root["spatail_doc"] = "Parent the imported mesh to template_root. Animate the mesh's own transform."
    save_as(root, out_dir, "rig.transform_only.blend")


def build_parented_group(out_dir):
    """N labelled slot empties under template_root. Each slot is the parent
    for a sub-mesh. Animations happen on the slots."""
    reset_scene()
    root = add_empty("template_root")
    root["spatail_rig_kind"] = "parented_group"
    root["spatail_doc"] = "Parent each sub-mesh to a rig_slot.* empty. Animate the slots, not the meshes."
    for i in range(6):
        add_empty("rig_slot.%02d" % i, parent=root)
    save_as(root, out_dir, "rig.parented_group.blend")


def build_hinged_panel(out_dir):
    """Door/lid hinge: template_root + a hinge_axis empty rotated around
    +Y by a driver tied to `open_amount` ∈ [0,1]. The imported panel
    parents to hinge_axis."""
    reset_scene()
    root = add_empty("template_root")
    root["spatail_rig_kind"] = "hinged_panel"
    root["spatail_doc"] = "Parent the panel to hinge_axis. Drive 'open_amount' 0..1 to open."
    root["open_amount"] = 0.0
    # Custom property UI range (Blender 4.x stores via id_properties_ui).
    try:
        ui = root.id_properties_ui("open_amount")
        ui.update(min=0.0, max=1.0, soft_min=0.0, soft_max=1.0)
    except Exception:
        pass
    hinge = add_empty("hinge_axis", parent=root)
    add_driver(hinge, "rotation_euler", 1, root, "open_amount", "v * 1.5708")  # 0..90°
    save_as(root, out_dir, "rig.hinged_panel.blend")


def build_exploded_assembly(out_dir, slots=8):
    """Master 'explode_amount' ∈ [0,1] drives all N slots radially outward
    from the rig origin along +Y (and a bit of XZ outward). The mesh
    region's bounding-box centroid is the radial direction; the import
    script positions each slot at its region's centroid at rest, then
    parents the region's mesh to the slot."""
    reset_scene()
    root = add_empty("template_root")
    root["spatail_rig_kind"] = "exploded_assembly"
    root["spatail_doc"] = ("Parent each region's sub-mesh to a rig_slot.* empty. "
                            "Drive 'explode_amount' 0..1 to spread parts outward. "
                            "Slot rest positions are set by the authoring import.")
    root["explode_amount"] = 0.0
    try:
        ui = root.id_properties_ui("explode_amount")
        ui.update(min=0.0, max=1.0, soft_min=0.0, soft_max=1.0)
    except Exception:
        pass
    # Slots arranged in a ring at rest so the template is visually sensible
    # when previewed before grafting. The authoring script overwrites
    # their rest locations using the region centroids.
    for i in range(slots):
        angle = (i / slots) * math.pi * 2
        slot = add_empty(
            "rig_slot.%02d" % i,
            location=(math.cos(angle) * 0.18, 0.0, math.sin(angle) * 0.18),
            parent=root,
        )
        # Store the rest direction so the driver knows where to push.
        slot["rest_dir"] = [math.cos(angle), 0.4, math.sin(angle)]
        # Drive a +Y lift proportional to explode_amount × the slot's
        # rest_dir.y. The authoring import overwrites rest_dir based on
        # actual region centroids before saving the grafted .blend, so
        # the explosion direction matches the mesh.
        for axis_index in (0, 1, 2):
            expr = "v * %.4f" % (0.18 if axis_index == 1 else 0.10)
            fcurve = add_driver(slot, "location", axis_index, root, "explode_amount", expr)
            # We'll rewrite this driver expression at graft time, but
            # leave a reasonable default so the template previews.
    save_as(root, out_dir, "rig.exploded_assembly.blend")


def save_as(root, out_dir, filename):
    out_path = os.path.abspath(os.path.join(out_dir, filename))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    # Keep all data-blocks in the file so library appends work.
    for obj in bpy.data.objects:
        obj.use_fake_user = True
    bpy.ops.wm.save_as_mainfile(filepath=out_path)
    print("[build_rigs_library] wrote %s" % out_path)


def main():
    if bpy is None:
        print("[build_rigs_library] run inside Blender", file=sys.stderr)
        sys.exit(2)
    try:
        argv = sys.argv[sys.argv.index("--") + 1:]
    except ValueError:
        argv = []
    if len(argv) < 1:
        print("usage: blender --background --factory-startup --python "
              "build_rigs_library.py -- <output_dir>", file=sys.stderr)
        sys.exit(2)
    out_dir = argv[0]
    os.makedirs(out_dir, exist_ok=True)

    build_transform_only(out_dir)
    build_parented_group(out_dir)
    build_hinged_panel(out_dir)
    build_exploded_assembly(out_dir)


if __name__ == "__main__":
    main()
