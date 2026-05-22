"""
spatail_calibration_grid.py — glowing axis lines through every pivot
for visual verification of cylinder-fit accuracy.

Lines:
  - Magenta: crank journal axes (parallel to crank Z)
  - Cyan:    rod big-end ring axes (parallel to wrist-pin = crank Z)
  - Yellow:  cylinder bore axes (piston travel direction)

Each line is a thin cylinder mesh with an emissive material so it's
visible regardless of lighting. Spans ±AXIS_HALF_LENGTH from the pivot.
"""
import bpy, math
from mathutils import Vector, Matrix


COLORS = {
    "journal":   (1.0, 0.15, 1.0, 1.0),  # magenta
    "big_end":   (0.15, 0.85, 1.0, 1.0), # cyan
    "small_end": (1.0, 0.6, 0.15, 1.0),  # orange
    "bore_axis": (1.0, 1.0, 0.15, 1.0),  # yellow
}


def _emissive_material(name, rgba, strength=12.0):
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    for n in list(nt.nodes): nt.nodes.remove(n)
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs[0].default_value = rgba
    em.inputs[1].default_value = strength
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(em.outputs[0], out.inputs[0])
    mat.diffuse_color = rgba
    return mat


def add_axis_line(name, world_point, world_dir, half_length=4.0,
                    color_key="journal", thickness=0.06):
    """Add a thin glowing cylindrical line centred at world_point,
    pointing along world_dir, extending ±half_length."""
    if name in bpy.data.objects:
        bpy.data.objects.remove(bpy.data.objects[name], do_unlink=True)
    bpy.ops.mesh.primitive_cylinder_add(radius=thickness,
                                          depth=2.0 * half_length,
                                          location=world_point)
    line = bpy.context.active_object
    line.name = name
    direction = Vector(world_dir).normalized()
    line.rotation_euler = direction.to_track_quat("Z", "Y").to_euler()
    mat = _emissive_material(f"SPATAIL_grid.{color_key}", COLORS[color_key])
    line.data.materials.clear()
    line.data.materials.append(mat)
    return line


def add_dot(name, world_point, color_key="journal", radius=0.2):
    if name in bpy.data.objects:
        bpy.data.objects.remove(bpy.data.objects[name], do_unlink=True)
    bpy.ops.mesh.primitive_uv_sphere_add(radius=radius, location=world_point)
    dot = bpy.context.active_object
    dot.name = name
    mat = _emissive_material(f"SPATAIL_grid_dot.{color_key}", COLORS[color_key])
    dot.data.materials.clear()
    dot.data.materials.append(mat)
    return dot


def clear_grid():
    """Remove all SPATAIL_grid_* objects."""
    for nm in [o.name for o in bpy.data.objects
               if o.name.startswith("SPATAIL_grid_") or o.name.startswith("SPATAIL_axis_")]:
        bpy.data.objects.remove(bpy.data.objects[nm], do_unlink=True)


def build_grid_for_engine(half_length_cm=4.0):
    """Walk the rigged engine and add axis lines for every pivot point."""
    clear_grid()
    added = []

    # 1) Crank journal axes (magenta), parallel to crank Z
    crank_dir = Vector((0, 0, 1))
    for tgt in [o for o in bpy.data.objects if o.name.startswith("journal_target_")]:
        line = add_axis_line(f"SPATAIL_grid_journal.{tgt.name}",
                                tgt.matrix_world.translation, crank_dir,
                                half_length=half_length_cm, color_key="journal")
        dot = add_dot(f"SPATAIL_grid_dot.{tgt.name}",
                        tgt.matrix_world.translation, color_key="journal", radius=0.18)
        added.extend([line.name, dot.name])

    # 2) Bore axes for each piston (yellow)
    for piston in bpy.data.objects:
        if piston.type != "MESH" or not piston.get("spatail_slot"): continue
        slot = piston["spatail_slot"]
        bore = Vector(list(slot.get("boreAxisUnit", (0, 1, 0))))
        line = add_axis_line(f"SPATAIL_grid_bore.{piston.name}",
                                piston.matrix_world.translation, bore,
                                half_length=half_length_cm, color_key="bore_axis")
        added.append(line.name)

    # 3) Rod big-end ring axis dots (cyan) — small dots at the rod's
    # current big-end position (where the ring should be)
    for piston in bpy.data.objects:
        if piston.type != "MESH" or not piston.get("spatail_slot"): continue
        rod = next((c for c in piston.children
                    if any(con.name.startswith("SPATAIL_rod_to_journal")
                            for con in c.constraints)), None)
        if rod is None: continue
        # Big-end world: rod origin + local_long_axis × bbox_extent
        lo = Vector((float("inf"),)*3); hi = Vector((-float("inf"),)*3)
        for v in rod.data.vertices:
            lo = Vector(map(min, lo, v.co)); hi = Vector(map(max, hi, v.co))
        extents = hi - lo
        long_idx = max(range(3), key=lambda i: extents[i])
        axis_local = Vector((0,0,0)); axis_local[long_idx] = 1.0 if abs(hi[long_idx]) > abs(lo[long_idx]) else -1.0
        far_world = rod.matrix_world @ Vector(
            (hi.x if long_idx == 0 else 0,
             hi.y if long_idx == 1 else 0,
             hi.z if long_idx == 2 else 0))
        if abs(lo[long_idx]) > abs(hi[long_idx]):
            far_world = rod.matrix_world @ Vector(
                (lo.x if long_idx == 0 else 0,
                 lo.y if long_idx == 1 else 0,
                 lo.z if long_idx == 2 else 0))
        dot = add_dot(f"SPATAIL_grid_bigend.{rod.name}", far_world,
                       color_key="big_end", radius=0.12)
        added.append(dot.name)

    return {"grid_objects": len(added), "first_few": added[:5]}


print("[spatail_calibration_grid] module loaded.")
