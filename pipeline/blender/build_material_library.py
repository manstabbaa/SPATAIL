"""
build_material_library.py — writes assets_authoring/materials/spatail_pbr_library.blend
with one Principled-BSDF material per MATERIAL_CLASSES entry.

Authoring scripts then `bpy.data.libraries.load(... materials=...)` to
append the right material onto a freshly-imported mesh.

Run:
    blender --background --factory-startup \
        --python pipeline/blender/build_material_library.py \
        -- <output.blend>

The presets are deliberately shallow — albedo + roughness + metallic +
optional emission. No texture maps. Rough capability over depth, per
the product brief.
"""

import os
import sys

try:
    import bpy  # type: ignore
except ImportError:
    bpy = None

# Single source of truth. The Python classifier ships its own copy of the
# enum; this file's job is to materialise each name as a Blender material.
PRESETS = {
    # name                  (baseColor RGBA,         metallic, roughness, emission_strength, emission RGBA)
    "metal_polished":      ((0.92, 0.93, 0.95, 1.0), 1.00, 0.06, 0.0, (0, 0, 0, 1)),
    "metal_brushed":       ((0.71, 0.72, 0.74, 1.0), 0.90, 0.34, 0.0, (0, 0, 0, 1)),
    "plastic_matte":       ((0.07, 0.07, 0.08, 1.0), 0.00, 0.85, 0.0, (0, 0, 0, 1)),
    "plastic_soft_touch":  ((0.05, 0.05, 0.06, 1.0), 0.00, 0.92, 0.0, (0, 0, 0, 1)),
    "rubber":              ((0.04, 0.04, 0.04, 1.0), 0.00, 0.95, 0.0, (0, 0, 0, 1)),
    "glass_tinted":        ((0.08, 0.10, 0.13, 0.6), 0.00, 0.03, 0.0, (0, 0, 0, 1)),
    "display_emissive":    ((0.02, 0.03, 0.06, 1.0), 0.00, 0.20, 2.5, (0.30, 0.55, 1.00, 1)),
    "paint_clearcoat":     ((0.04, 0.06, 0.10, 1.0), 0.20, 0.20, 0.0, (0, 0, 0, 1)),
    "wood":                ((0.36, 0.22, 0.13, 1.0), 0.00, 0.65, 0.0, (0, 0, 0, 1)),
    "fabric":              ((0.28, 0.28, 0.32, 1.0), 0.00, 0.90, 0.0, (0, 0, 0, 1)),
    "placeholder_neutral": ((0.50, 0.50, 0.55, 1.0), 0.00, 0.60, 0.0, (0, 0, 0, 1)),
}

MATERIAL_NAME_PREFIX = "SPATAIL_PBR_"


def reset_scene():
    if bpy is None:
        return
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for coll in (bpy.data.materials, bpy.data.images, bpy.data.cameras, bpy.data.lights):
        for b in list(coll):
            try: coll.remove(b, do_unlink=True)
            except Exception: pass


def make_material(name, base_color, metallic, roughness, emission_strength, emission_color):
    full_name = MATERIAL_NAME_PREFIX + name
    mat = bpy.data.materials.new(full_name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = base_color
    bsdf.inputs["Metallic"].default_value = metallic
    bsdf.inputs["Roughness"].default_value = roughness
    # Emission inputs vary by Blender version; set whichever ones exist.
    if "Emission Strength" in bsdf.inputs:
        bsdf.inputs["Emission Strength"].default_value = emission_strength
    for key in ("Emission Color", "Emission"):
        if key in bsdf.inputs:
            bsdf.inputs[key].default_value = emission_color
            break
    # Tint the transparency for glass_tinted only — others stay opaque.
    if name == "glass_tinted":
        mat.blend_method = "BLEND"
        if "Alpha" in bsdf.inputs:
            bsdf.inputs["Alpha"].default_value = base_color[3]
    # Tag with a custom property so the authoring import can introspect
    # which preset a slot was filled from later.
    mat["spatail_class"] = name
    return mat


def main():
    if bpy is None:
        print("[build_material_library] run inside Blender", file=sys.stderr)
        sys.exit(2)
    try:
        argv = sys.argv[sys.argv.index("--") + 1:]
    except ValueError:
        argv = []
    if len(argv) < 1:
        print("usage: blender --background --factory-startup --python "
              "build_material_library.py -- <output.blend>", file=sys.stderr)
        sys.exit(2)
    out_blend = os.path.abspath(argv[0])
    os.makedirs(os.path.dirname(out_blend), exist_ok=True)

    reset_scene()
    for name, args in PRESETS.items():
        m = make_material(name, *args)
        m.use_fake_user = True  # so the library .blend keeps the material on save

    bpy.ops.wm.save_as_mainfile(filepath=out_blend)
    print("[build_material_library] wrote %s (%d presets)" % (out_blend, len(PRESETS)))


if __name__ == "__main__":
    main()
