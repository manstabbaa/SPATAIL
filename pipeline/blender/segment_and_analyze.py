"""
segment_and_analyze.py — CAD → parts analysis pipeline, run inside Blender.

Workflow (matches the user's spec):
  1. Get the CAD (OBJ or .blend with a named collection).
  2. Segment / separate it into its own meshes.
  3. Render out each mesh from front / iso / top.
  4. Store the data of what this mesh is + role it plays.
  5. The labelling step is the AGENT's loop — it views the renders and
     fills the `semantic` block on each part. This script leaves
     `semantic` empty so the agent always owns the labels.

Run:
    blender --background --factory-startup \
        --python pipeline/blender/segment_and_analyze.py \
        -- <input.obj|input.blend> <assetId>

Output:
    assets_processed/segmented/<assetId>/
        parts/<part_name>.front.png
        parts/<part_name>.iso.png
        parts/<part_name>.top.png
        <assetId>.parts.json   — analysis skeleton the agent fills in
"""

import bpy, os, sys, json, math
from mathutils import Vector


def reset_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for coll in (bpy.data.meshes, bpy.data.materials, bpy.data.cameras,
                 bpy.data.lights, bpy.data.images, bpy.data.actions,
                 bpy.data.collections):
        for b in list(coll):
            try: coll.remove(b, do_unlink=True)
            except Exception: pass


def import_input(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".blend":
        # Append every object from the source .blend.
        with bpy.data.libraries.load(path, link=False) as (src, dst):
            dst.objects = list(src.objects)
        for obj in bpy.data.objects:
            if obj.users == 0:
                bpy.context.scene.collection.objects.link(obj)
    elif ext in (".obj",):
        # use_split_groups=True turns OBJ `g <name>` markers into separate
        # Blender objects. CAD exports use this; we want it. If the OBJ
        # has no groups at all, the loose-separate fallback later kicks in.
        try:
            bpy.ops.wm.obj_import(
                filepath=path,
                use_split_objects=True,
                use_split_groups=True,
            )
        except TypeError:
            # Older Blender: signature differed.
            bpy.ops.wm.obj_import(filepath=path)
        except Exception:
            try:
                bpy.ops.import_scene.obj(
                    filepath=path,
                    use_split_objects=True,
                    use_split_groups=True,
                )
            except TypeError:
                bpy.ops.import_scene.obj(filepath=path)
    elif ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=path)
    else:
        raise SystemExit(f"unsupported CAD extension: {ext}")


def collect_mesh_parts():
    """Return every MESH object currently in the scene (post-import)."""
    return [o for o in bpy.data.objects if o.type == "MESH"]


def separate_loose_parts():
    """For each mesh, try mesh.separate(type='LOOSE'). Returns the new
    full list of meshes. If a mesh has only one connected island, this
    is a no-op for that mesh."""
    bpy.ops.object.select_all(action="DESELECT")
    parts = collect_mesh_parts()
    for o in parts:
        bpy.context.view_layer.objects.active = o
        bpy.ops.object.select_all(action="DESELECT")
        o.select_set(True)
        try:
            bpy.ops.mesh.separate(type="LOOSE")
        except Exception:
            pass
    return collect_mesh_parts()


def bbox_region_split(obj, regions_xy):
    """Fallback when the CAD is one monolithic mesh and loose-separate
    didn't help. Splits faces into N regions by 2D bounding-box position
    (X / Z plane), extracts each into its own object. The agent's
    visual-inspection step then labels what each region actually is.

    `regions_xy` is a list of (name, fn(centroid)→bool) — first matching
    region wins. Falls back to "_other" for unclassified faces.
    """
    import bmesh
    bm = bmesh.new(); bm.from_mesh(obj.data); bm.faces.ensure_lookup_table()

    buckets = {r[0]: [] for r in regions_xy}
    buckets["_other"] = []
    for face in bm.faces:
        c = face.calc_center_median()
        placed = False
        for name, fn in regions_xy:
            if fn(c):
                buckets[name].append(face)
                placed = True
                break
        if not placed:
            buckets["_other"].append(face)

    new_objects = []
    for name, faces in buckets.items():
        if not faces: continue
        new_bm = bmesh.new()
        vert_map = {}
        for f in faces:
            verts = []
            for v in f.verts:
                if v not in vert_map:
                    vert_map[v] = new_bm.verts.new(v.co)
                verts.append(vert_map[v])
            try: new_bm.faces.new(verts)
            except ValueError: pass
        new_mesh = bpy.data.meshes.new(name + "_mesh")
        new_bm.to_mesh(new_mesh); new_bm.free()
        new_obj = bpy.data.objects.new(name, new_mesh)
        bpy.context.scene.collection.objects.link(new_obj)
        new_objects.append(new_obj)
    bm.free()
    bpy.data.objects.remove(obj, do_unlink=True)
    return new_objects


def ensure_intro_camera():
    name = "_introspect_cam"
    obj = bpy.data.objects.get(name)
    if obj is None:
        cam_data = bpy.data.cameras.new(name); cam_data.lens = 50
        obj = bpy.data.objects.new(name, cam_data)
        bpy.context.scene.collection.objects.link(obj)
    bpy.context.scene.camera = obj
    return obj


def union_bbox(objs):
    lo = Vector((float("inf"),)*3); hi = Vector((-float("inf"),)*3)
    for o in objs:
        for c in o.bound_box:
            p = o.matrix_world @ Vector(c)
            lo = Vector(map(min, lo, p)); hi = Vector(map(max, hi, p))
    return lo, hi


def safe_filename(name):
    """Sanitize for Win32 + macOS filesystems. CAD groups often use
    `:` (NTFS alt-data-stream sigil) and `/` (path separator). Replace
    every problematic char with `_`. The contract id keeps the original
    name; only the on-disk path is sanitized."""
    bad = ':/\\?*"<>|'
    out = name
    for c in bad:
        out = out.replace(c, "_")
    return out


def render_three_views(cam, target_objs, out_dir, name):
    """Front / iso / top renders for one part (or part-group)."""
    if not target_objs: return {}
    safe = safe_filename(name)
    lo, hi = union_bbox(target_objs)
    center = (lo + hi) / 2
    diag = (hi - lo).length
    dist = max(diag * 1.6, 0.12)

    views = {
        "front": Vector((0,        -dist,     0)),
        "top":   Vector((0,        -0.001,    dist)),
        "iso":   Vector((dist*0.7, -dist*0.7, dist*0.55)),
    }
    paths = {}
    bpy.context.scene.render.image_settings.file_format = "PNG"
    for view, offset in views.items():
        cam.location = center + offset
        d = (center - cam.location).normalized()
        cam.rotation_mode = "XYZ"
        cam.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
        path = os.path.join(out_dir, f"{safe}.{view}.png")
        bpy.context.scene.render.filepath = path
        bpy.ops.render.render(write_still=True)
        paths[view] = path
    return paths


def configure_render():
    s = bpy.context.scene
    s.render.engine = "BLENDER_EEVEE"
    s.render.resolution_x = 720
    s.render.resolution_y = 540
    s.render.resolution_percentage = 100
    if s.world is None:
        s.world = bpy.data.worlds.new("World")
    s.world.use_nodes = True
    bg = s.world.node_tree.nodes.get("Background")
    if bg is not None:
        bg.inputs["Color"].default_value = (0.04, 0.045, 0.06, 1.0)
        bg.inputs["Strength"].default_value = 0.7


def ensure_studio_lights():
    """Three-point studio lighting so every render reads, regardless of
    where the CAD lands in world space."""
    presets = [
        ("Key",  (1.0, -1.0, 1.0), 800),
        ("Fill", (-1.0, -0.5, 0.5), 300),
        ("Rim",  (0.0, 1.0, 0.8),  500),
    ]
    for name, loc, energy in presets:
        light = bpy.data.objects.get(name)
        if light is None:
            d = bpy.data.lights.new(name, "AREA"); d.energy = energy
            light = bpy.data.objects.new(name, d)
            bpy.context.scene.collection.objects.link(light)
        light.location = loc


def main():
    try:
        argv = sys.argv[sys.argv.index("--") + 1:]
    except ValueError:
        argv = []
    if len(argv) < 2:
        raise SystemExit("usage: blender --background --factory-startup --python "
                         "segment_and_analyze.py -- <input.obj|.blend|.glb> <assetId>")
    in_path, asset_id = argv[0], argv[1]

    out_root = os.path.join(r"C:\SPATAIL_MAX\assets_processed\segmented", asset_id)
    parts_dir = os.path.join(out_root, "parts")
    analysis_path = os.path.join(out_root, asset_id + ".parts.json")
    os.makedirs(parts_dir, exist_ok=True)

    reset_scene()
    ensure_studio_lights()
    configure_render()
    cam = ensure_intro_camera()
    import_input(in_path)

    # Step 1: try mesh.separate(LOOSE) — works for properly multi-mesh CAD.
    parts = separate_loose_parts()

    # Step 2: if we still have ≤1 mesh, fall back to bbox region partition.
    used_fallback = False
    if len(parts) <= 1 and parts:
        used_fallback = True
        obj = parts[0]
        lo, hi = union_bbox([obj])
        cx, cy, cz = (lo + hi) / 2
        # 7-region partition tuned for compact mechanical CAD: center +
        # 6 cardinal sectors. The agent's labelling step makes sense of
        # each region by looking at the renders.
        regions = [
            ("center",      lambda c: abs(c.x - cx) < (hi.x - lo.x)*0.15
                                    and abs(c.y - cy) < (hi.y - lo.y)*0.15
                                    and abs(c.z - cz) < (hi.z - lo.z)*0.15),
            ("top",         lambda c: c.z > cz + (hi.z - lo.z)*0.20),
            ("bottom",      lambda c: c.z < cz - (hi.z - lo.z)*0.20),
            ("left",        lambda c: c.x < cx - (hi.x - lo.x)*0.20),
            ("right",       lambda c: c.x > cx + (hi.x - lo.x)*0.20),
            ("front",       lambda c: c.y < cy - (hi.y - lo.y)*0.20),
            ("back",        lambda c: c.y > cy + (hi.y - lo.y)*0.20),
        ]
        parts = bbox_region_split(obj, regions)

    print(f"[segment] {len(parts)} parts "
          f"({'loose_separate' if not used_fallback else 'bbox_fallback'})")

    # Step 3 + 4: render each part + collect metrics.
    records = []
    for o in parts:
        # Hide everything else.
        prev = {x.name: (x.hide_viewport, x.hide_render) for x in parts}
        for x in parts:
            x.hide_viewport = True
            x.hide_render = True
        o.hide_viewport = False
        o.hide_render = False

        paths = render_three_views(cam, [o], parts_dir, o.name)

        lo, hi = union_bbox([o])
        size = hi - lo
        centroid = (lo + hi) / 2

        records.append({
            "id": o.name,
            "originalName": o.name,
            "bboxSizeMeters":  [round(size.x, 4), round(size.y, 4), round(size.z, 4)],
            "centroidWorld":   [round(centroid.x, 4), round(centroid.y, 4), round(centroid.z, 4)],
            "faceCount":   len(o.data.polygons),
            "vertexCount": len(o.data.vertices),
            "renders": paths,
            # Empty — the agent fills these from visual inspection of `renders`.
            "semantic": {
                "name": None, "role": None, "function": None,
                "connectedTo": [], "motionDOF": None, "agentNotes": None,
            },
        })
        # Restore visibility.
        for x in parts:
            x.hide_viewport, x.hide_render = prev[x.name]

    analysis = {
        "assetId": asset_id,
        "source": in_path,
        "segmentationStrategy": "bbox_fallback" if used_fallback else "loose_separate",
        "schemaVersion": "0.1.0-parts-analysis",
        "partCount": len(records),
        "parts": records,
        "agentLoop":
            "Each part has front/iso/top renders. The next pass is the "
            "agent reading those renders and filling the `semantic` block "
            "on each part. Subsequent animation passes consume only this "
            "JSON, not the raw mesh.",
    }
    with open(analysis_path, "w") as f:
        json.dump(analysis, f, indent=2)
    print(f"[segment] wrote {analysis_path}")
    print(f"[segment] {len(records)} parts under {parts_dir}")


if __name__ == "__main__":
    main()
