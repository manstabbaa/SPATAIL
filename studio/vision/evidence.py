"""evidence.py — Pass A of the vision-guided pipeline. Runs INSIDE Blender headless.

    blender --background --python studio/vision/evidence.py -- <spec.json>

spec: {result_path, assetId, subject, glb_in, out_dir, views?, render_res?}

This pass produces VISUAL + NUMERIC evidence and makes NO semantic decision. It:

  1. imports the (Meshy) GLB, joins it, measures the SOURCE bbox in metres,
  2. seats it on Z=0 and frames it to a uniform render size (orientation untouched),
  3. renders the evidence pack into <out_dir>/preview/:
       front back left right top bottom    (orthographic axis views, clean silhouettes)
       iso_01 iso_02                        (3/4 perspective, with axis triad + ground grid)
       silhouette                            (flat shape read)
       material_preview                      (full textures/materials, studio light)
       normal_preview                        (world-space normals as color — reveals bad normals)
       contact_sheet.png                     (labeled grid + measurement panel, the reviewer input)
  4. writes <out_dir>/reports/asset_report.json (numbers) + visual_review_prompt.md,
  5. copies the source GLB into <out_dir>/imported/.

The reviewer (Claude skill/subagent, or review_gemini.py) reads the contact sheet +
report and decides up/front/scale/class/repair. apply.py then executes that decision.
"""
import json
import math
import shutil
import sys
import time
from pathlib import Path

import bpy
import bmesh
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vision_report as vr  # noqa: E402  (bpy-free contract)

RES_DEFAULT = 768
_MANIFOLD_VERT_CAP = 600_000

# Ortho tiles, named by the FACE they show: the camera is PLACED on the +axis side
# and looks back at the origin, so e.g. "front" (axis -Y) shows the -Y face.
ORTHO_VIEWS = [
    ("front", (0, -1, 0)), ("back", (0, 1, 0)),
    ("right", (1, 0, 0)), ("left", (-1, 0, 0)),
    ("top", (0, 0, 1)), ("bottom", (0, 0, -1)),
]


# ── scene helpers ────────────────────────────────────────────────────────────

def _clear():
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for blk in (bpy.data.meshes, bpy.data.materials, bpy.data.images,
                bpy.data.cameras, bpy.data.lights):
        for b in list(blk):
            if b.users == 0:
                try:
                    blk.remove(b)
                except Exception:
                    pass


def _import_glb(path):
    before = set(bpy.data.objects)
    try:
        bpy.ops.preferences.addon_enable(module="io_scene_gltf2")
    except Exception:
        pass
    bpy.ops.import_scene.gltf(filepath=str(path))
    return [o for o in bpy.data.objects if o not in before]


def _join(meshes):
    bpy.ops.object.select_all(action="DESELECT")
    for o in meshes:
        o.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    if len(meshes) > 1:
        bpy.ops.object.join()
    return bpy.context.view_layer.objects.active


def _bbox_world(obj):
    bpy.context.view_layer.update()
    pts = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    mn = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    mx = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return mn, mx


def _bbox_dict(mn, mx):
    size = [round(mx[i] - mn[i], 6) for i in range(3)]
    return {
        "min": [round(v, 6) for v in mn], "max": [round(v, 6) for v in mx],
        "size": size, "center": [round((mn[i] + mx[i]) / 2, 6) for i in range(3)],
        "largest_dim": round(max(size), 6),
        "aspect": _aspect(size),
    }


def _aspect(size):
    m = max(size) or 1.0
    return [round(s / m, 3) for s in size]      # 1.0 on the longest axis


# ── geometry + health (measured, never interpreted) ─────────────────────────────

def _geometry(obj):
    me = obj.data
    me.calc_loop_triangles()
    mats = [s.material for s in obj.material_slots if s.material]
    imgs = set()
    for m in mats:
        if m.use_nodes and m.node_tree:
            for n in m.node_tree.nodes:
                if n.type == "TEX_IMAGE" and n.image:
                    imgs.add(n.image)
    return {
        "triangle_count": len(me.loop_triangles),
        "vertex_count": len(me.vertices),
        "mesh_count": 1,
        "material_count": len(mats),
        "texture_count": len(imgs),
        "has_uvs": bool(me.uv_layers),
        "has_materials": bool(mats),
        "has_textures": bool(imgs),
    }


def _split_normal_ratio(me):
    """Share of interior edges (two adjacent faces) whose corner normals disagree
    across the edge — the unwelded faceted-seam signature a weld+smooth repair
    removes. None = could not be measured."""
    try:
        loops = me.loops
        try:
            corner = me.corner_normals            # Blender 4.1+ / 5.x

            def _n(li):
                return corner[li].vector
        except AttributeError:                    # legacy loop normals
            try:
                me.calc_normals_split()
            except Exception:
                pass

            def _n(li):
                return loops[li].normal
        edge_polys = {}
        vert_loop = {}
        for poly in me.polygons:
            for li in poly.loop_indices:
                lp = loops[li]
                edge_polys.setdefault(lp.edge_index, []).append(poly.index)
                vert_loop[(poly.index, lp.vertex_index)] = li
        interior, split = 0, 0
        cos_eps = 0.99996                          # ~0.5 deg
        for ei, polys in edge_polys.items():
            if len(polys) != 2:
                continue
            interior += 1
            pa, pb = polys
            for v in me.edges[ei].vertices:
                la = vert_loop.get((pa, v))
                lb = vert_loop.get((pb, v))
                if la is None or lb is None:
                    continue
                na, nb = _n(la), _n(lb)
                if na.length > 1e-6 and nb.length > 1e-6 and \
                        na.normalized().dot(nb.normalized()) < cos_eps:
                    split += 1
                    break
        return round(split / interior, 4) if interior else 0.0
    except Exception:
        return None


def _health(obj, bbox):
    me = obj.data
    verts = len(me.vertices)
    loose = None                                     # None = not scanned (very heavy mesh)
    nm = None
    snr = None
    normals_valid = True
    if verts <= _MANIFOLD_VERT_CAP:
        bm = bmesh.new()
        try:
            bm.from_mesh(me)
            loose = any(len(v.link_faces) == 0 for v in bm.verts) or \
                any(len(e.link_faces) == 0 for e in bm.edges)
            nm = any(not e.is_manifold for e in bm.edges)
        except Exception:
            pass
        finally:
            bm.free()
        snr = _split_normal_ratio(me)
    if me.polygons:
        n = me.polygons[0].normal
        normals_valid = n.length > 1e-6
    else:
        normals_valid = False
    size = bbox["size"]
    return {
        "loose_geometry": loose,
        "non_manifold": nm,
        "normals_valid": normals_valid,
        # ->1 = unwelded split normals everywhere (faceted seams if repairs skipped)
        "split_normal_ratio": snr,
        "flatness": round((min(size) / (max(size) or 1.0)), 4),   # ->0 = a flat/thin object
        "manifold_scanned": verts <= _MANIFOLD_VERT_CAP,
    }


# ── framing: seat on Z=0, uniform-scale to a fixed render size (NO reorient) ─────

def _frame(obj, target=2.0):
    """Center X/Y, seat the bottom on Z=0, uniform-scale so the largest dim = target.
    This only frames the renders consistently — it preserves the asset's axes, so the
    reviewer's up/front choice (referencing the rendered axes) maps 1:1 to apply.py."""
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.transform_apply(location=True, rotation=False, scale=True)
    mn, mx = _bbox_world(obj)
    largest = max((mx[i] - mn[i]) for i in range(3)) or 1.0
    s = target / largest
    obj.scale = (s, s, s)
    bpy.context.view_layer.update()
    mn, mx = _bbox_world(obj)
    cx, cy = (mn.x + mx.x) / 2, (mn.y + mx.y) / 2
    obj.location -= Vector((cx, cy, mn.z))            # center X/Y, bottom on Z=0
    bpy.context.view_layer.update()
    return s


# ── render rig ───────────────────────────────────────────────────────────────

def _setup_render(res):
    scn = bpy.context.scene
    for eng in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "BLENDER_WORKBENCH"):
        try:
            scn.render.engine = eng
            break
        except TypeError:
            continue
    scn.render.resolution_x = scn.render.resolution_y = res
    scn.render.resolution_percentage = 100
    scn.render.film_transparent = False
    scn.render.image_settings.file_format = "PNG"
    scn.render.image_settings.color_mode = "RGB"
    try:
        scn.view_settings.view_transform = "Standard"
    except Exception:
        pass
    try:
        scn.eevee.taa_render_samples = 24
    except Exception:
        pass
    world = bpy.data.worlds.new("vis_world")
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs[0].default_value = (0.62, 0.62, 0.65, 1.0)
        bg.inputs[1].default_value = 1.0
    scn.world = world
    key = bpy.data.lights.new("vis_key", type="SUN"); key.energy = 3.0
    ko = bpy.data.objects.new("vis_key", key)
    bpy.context.collection.objects.link(ko)
    ko.rotation_euler = (math.radians(55), math.radians(8), math.radians(40))
    fill = bpy.data.lights.new("vis_fill", type="SUN"); fill.energy = 1.1
    fo = bpy.data.objects.new("vis_fill", fill)
    bpy.context.collection.objects.link(fo)
    fo.rotation_euler = (math.radians(60), 0.0, math.radians(-120))


def _ortho_cam(name, location, look_at, scale, up="Y"):
    cd = bpy.data.cameras.new(name); cd.type = "ORTHO"
    cd.ortho_scale = scale; cd.clip_start = 0.001; cd.clip_end = scale * 50 + 100
    cam = bpy.data.objects.new(name, cd)
    bpy.context.collection.objects.link(cam)
    cam.location = location
    direction = Vector(look_at) - Vector(location)
    cam.rotation_euler = direction.to_track_quat("-Z", up).to_euler()
    return cam


def _persp_cam(name, location, look_at):
    cd = bpy.data.cameras.new(name); cd.type = "PERSP"; cd.lens = 50
    cd.clip_start = 0.001; cd.clip_end = 1000.0
    cam = bpy.data.objects.new(name, cd)
    bpy.context.collection.objects.link(cam)
    cam.location = location
    direction = Vector(look_at) - Vector(location)
    cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    return cam


def _render(cam, path, hide=()):
    scn = bpy.context.scene
    scn.camera = cam
    for o in hide:
        o.hide_render = True
    scn.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    for o in hide:
        o.hide_render = False
    return Path(path)


# ── orientation aids (axis triad + ground grid) — iso views only ────────────────

def _emit_mat(name, rgba, strength=1.0):
    m = bpy.data.materials.new(name); m.use_nodes = True
    nt = m.node_tree; nt.nodes.clear()
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs[0].default_value = rgba; em.inputs[1].default_value = strength
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(em.outputs[0], out.inputs[0])
    return m


def _axis_triad(L):
    """Three thin colored bars from origin: X red, Y green, Z blue, length ~L."""
    aids = []
    for axis, rgba, rot in (("X", (1, 0.05, 0.05, 1), (0, math.radians(90), 0)),
                            ("Y", (0.05, 0.9, 0.1, 1), (math.radians(-90), 0, 0)),
                            ("Z", (0.1, 0.3, 1.0, 1), (0, 0, 0))):
        bpy.ops.mesh.primitive_cylinder_add(radius=L * 0.012, depth=L)
        bar = bpy.context.active_object
        bar.name = f"vis_axis_{axis}"
        bar.rotation_euler = rot
        bpy_apply = bar
        bpy.context.view_layer.update()
        # shift so the bar runs from origin outward along +axis
        d = {"X": Vector((L / 2, 0, 0)), "Y": Vector((0, L / 2, 0)),
             "Z": Vector((0, 0, L / 2))}[axis]
        bar.location = d
        bar.data.materials.append(_emit_mat(f"vis_axis_{axis}", rgba, 1.6))
        aids.append(bar)
    return aids


def _ground_grid(extent):
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=12, y_subdivisions=12,
                                    size=extent * 2.4)
    g = bpy.context.active_object
    g.name = "vis_grid"
    g.location = (0, 0, 0)
    mat = bpy.data.materials.new("vis_grid"); mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (0.30, 0.30, 0.33, 1)
        if "Roughness" in bsdf.inputs:
            bsdf.inputs["Roughness"].default_value = 1.0
    g.data.materials.append(mat)
    # wireframe overlay so grid lines read in the render
    wf = g.modifiers.new("wire", "WIREFRAME")
    wf.thickness = extent * 0.006
    return g


# ── material overrides for the silhouette + normal passes ───────────────────────

def _save_materials(obj):
    return [s.material for s in obj.material_slots]


def _override_material(obj, mat):
    obj.data.materials.clear()
    obj.data.materials.append(mat)


def _restore_materials(obj, mats):
    obj.data.materials.clear()
    for m in mats:
        obj.data.materials.append(m)


def _normal_material():
    """Color = world-space normal (Geometry.Normal -> Emission). Inverted/garbage
    normals show up as wrong/sparkly colors — a fast visual integrity read."""
    m = bpy.data.materials.new("vis_normal"); m.use_nodes = True
    nt = m.node_tree; nt.nodes.clear()
    geo = nt.nodes.new("ShaderNodeNewGeometry")
    # remap normal [-1,1] -> [0,1] so it's a visible color
    mr = nt.nodes.new("ShaderNodeVectorMath"); mr.operation = "MULTIPLY_ADD"
    mr.inputs[1].default_value = (0.5, 0.5, 0.5)
    mr.inputs[2].default_value = (0.5, 0.5, 0.5)
    em = nt.nodes.new("ShaderNodeEmission")
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(geo.outputs["Normal"], mr.inputs[0])
    nt.links.new(mr.outputs[0], em.inputs[0])
    nt.links.new(em.outputs[0], out.inputs[0])
    return m


# ── contact sheet (PIL — bundled with Blender's python) ─────────────────────────

def _contact_sheet(view_paths, report, out_path, subject):
    from PIL import Image, ImageDraw, ImageFont
    order = ["front", "back", "left", "right", "top", "bottom",
             "iso_01", "iso_02", "silhouette", "material_preview", "normal_preview"]
    tile = 320
    cols = 4
    rows = math.ceil(len(order) / cols)
    pad = 10
    label_h = 22
    panel_h = 150
    W = cols * tile + (cols + 1) * pad
    H = rows * (tile + label_h) + (rows + 1) * pad + panel_h
    sheet = Image.new("RGB", (W, H), (24, 24, 28))
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("arial.ttf", 15)
        big = ImageFont.truetype("arialbd.ttf", 19)
    except Exception:
        font = ImageFont.load_default()
        big = font
    for i, name in enumerate(order):
        r, c = divmod(i, cols)
        x = pad + c * (tile + pad)
        y = pad + r * (tile + label_h + pad)
        p = view_paths.get(name)
        if p and Path(p).exists():
            im = Image.open(p).convert("RGB").resize((tile, tile))
            sheet.paste(im, (x, y))
        else:
            draw.rectangle([x, y, x + tile, y + tile], fill=(40, 40, 46))
        draw.rectangle([x, y + tile, x + tile, y + tile + label_h], fill=(12, 12, 14))
        draw.text((x + 6, y + tile + 3), name, fill=(235, 235, 240), font=font)
    # measurement panel
    py = H - panel_h + 6
    b = report["source_bbox_m"]; g = report["geometry"]; h = report["health"]
    loose_disp = "unknown" if h.get("loose_geometry") is None else h["loose_geometry"]
    nm_disp = "unknown" if h.get("non_manifold") is None else h["non_manifold"]
    snr_disp = "unknown" if h.get("split_normal_ratio") is None else h["split_normal_ratio"]
    draw.text((pad, py), f'EVIDENCE  —  {report["asset_id"]}   "{subject}"',
              fill=(255, 220, 120), font=big)
    lines = [
        f'source bbox (m): {b["size"]}   aspect {b["aspect"]}   largest {b["largest_dim"]}',
        f'tris {g["triangle_count"]}   verts {g["vertex_count"]}   mats {g["material_count"]}'
        f'   textured {g["has_textures"]}   uvs {g["has_uvs"]}',
        f'normals_valid {h["normals_valid"]}   loose {loose_disp}'
        f'   non_manifold {nm_disp}   split_normals {snr_disp}   flatness {h["flatness"]}',
        'axes: +X red  +Y green  +Z blue (up).  Decide TRUE up/front, real scale, class, repair.',
    ]
    for j, ln in enumerate(lines):
        draw.text((pad, py + 30 + j * 26), ln, fill=(215, 215, 222), font=font)
    sheet.save(out_path)
    return out_path


# ── main pass ────────────────────────────────────────────────────────────────

def run(spec):
    asset_id = spec["assetId"]
    subject = spec.get("subject") or asset_id.replace("_", " ")
    out_dir = Path(spec["out_dir"])
    res = int(spec.get("render_res", RES_DEFAULT))
    preview = out_dir / "preview"; reports = out_dir / "reports"
    imported = out_dir / "imported"; logs = out_dir / "logs"
    for d in (preview, reports, imported, logs):
        d.mkdir(parents=True, exist_ok=True)

    timings = {}
    t0 = time.perf_counter()
    log_lines = []

    def log(m):
        print(f"[evidence] {m}", flush=True)
        log_lines.append(m)

    _clear()
    glb_in = Path(spec["glb_in"])
    objs = _import_glb(glb_in)
    meshes = [o for o in objs if o.type == "MESH"]
    if not meshes:
        raise RuntimeError("no mesh in GLB")
    obj = _join(meshes)
    # drop import leftovers (empties/lights/cameras) so the scene is just the asset
    for o in list(bpy.data.objects):
        if o is not obj and o.type in ("EMPTY", "LIGHT", "CAMERA"):
            bpy.data.objects.remove(o, do_unlink=True)
    timings["import"] = time.perf_counter() - t0

    mn, mx = _bbox_world(obj)
    source_bbox = _bbox_dict(mn, mx)
    if not obj.data.polygons or source_bbox["largest_dim"] < 1e-6:
        raise RuntimeError(f"degenerate/empty mesh after import+join "
                           f"(polys={len(obj.data.polygons)}, bbox={source_bbox['size']})")
    geometry = _geometry(obj)
    health = _health(obj, source_bbox)
    log(f"{asset_id}: source bbox {source_bbox['size']} m, {geometry['triangle_count']} tris, "
        f"normals_valid={health['normals_valid']}, "
        f"split_normal_ratio={health['split_normal_ratio']}")

    frame_scale = _frame(obj, target=2.0)
    mn, mx = _bbox_world(obj)
    center = (mn + mx) / 2
    extent = max((mx[i] - mn[i]) for i in range(3)) or 1.0
    diag = (mx - mn).length

    _setup_render(res)
    grid = _ground_grid(extent)
    triad = _axis_triad(extent * 0.7)
    aids = [grid] + triad                                 # hidden in ortho views

    # cameras
    d = diag * 1.1
    oc = extent * 1.5                                     # ortho framing
    cams = {}
    for name, axis in ORTHO_VIEWS:
        loc = center + Vector(axis) * (d + 1.0)
        # every ortho view keeps +Y "north" (top/bottom included) so the tiles form a
        # consistent mirror set; the camera tracker resolves roll for the straight-down views.
        cams[name] = _ortho_cam(f"vis_{name}", loc, center, oc, up="Y")
    cams["iso_01"] = _persp_cam("vis_iso_01",
                                center + Vector((d, -d, d * 0.7)), center)
    cams["iso_02"] = _persp_cam("vis_iso_02",
                                center + Vector((-d, -d * 0.8, d * 0.45)), center)

    view_paths = {}
    t = time.perf_counter()
    # 6 ortho axis views — clean asset (hide the orientation aids)
    for name, _ in ORTHO_VIEWS:
        view_paths[name] = str(_render(cams[name], preview / f"{name}.png", hide=aids))
    # iso views WITH axis triad + ground grid
    for name in ("iso_01", "iso_02"):
        view_paths[name] = str(_render(cams[name], preview / f"{name}.png"))
    timings["render_views"] = time.perf_counter() - t

    # silhouette: flat black emission on white world, from iso_01 (aids hidden)
    saved = _save_materials(obj)
    _override_material(obj, _emit_mat("vis_sil", (0.02, 0.02, 0.02, 1), 0.0))
    scn = bpy.context.scene
    sil_world = scn.world.node_tree.nodes.get("Background")
    old_bg = sil_world.inputs[0].default_value[:] if sil_world else None
    if sil_world:
        sil_world.inputs[0].default_value = (1, 1, 1, 1)
    view_paths["silhouette"] = str(_render(cams["iso_01"], preview / "silhouette.png", hide=aids))
    if sil_world and old_bg:
        sil_world.inputs[0].default_value = old_bg

    # normal_preview: world-normal as color, from iso_01 (aids hidden)
    _override_material(obj, _normal_material())
    view_paths["normal_preview"] = str(_render(cams["iso_01"], preview / "normal_preview.png", hide=aids))
    _restore_materials(obj, saved)

    # material_preview: a clean textured 3/4 (reuse iso_01 camera, aids hidden)
    view_paths["material_preview"] = str(_render(cams["iso_01"], preview / "material_preview.png", hide=aids))

    # relative paths for the report (portable)
    rel_views = {k: str(Path(v).relative_to(out_dir)).replace("\\", "/")
                 for k, v in view_paths.items()}

    report = vr.build_evidence_report(
        asset_id=asset_id, subject=subject, source=str(glb_in),
        frame={"mode": "seat_z0_uniform", "scale": round(frame_scale, 6), "target": 2.0},
        bbox=source_bbox, geometry=geometry, health=health, views=rel_views,
        contact_sheet="preview/contact_sheet.png", timings=timings)

    cs = _contact_sheet(view_paths, report, preview / "contact_sheet.png", subject)
    vr_path = reports / "asset_report.json"
    vr_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    (reports / "visual_review_prompt.md").write_text(
        vr.reviewer_prompt(asset_id, subject, report), encoding="utf-8")

    # stage the source for provenance
    try:
        shutil.copyfile(glb_in, imported / glb_in.name)
    except Exception as e:  # noqa: BLE001
        log(f"imported/ copy skipped: {e}")

    timings["total"] = time.perf_counter() - t0
    (logs / "blender_log.txt").write_text("\n".join(log_lines), encoding="utf-8")
    log(f"{asset_id}: evidence pack ready in {timings['total']:.1f}s -> {out_dir}")
    return {
        "assetId": asset_id, "out_dir": str(out_dir),
        "contact_sheet": str(cs), "report": str(vr_path),
        "prompt": str(reports / "visual_review_prompt.md"),
        "views": view_paths, "source_bbox_m": source_bbox,
    }


def main():
    spec = json.loads(Path(sys.argv[sys.argv.index("--") + 1]).read_text(encoding="utf-8"))
    result_path = Path(spec["result_path"])
    try:
        res = run(spec)
        res["ok"] = True
        result_path.write_text(json.dumps(res, indent=2), encoding="utf-8")
        print(f"[evidence] OK -> {result_path}")
    except Exception as e:  # noqa: BLE001
        import traceback
        tb = traceback.format_exc()
        result_path.write_text(json.dumps(
            {"ok": False, "assetId": spec.get("assetId"), "error": str(e),
             "trace_tail": tb[-1500:]}, indent=2), encoding="utf-8")
        print(f"[evidence] FAILED: {e}\n{tb}")


main()
