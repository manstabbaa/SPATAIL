"""
spatail_segment_colors.py — apply per-part viewport+render colors.

See skills/spatail-segment-colors/SKILL.md.

CONTRACT:
  Reads rig data from the live scene (spatail_slot custom props, Damped
  Track constraints, journal_target parenting) to derive each mesh's
  (role, cyl, bank). Assigns one Principled-BSDF material per mesh with
  a deterministic color from a per-role hue band. Saves the modified
  scene as <save_as> so the original calibrated/rigged .blend stays
  pristine for the realistic-materials pass.
"""
import bpy, colorsys, json, os
from mathutils import Vector


ROLE_HUE = {
    "crank_throw":    0.00,   # red
    "connecting_rod": 0.33,   # green
    "piston":         0.60,   # blue
    "wrist_pin":      0.10,   # orange
}


def _hsv_to_rgba(h, s, v):
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
    return (r, g, b, 1.0)


def _color_for(role, cyl=None, bank=None, instance_idx=0):
    """Deterministic colour per (role, cyl, bank).

    Cyl shifts hue ±0.04 around the role base; bank shifts value (B
    brighter, A slightly darker); instance_idx breaks ties for unclassified."""
    h_base = ROLE_HUE.get(role)
    if h_base is None:
        # Unknown role → mid-grey + instance index in alpha-as-hue
        return (0.55, 0.55, 0.55, 1.0)
    cyl = cyl if cyl is not None else (instance_idx + 1)
    hue = h_base + ((cyl - 3) * 0.022)
    sat = 0.85
    val = 0.85 if bank == "B" else 0.55 if bank == "A" else 0.70
    return _hsv_to_rgba(hue, sat, val)


def _get_or_make_material(name, base_color_rgba):
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    # Principled BSDF base color
    nt = mat.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    if bsdf is None:
        # Add one if missing
        bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Base Color"].default_value = base_color_rgba
    # Slight roughness so it doesn't read as plastic-mirror
    try:
        bsdf.inputs["Roughness"].default_value = 0.55
    except Exception: pass
    # Viewport display color (for Solid mode)
    mat.diffuse_color = base_color_rgba
    return mat


def _assign_material(obj, mat):
    """Replace all material slots with this one material."""
    obj.data.materials.clear()
    obj.data.materials.append(mat)
    # Object colour too (for object-colour viewport mode)
    obj.color = mat.diffuse_color


def _classify_scene():
    """Walk the scene and group meshes by role using rig data.

    Returns dict: {role: [(obj, cyl, bank, instance_idx), ...]}
    """
    out = {"crank_throw": [], "connecting_rod": [], "piston": [],
           "wrist_pin": [], "unclassified": []}

    # 1) Throws: meshes parented to crank_assembly
    ca = bpy.data.objects.get("crank_assembly")
    throws = []
    if ca:
        for child in ca.children:
            if child.type == "MESH":
                throws.append(child)
    # Sort throws by their Z so we can give them cyl indices 1..N
    throws.sort(key=lambda o: -o.matrix_world.translation.z)
    for i, t in enumerate(throws):
        out["crank_throw"].append((t, i + 1, None, i))

    # 2) Pistons: meshes with spatail_slot
    pistons = []
    for o in bpy.data.objects:
        if o.type == "MESH" and o.get("spatail_slot"):
            slot = o["spatail_slot"]
            pistons.append((o, int(slot.get("cyl", 0)), str(slot.get("bank", ""))))
    for o, cyl, bank in pistons:
        out["piston"].append((o, cyl, bank, 0))

    # 3) Rods: children of pistons with SPATAIL_rod_to_journal constraint
    rods = []
    for piston_obj, cyl, bank in pistons:
        for child in piston_obj.children:
            if child.type != "MESH": continue
            is_rod = any(c.name.startswith("SPATAIL_rod_to_journal")
                         for c in child.constraints)
            if is_rod:
                rods.append((child, cyl, bank))
    for o, cyl, bank in rods:
        out["connecting_rod"].append((o, cyl, bank, 0))

    # 4) Wrist pins: children of pistons that AREN'T rods
    pins = []
    rod_names = {r[0].name for r in rods}
    for piston_obj, cyl, bank in pistons:
        for child in piston_obj.children:
            if child.type != "MESH": continue
            if child.name in rod_names: continue
            pins.append((child, cyl, bank))
    for o, cyl, bank in pins:
        out["wrist_pin"].append((o, cyl, bank, 0))

    # 5) Unclassified: any remaining mesh not in the above
    classified = set()
    for role_list in out.values():
        for rec in role_list:
            classified.add(rec[0].name)
    for o in bpy.data.objects:
        if o.type != "MESH": continue
        if o.name in classified: continue
        out["unclassified"].append((o, None, None, len(out["unclassified"])))

    return out


def apply_segmentation_colors(save_as=None, write_legend=None):
    """Apply the segmentation colours to the current scene + optionally
    save as a new .blend and write a legend JSON."""
    grouped = _classify_scene()
    legend = []
    applied = 0
    for role, recs in grouped.items():
        for obj, cyl, bank, instance_idx in recs:
            color = _color_for(role, cyl=cyl, bank=bank, instance_idx=instance_idx)
            mat_name = f"SPATAIL_seg.{role}"
            if cyl is not None:
                mat_name += f".cyl{cyl}"
            if bank:
                mat_name += f".{bank}"
            mat_name += f".{obj.name}"
            mat = _get_or_make_material(mat_name, color)
            _assign_material(obj, mat)
            legend.append({
                "object": obj.name,
                "role": role,
                "cyl": cyl,
                "bank": bank,
                "color_rgb": [round(c, 4) for c in color[:3]],
            })
            applied += 1

    # Configure viewport shading so the segmentation actually shows
    try:
        for screen in bpy.data.screens:
            for area in screen.areas:
                if area.type != "VIEW_3D": continue
                for space in area.spaces:
                    if space.type != "VIEW_3D": continue
                    space.shading.type = "MATERIAL"
                    # Solid mode fallback colour source
                    try: space.shading.color_type = "MATERIAL"
                    except Exception: pass
    except Exception: pass

    # Save to <save_as> if provided (so the source .blend stays pristine)
    if save_as:
        os.makedirs(os.path.dirname(save_as), exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=save_as, copy=False)
        print(f"[segment_colors] saved -> {save_as}")

    if write_legend:
        os.makedirs(os.path.dirname(write_legend), exist_ok=True)
        with open(write_legend, "w") as f:
            json.dump({"parts": legend,
                       "role_hue_bands": ROLE_HUE,
                       "convention": "bank B brighter than bank A; cyl shifts hue"},
                      f, indent=2)
        print(f"[segment_colors] legend -> {write_legend}")

    print(f"[segment_colors] applied to {applied} parts across "
          f"{sum(1 for v in grouped.values() if v)} role groups")
    return {"appliedCount": applied,
            "byRole": {r: len(v) for r, v in grouped.items()},
            "legend": legend[:3]}  # first 3 for quick inspection


print("[spatail_segment_colors] module loaded.")
