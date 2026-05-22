"""Render high-fidelity validation views of a CAD asset for SPATAIL.

Usage (headless):
  blender --background --factory-startup --python segment_obj.py -- \
      <input_path> <output_dir> <asset_id>

Why this script exists
----------------------
Loose-parts segmentation doesn't work on CAD-exported OBJs whose
geometry is topologically a single connected mesh (e.g. an F1 steering
wheel from OpenCascade). Even after welding by distance the buttons,
paddles, and rotaries are physically attached to the chassis in the
source CAD, so they collapse into one connected component.

The honest move is:
  1. Import + normalise the geometry (units, axis, scale).
  2. Assign baseline PBR materials by inferred kind so the renders are
     legible, not flat-grey.
  3. Render 4 high-quality validation views (front / 3-quarter /
     top-down / back) using Eevee Next + a 3-point lighting rig.
  4. Export a single clean GLB.
  5. Emit a manifest the next pipeline step uses for VISUAL labelling
     (look at the renders, define labelled regions as 3D bounding boxes
     on top of the wheel).

The manifest carries:
  - geometry stats (bbox in meters, vertex / face count)
  - normalisation transform (so the labeller knows what frame to use)
  - paths to all rendered views
  - parts: [] (empty in v0.1 — filled in by the manual / Claude labelling step)
"""

import bpy
import sys
import os
import json
import math
import traceback
from mathutils import Vector

# --------------------------------------------------------------------------
# Tunables
# --------------------------------------------------------------------------

PREVIEW_PIXELS = 1600
EEVEE_SAMPLES = 96
BG_RGBA = (0.06, 0.07, 0.10, 1.0)
RENDER_EXPOSURE = -1.5          # pull the whole frame down — Filmic alone overcooks
# Energies in Watts at the AREA-light's *position*. With sub-metre objects
# the lights end up very close, so values stay small. Tuned visually.
LIGHT_ENERGY_KEY = 6
LIGHT_ENERGY_FILL = 2.4
LIGHT_ENERGY_RIM = 1.8
LIGHT_DIST_FACTOR = 4.5

# Source CAD units. The Mercedes wheel OBJ is in millimetres; STL files
# from SolidWorks are usually mm too. The pipeline normalises to metres
# so the viewer's auto-fit math stays well-conditioned.
ASSUMED_MM_UNITS = True

# Camera setups — (label, azimuth°, elevation°, ortho?). Azimuth is around
# +Z, 0° looks at -Y face. Elevation tilts up.
VIEWS = [
    ("front",   0,    0,   False),
    ("threeq",  -25, 25,   False),
    ("top",     0,    89,  True),
    ("back",    180,  0,   False),
]


# --------------------------------------------------------------------------
# Plumbing
# --------------------------------------------------------------------------

def parse_args():
    if "--" not in sys.argv:
        sys.exit("error: pass args after '--'")
    rest = sys.argv[sys.argv.index("--") + 1:]
    if len(rest) < 3:
        sys.exit("error: <input_path> <output_dir> <asset_id>")
    return rest[0], rest[1], rest[2]


def reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    # Default world is too bright; we'll add our own lighting.
    if bpy.context.scene.world is None:
        bpy.context.scene.world = bpy.data.worlds.new("World")


def import_mesh(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".obj":
        if hasattr(bpy.ops.wm, "obj_import"):
            bpy.ops.wm.obj_import(filepath=path)
        else:
            bpy.ops.import_scene.obj(filepath=path)
    elif ext in (".gltf", ".glb"):
        bpy.ops.import_scene.gltf(filepath=path)
    elif ext == ".stl":
        if hasattr(bpy.ops.wm, "stl_import"):
            bpy.ops.wm.stl_import(filepath=path)
        else:
            bpy.ops.import_mesh.stl(filepath=path)
    else:
        raise RuntimeError(f"unsupported extension: {ext}")


def all_meshes():
    return [o for o in bpy.data.objects if o.type == "MESH"]


def join_all_meshes():
    meshes = all_meshes()
    if not meshes:
        return None
    if len(meshes) == 1:
        bpy.context.view_layer.objects.active = meshes[0]
        return meshes[0]
    for o in bpy.data.objects:
        o.select_set(False)
    target = meshes[0]
    for m in meshes:
        m.select_set(True)
    bpy.context.view_layer.objects.active = target
    bpy.ops.object.join()
    return target


def world_bbox(obj):
    minv = [math.inf] * 3
    maxv = [-math.inf] * 3
    for c in obj.bound_box:
        w = obj.matrix_world @ Vector((c[0], c[1], c[2]))
        for i in range(3):
            minv[i] = min(minv[i], w[i])
            maxv[i] = max(maxv[i], w[i])
    return minv, maxv


def cleanup_geometry(obj):
    """Repair the topological mess CAD tessellators leave behind.

    OpenCascade-style OBJ/STL exports are unwelded patches (one mesh
    fragment per source NURBS face) with frequently inconsistent winding.
    Three.js culls back-faces by default, so flipped faces become
    invisible — that's the swiss-cheese look you see in the viewer.

    Fix order matters:
      1. Merge by distance (welds the seams between patches)
      2. Recalculate normals (makes winding outward-consistent)
      3. Re-merge a second time (some neighbouring faces only become
         mergeable after their normals point the same way)

    Returns a small report so the manifest records what changed.
    """
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)

    verts_before = len(obj.data.vertices)
    faces_before = len(obj.data.polygons)

    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")

    # Weld: distance is in metres at this point (after scale). 0.0005 m =
    # 0.5 mm — generous for CAD-tessellated meshes, tight enough that we
    # don't collapse genuine features.
    bpy.ops.mesh.remove_doubles(threshold=0.0005)

    # Outward normals. `inside=False` flips faces whose normals point
    # into the volume, leaving them all facing out.
    bpy.ops.mesh.normals_make_consistent(inside=False)

    # Second weld pass — some shared edges only collapse after winding
    # is consistent, because remove_doubles checks compatible flow.
    bpy.ops.mesh.remove_doubles(threshold=0.0005)

    # Shade smooth so the high-poly tessellation reads as a smooth
    # surface, not flat-shaded polygon facets. Auto-smooth angle keeps
    # genuine creases sharp.
    bpy.ops.mesh.faces_shade_smooth()

    bpy.ops.object.mode_set(mode="OBJECT")

    # Auto-smooth at 50° preserves real edges (label engravings) while
    # smoothing the rest. The modifier API is the iOS-compatible route.
    if hasattr(bpy.ops.object, "shade_auto_smooth"):
        try:
            bpy.ops.object.shade_auto_smooth(angle=math.radians(50))
        except Exception:
            pass

    verts_after = len(obj.data.vertices)
    faces_after = len(obj.data.polygons)
    return {
        "verticesBefore": verts_before,
        "verticesAfter": verts_after,
        "facesBefore": faces_before,
        "facesAfter": faces_after,
        "welded": verts_before - verts_after,
    }


def normalise_units_and_centre(obj):
    """Scale to metres if needed, then translate so the bbox is centred
    on the origin. The viewer's auto-fit assumes meters. Returns the
    applied transform so the manifest can record it.
    """
    minv, maxv = world_bbox(obj)
    size = max(maxv[i] - minv[i] for i in range(3))
    # If the longest axis is > 10 we're almost certainly in mm.
    scale = 0.001 if (size > 10 and ASSUMED_MM_UNITS) else 1.0
    obj.scale = (scale, scale, scale)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

    minv, maxv = world_bbox(obj)
    centre = [(minv[i] + maxv[i]) / 2 for i in range(3)]
    obj.location = (-centre[0], -centre[1], -centre[2])
    bpy.ops.object.transform_apply(location=True, rotation=False, scale=False)

    minv, maxv = world_bbox(obj)
    return {
        "scaleApplied": scale,
        "translationApplied": [-centre[0], -centre[1], -centre[2]],
        "bboxAfter": {"min": minv, "max": maxv},
    }


# --------------------------------------------------------------------------
# Materials
# --------------------------------------------------------------------------

def make_baseline_pbr_material():
    """A satin-black plastic. This is what F1 wheel chassis actually is.
    Non-metallic, mid-roughness, slightly tinted — surface detail
    (engraved labels, button rims, vent slots) reads clearly under
    sane lighting without any specular blow-out.
    """
    mat = bpy.data.materials.new("baseline_chassis")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        def set_input(key, value):
            if key in bsdf.inputs:
                bsdf.inputs[key].default_value = value
        set_input("Base Color", (0.035, 0.035, 0.040, 1.0))
        set_input("Roughness", 0.55)
        set_input("Metallic", 0.0)
        if "Specular IOR Level" in bsdf.inputs:
            set_input("Specular IOR Level", 0.35)
    return mat


def assign_material(obj, mat):
    obj.data.materials.clear()
    obj.data.materials.append(mat)


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def setup_eevee():
    scene = bpy.context.scene
    for engine in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
        try:
            scene.render.engine = engine
            break
        except Exception:
            continue
    scene.render.resolution_x = PREVIEW_PIXELS
    scene.render.resolution_y = PREVIEW_PIXELS
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.film_transparent = False

    # Filmic tone mapping makes the metals/blacks read properly.
    scene.view_settings.view_transform = "Filmic"
    scene.view_settings.look = "Medium Contrast"
    scene.view_settings.exposure = RENDER_EXPOSURE

    # World background.
    world = scene.world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs[0].default_value = BG_RGBA
        bg.inputs[1].default_value = 0.4

    # Eevee Next antialiasing.
    eevee = getattr(scene, "eevee", None)
    if eevee:
        if hasattr(eevee, "taa_render_samples"):
            eevee.taa_render_samples = EEVEE_SAMPLES
        if hasattr(eevee, "use_raytracing"):
            eevee.use_raytracing = True
        if hasattr(eevee, "use_shadow_jittered_viewport"):
            eevee.use_shadow_jittered_viewport = True


def add_three_point_lighting(centre, scale):
    def add_light(name, kind, energy, location, color=(1, 1, 1)):
        data = bpy.data.lights.new(name=name, type=kind)
        data.energy = energy
        data.color = color
        if kind == "AREA":
            data.size = scale * 2.0
        obj = bpy.data.objects.new(name=name, object_data=data)
        obj.location = location
        bpy.context.collection.objects.link(obj)
        # Aim at centre
        direction = Vector(centre) - Vector(location)
        obj.rotation_mode = "QUATERNION"
        obj.rotation_quaternion = direction.to_track_quat("-Z", "Y")
        return obj

    d = LIGHT_DIST_FACTOR
    add_light("Key",   "AREA", LIGHT_ENERGY_KEY,
              (centre[0] + scale * d * 0.7, centre[1] - scale * d * 0.8, centre[2] + scale * d * 0.7))
    add_light("Fill",  "AREA", LIGHT_ENERGY_FILL,
              (centre[0] - scale * d * 0.8, centre[1] - scale * d * 0.6, centre[2] + scale * d * 0.3),
              color=(0.85, 0.92, 1.0))
    add_light("Rim",   "AREA", LIGHT_ENERGY_RIM,
              (centre[0],                   centre[1] + scale * d * 0.9, centre[2] + scale * d * 0.4),
              color=(1.0, 0.95, 0.85))


def make_camera(name, location, target, ortho=False, ortho_scale=0.4):
    data = bpy.data.cameras.new(name)
    data.type = "ORTHO" if ortho else "PERSP"
    if ortho:
        data.ortho_scale = ortho_scale
    else:
        data.lens = 50  # mm-equivalent
    cam = bpy.data.objects.new(name, data)
    cam.location = location
    direction = Vector(target) - Vector(location)
    cam.rotation_mode = "QUATERNION"
    cam.rotation_quaternion = direction.to_track_quat("-Z", "Y")
    bpy.context.collection.objects.link(cam)
    return cam


def camera_for_view(label, azimuth_deg, elevation_deg, ortho, centre, radius):
    az = math.radians(azimuth_deg)
    el = math.radians(elevation_deg)
    # Distance: 2.5× radius for perspective, anything for ortho.
    dist = radius * 2.8
    # Spherical: azimuth around Z, elevation off the XY plane.
    cx = centre[0] + dist * math.cos(el) * math.sin(az)
    cy = centre[1] - dist * math.cos(el) * math.cos(az)
    cz = centre[2] + dist * math.sin(el)
    cam = make_camera(
        f"Cam_{label}",
        (cx, cy, cz),
        centre,
        ortho=ortho,
        ortho_scale=radius * 2.6,
    )
    return cam


def render_to(path):
    bpy.context.scene.render.filepath = path
    bpy.ops.render.render(write_still=True)


def render_views(output_dir, asset_id, centre, radius):
    paths = {}
    for (label, az, el, ortho) in VIEWS:
        cam = camera_for_view(label, az, el, ortho, centre, radius)
        bpy.context.scene.camera = cam
        target_path = os.path.join(output_dir, f"{asset_id}__view_{label}.png")
        render_to(target_path)
        paths[label] = os.path.basename(target_path)
    return paths


# --------------------------------------------------------------------------
# GLB export
# --------------------------------------------------------------------------

def export_glb(output_dir, asset_id):
    glb_path = os.path.join(output_dir, f"{asset_id}.glb")
    bpy.ops.export_scene.gltf(
        filepath=glb_path,
        export_format="GLB",
        export_apply=True,
        export_yup=True,
    )
    return glb_path


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main():
    input_path, output_dir, asset_id = parse_args()
    os.makedirs(output_dir, exist_ok=True)
    manifest_path = os.path.join(output_dir, f"{asset_id}.segmentation.json")

    manifest = {
        "assetId": asset_id,
        "sourcePath": input_path,
        "status": "failed",
        "reason": None,
        "geometry": None,
        "normalisation": None,
        "renders": {},
        "glb": None,
        "parts": [],
        "labellingStatus": "pending_visual_review",
        "labellingNote":
            "Geometry-based segmentation was not attempted — the source "
            "CAD is topologically a single connected mesh. Define parts "
            "by visually labelling regions on the rendered views and "
            "writing them back into this manifest's `parts` array as "
            "3D bounding boxes in the normalised (post-translation, "
            "post-scale) frame.",
    }

    try:
        if not os.path.isfile(input_path):
            raise RuntimeError(f"input not found: {input_path}")
        reset_scene()
        import_mesh(input_path)

        joined = join_all_meshes()
        if not joined:
            raise RuntimeError("import produced no mesh objects")

        # Drop non-meshes.
        for o in list(bpy.data.objects):
            if o.type != "MESH":
                bpy.data.objects.remove(o, do_unlink=True)

        # Unit / centre normalisation.
        manifest["normalisation"] = normalise_units_and_centre(joined)

        # Topology cleanup — welds disconnected tessellation patches and
        # forces consistent outward winding. Without this, ~3.6% of face
        # pairs render invisible (back-face culled) because OpenCascade
        # exports inconsistent normals.
        manifest["cleanup"] = cleanup_geometry(joined)

        # Material.
        assign_material(joined, make_baseline_pbr_material())

        # Geometry stats — recomputed post-normalisation, in metres.
        mn, mx = world_bbox(joined)
        size = [mx[i] - mn[i] for i in range(3)]
        centre = [(mn[i] + mx[i]) / 2 for i in range(3)]
        radius = max(size) / 2
        manifest["geometry"] = {
            "vertexCount": len(joined.data.vertices),
            "faceCount":   len(joined.data.polygons),
            "bbox": {"min": mn, "max": mx},
            "sizeMeters": size,
            "centroid": centre,
        }

        # Renders.
        setup_eevee()
        add_three_point_lighting(centre, radius)
        manifest["renders"] = render_views(output_dir, asset_id, centre, radius)

        # GLB export.
        manifest["glb"] = os.path.basename(export_glb(output_dir, asset_id))

        manifest["status"] = "ok"
    except Exception as e:
        manifest["reason"] = f"{type(e).__name__}: {e}"
        manifest["traceback"] = traceback.format_exc()
    finally:
        with open(manifest_path, "w", encoding="utf-8") as fp:
            json.dump(manifest, fp, indent=2, default=str)
        print(f"[segment_obj] wrote {manifest_path}")


if __name__ == "__main__":
    main()
