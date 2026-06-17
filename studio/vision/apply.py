"""apply.py — Pass B of the vision-guided pipeline. Runs INSIDE Blender headless.

    blender --background --python studio/vision/apply.py -- <spec.json>

spec: {result_path, assetId, subject, glb_in, out_dir, review, realSizeMeters?,
       pivot?, ar_decimate?}

This pass EXECUTES the reviewer's decision (visual_review_result.json). It does NOT
re-decide anything — the semantic call was made from the evidence pack.

  1. import the raw GLB clean, join,
  2. REORIENT onto SPATAIL's canonical pose (review.up -> +Z, review.front -> -Y),
  3. SCALE uniformly to the real-world longest dimension (review estimate /
     object_size realSizeMeters), preserving proportions,
  4. REPAIR (only the ops the reviewer asked for — conservative, each logged),
  5. seat by pivot (center_bottom) and EXPORT exports/normalized.glb + .usdz,
  6. VALIDATE (dims, seated on Z=0, centered, files non-empty) -> logs/validation_log.txt,
  7. render preview/verify.png — the asset on a 10 cm grid with up(+Z) + front(-Y)
     arrows, annotated with the baked facts, so the result is VISUALLY confirmable.

Exports reuse studio/library/bake_assets (export_yup GLB + RealityKit USDZ), so a
vision-applied asset is pipeline-identical to every other SPATAIL asset.
"""
import json
import math
import sys
import time
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "library"))
import vision_report as vr  # noqa: E402
import bake_assets as BA  # noqa: E402  (proven export_yup GLB + RealityKit USDZ)

FALLBACK_LONGEST_M = 0.2


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


def _activate(obj):
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def _apply_all(obj):
    _activate(obj)
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    bpy.context.view_layer.update()


def _bbox(obj):
    bpy.context.view_layer.update()
    pts = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    mn = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    mx = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return mn, mx


# ── the three transforms the review drives ──────────────────────────────────────

def _reorient(obj, up_axis, front_axis, log):
    """Rotate the geometry so up_axis -> +Z and front_axis -> -Y (canonical). Refuses a
    non-proper rotation (det != +1) so a degenerate review can never collapse the mesh
    into the export. Returns True if a rotation was applied."""
    R3 = vr.rotation_to_canonical(up_axis, front_axis)          # 3x3 row-major
    M = Matrix((R3[0] + [0.0], R3[1] + [0.0], R3[2] + [0.0], (0, 0, 0, 1)))
    det = M.to_3x3().determinant()
    _apply_all(obj)                                              # bake import transform first
    if abs(det - 1.0) > 0.01:
        log(f"reorient: SKIPPED — degenerate rotation (det={det:.3f}) for up={up_axis} "
            f"front={front_axis}; keeping imported pose")
        return False
    obj.matrix_world = M @ obj.matrix_world
    _apply_all(obj)
    log(f"reorient: up {up_axis}->+Z, front {front_axis}->-Y (det={det:.2f})")
    return True


def _scale_to(obj, target_longest_m, log):
    _activate(obj)
    bpy.context.view_layer.update()
    cur = obj.dimensions
    cur_max = max(cur.x, cur.y, cur.z) or 1.0
    s = max(target_longest_m, 1e-4) / cur_max
    obj.scale = (s, s, s)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    bpy.context.view_layer.update()
    log(f"scale: longest {cur_max:.4f} -> {target_longest_m:.4f} m (x{s:.4f})")


def _seat(obj, pivot, log):
    bpy.context.view_layer.update()
    mn, mx = _bbox(obj)
    cx, cy = (mn.x + mx.x) / 2, (mn.y + mx.y) / 2
    if (pivot or "center_bottom") == "center_bottom":
        obj.location -= Vector((cx, cy, mn.z))
    else:
        obj.location -= Vector((cx, cy, (mn.z + mx.z) / 2))
    _apply_all(obj)
    log(f"seat: pivot={pivot or 'center_bottom'}")


def _repair(obj, ops, log):
    if not ops:
        return []
    done = []
    _activate(obj)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    try:
        if "remove_loose" in ops:
            bpy.ops.mesh.delete_loose(); done.append("remove_loose")
        if "merge_by_distance" in ops:
            bpy.ops.mesh.remove_doubles(threshold=1e-4); done.append("merge_by_distance")
        if "fill_holes" in ops:
            bpy.ops.mesh.fill_holes(sides=6); done.append("fill_holes")
        if "recalc_normals_outside" in ops:
            bpy.ops.mesh.normals_make_consistent(inside=False); done.append("recalc_normals_outside")
        if "flip_normals" in ops:
            bpy.ops.mesh.flip_normals(); done.append("flip_normals")
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")
    log(f"repair: {done}")
    return done


# ── verify render ────────────────────────────────────────────────────────────

def _emit(name, rgba, strength=2.0):
    m = bpy.data.materials.new(name); m.use_nodes = True
    nt = m.node_tree; nt.nodes.clear()
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs[0].default_value = rgba; em.inputs[1].default_value = strength
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(em.outputs[0], out.inputs[0])
    return m


def _arrow(name, vec, length, rgba):
    """A colored shaft + cone pointing along `vec` (unit), from origin."""
    v = Vector(vec).normalized()
    bpy.ops.mesh.primitive_cylinder_add(radius=length * 0.02, depth=length * 0.8)
    shaft = bpy.context.active_object
    bpy.ops.mesh.primitive_cone_add(radius1=length * 0.05, depth=length * 0.2,
                                    location=(0, 0, length * 0.5))
    tip = bpy.context.active_object
    _activate(shaft); tip.select_set(True)
    bpy.context.view_layer.objects.active = shaft
    bpy.ops.object.join()
    arrow = bpy.context.view_layer.objects.active
    arrow.name = name
    # +Z (cylinder default) -> v
    arrow.rotation_mode = "QUATERNION"
    arrow.rotation_quaternion = Vector((0, 0, 1)).rotation_difference(v)
    arrow.location = v * (length * 0.5)
    arrow.data.materials.append(_emit(name, rgba))
    return arrow


def _grid(cell, n):
    total = cell * n
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=n, y_subdivisions=n, size=total)
    g = bpy.context.active_object; g.name = "verify_grid"
    mat = bpy.data.materials.new("verify_grid"); mat.use_nodes = True
    b = mat.node_tree.nodes.get("Principled BSDF")
    if b:
        b.inputs["Base Color"].default_value = (0.28, 0.28, 0.32, 1)
        if "Roughness" in b.inputs:
            b.inputs["Roughness"].default_value = 1.0
    g.data.materials.append(mat)
    wf = g.modifiers.new("wire", "WIREFRAME"); wf.thickness = max(total * 0.003, cell * 0.02)
    return g


def _verify_render(obj, out_path, res, facts, log):
    scn = bpy.context.scene
    for eng in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "BLENDER_WORKBENCH"):
        try:
            scn.render.engine = eng; break
        except TypeError:
            continue
    scn.render.resolution_x = scn.render.resolution_y = res
    scn.render.film_transparent = False
    scn.render.image_settings.file_format = "PNG"
    try:
        scn.view_settings.view_transform = "Standard"
    except Exception:
        pass
    world = bpy.data.worlds.new("verify_world"); world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs[0].default_value = (0.6, 0.62, 0.66, 1); bg.inputs[1].default_value = 1.0
    scn.world = world
    sun = bpy.data.lights.new("verify_sun", type="SUN"); sun.energy = 3.0
    so = bpy.data.objects.new("verify_sun", sun); bpy.context.collection.objects.link(so)
    so.rotation_euler = (math.radians(55), math.radians(10), math.radians(40))

    mn, mx = _bbox(obj)
    longest = max((mx[i] - mn[i]) for i in range(3)) or 0.1
    cell = _nice_cell(longest)
    ncell = max(8, int(math.ceil(longest * 3 / cell)))
    _grid(cell, ncell)
    alen = longest * 0.9
    _arrow("verify_up", (0, 0, 1), alen, (0.1, 0.95, 0.2, 1))      # +Z up = green
    _arrow("verify_front", (0, -1, 0), alen, (0.1, 0.8, 1.0, 1))   # -Y front = cyan

    center = (mn + mx) / 2
    diag = (mx - mn).length
    cam_d = bpy.data.cameras.new("verify_cam"); cam_d.lens = 50
    cam_d.clip_start = 0.0001; cam_d.clip_end = 1000
    cam = bpy.data.objects.new("verify_cam", cam_d)
    bpy.context.collection.objects.link(cam)
    cam.location = center + Vector((diag * 1.4, -diag * 1.7, diag * 1.2))
    cam.rotation_euler = (center - cam.location).to_track_quat("-Z", "Y").to_euler()
    scn.camera = cam
    scn.render.filepath = str(out_path)
    bpy.ops.render.render(write_still=True)

    _annotate(out_path, facts, cell)
    log(f"verify render: {out_path} (grid cell = {cell*100:.0f} cm)")
    return out_path


def _nice_cell(longest):
    for c in (0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0):
        if longest <= c * 12:
            return c
    return 5.0


def _annotate(png, facts, cell):
    try:
        from PIL import Image, ImageDraw, ImageFont
        im = Image.open(png).convert("RGB")
        d = ImageDraw.Draw(im)
        try:
            f = ImageFont.truetype("arialbd.ttf", 20)
            fs = ImageFont.truetype("arial.ttf", 16)
        except Exception:
            f = fs = ImageFont.load_default()
        d.rectangle([0, 0, im.width, 70], fill=(0, 0, 0))
        d.text((10, 6), facts["title"], fill=(255, 220, 120), font=f)
        d.text((10, 34), facts["sub"], fill=(220, 220, 228), font=fs)
        d.text((10, im.height - 24), f"grid cell = {cell*100:.0f} cm   "
               f"green=up(+Z)  cyan=front(-Y)", fill=(230, 230, 235), font=fs)
        im.save(png)
    except Exception as e:  # noqa: BLE001
        print(f"[apply] annotate skipped: {e}")


# ── main ─────────────────────────────────────────────────────────────────────

def run(spec):
    asset_id = spec["assetId"]
    subject = spec.get("subject") or asset_id.replace("_", " ")
    out_dir = Path(spec["out_dir"])
    exports = out_dir / "exports"; reports = out_dir / "reports"
    preview = out_dir / "preview"; logs = out_dir / "logs"
    for dd in (exports, reports, preview, logs):
        dd.mkdir(parents=True, exist_ok=True)
    res = int(spec.get("render_res", 768))
    pivot = spec.get("pivot", "center_bottom")

    review = vr.validate_review(spec.get("review") or {}, reviewer=spec.get("reviewer", "external"))
    log_lines = []

    def log(m):
        print(f"[apply] {m}", flush=True)
        log_lines.append(m)

    # decide the real-world longest dimension (preserve proportions)
    real = spec.get("realSizeMeters")
    est = review["scale"]["estimated_longest_dim_m"]
    if real and isinstance(real, (list, tuple)) and max(real) > 0:
        target = max(float(x) for x in real)
        src = "object_size.realSizeMeters"
        if est > 0 and (target / est > 2.2 or est / target > 2.2):
            log(f"WARNING scale disagreement: object_size={target:.3f} vs review={est:.3f} m; "
                f"using object_size")
    elif est > 0:
        target = est; src = "review.estimate"
    else:
        target = FALLBACK_LONGEST_M; src = "fallback"
    log(f"target longest = {target:.4f} m (source: {src})")

    t0 = time.perf_counter()
    _clear()
    objs = _import_glb(Path(spec["glb_in"]))
    meshes = [o for o in objs if o.type == "MESH"]
    if not meshes:
        raise RuntimeError("no mesh in GLB")
    obj = _join(meshes)
    for o in list(bpy.data.objects):
        if o is not obj and o.type in ("EMPTY", "LIGHT", "CAMERA"):
            bpy.data.objects.remove(o, do_unlink=True)
    if not obj.data.polygons:
        raise RuntimeError("degenerate/empty mesh after import+join")

    o = review["orientation"]
    rotated = False
    if o["needs_rotation"] or o["up_axis"] != vr.CANONICAL_UP or o["front_axis"] != vr.CANONICAL_FRONT:
        rotated = _reorient(obj, o["up_axis"], o["front_axis"], log)
    else:
        _apply_all(obj)
        log("reorient: none (already canonical)")

    repaired = _repair(obj, review["quality"]["repair"], log)
    _scale_to(obj, target, log)
    _seat(obj, pivot, log)

    # export (clean scene = just the asset) BEFORE adding verify props. The bake_assets
    # exporters serialize the whole scene, so guarantee nothing but the asset is in it.
    stray = [s for s in bpy.context.scene.objects if s is not obj and s.type == "MESH"]
    if stray:
        log(f"export: dropping {len(stray)} stray mesh object(s) so only the asset exports")
        for s in stray:
            bpy.data.objects.remove(s, do_unlink=True)
    _activate(obj)
    glb = exports / "normalized.glb"
    usdz = exports / "normalized.usdz"
    ok_glb = BA._export_glb(glb)
    ok_usdz = BA._export_usdz(usdz)
    if not ok_glb:
        raise RuntimeError("normalized GLB export failed")
    log(f"export: {glb.name}{'+usdz' if ok_usdz else ''}")

    # validate
    mn, mx = _bbox(obj)
    final_size = [round(mx[i] - mn[i], 6) for i in range(3)]
    final_longest = max(final_size)
    checks, errors = [], []

    def chk(name, cond, detail=""):
        checks.append({"check": name, "ok": bool(cond), "detail": detail})
        if not cond:
            errors.append(f"{name}: {detail}")

    chk("glb_nonempty", glb.exists() and glb.stat().st_size > 100, f"{glb.stat().st_size if glb.exists() else 0} bytes")
    chk("longest_matches_target", abs(final_longest - target) <= max(0.02, target * 0.05),
        f"final {final_longest:.4f} vs target {target:.4f}")
    chk("seated_on_z0", abs(mn.z) <= max(0.005, final_longest * 0.02), f"min_z={mn.z:.4f}")
    chk("centered_xy", abs((mn.x + mx.x) / 2) <= 0.01 and abs((mn.y + mx.y) / 2) <= 0.01,
        f"cx={(mn.x+mx.x)/2:.4f} cy={(mn.y+mx.y)/2:.4f}")
    # a real reviewer must have run (not the neutral fallback) and scale must be grounded
    rev_name = review.get("reviewer", "")
    chk("reviewer_ran", rev_name not in ("", "none") and review["orientation"]["confidence"] > 0.0,
        f"reviewer={rev_name!r} orient_conf={review['orientation']['confidence']}")
    chk("scale_grounded", src != "fallback", f"scale_source={src}")
    val_ok = not errors
    # uniform scale anchors ONE axis; flag when the mesh's longest axis isn't the real one
    if real and isinstance(real, (list, tuple)) and max(real) > 0:
        if max(range(3), key=lambda i: final_size[i]) != max(range(3), key=lambda i: real[i]):
            log(f"note: mesh longest axis != real longest axis {list(real)}; "
                f"uniform-scaled to the longest dimension only")
    (logs / "validation_log.txt").write_text(
        "\n".join(f"[{'OK' if c['ok'] else 'FAIL'}] {c['check']}: {c['detail']}" for c in checks),
        encoding="utf-8")
    log(f"validation: {'OK' if val_ok else 'FAILED ' + '; '.join(errors)}")

    # verify render
    facts = {
        "title": f'{asset_id}  —  "{subject}"  ·  {review["scale"]["placement_class"]}',
        "sub": f'{final_size} m  ·  up={o["up_axis"]} front={o["front_axis"]}'
               f'  ·  {"rotated" if rotated else "no-rotate"}'
               f'  ·  repair={repaired or "none"}  ·  scale:{src}'
               f'  ·  {"VALID" if val_ok else "CHECK FAILED"}',
    }
    verify = _verify_render(obj, preview / "verify.png", res, facts, log)

    timings = {"total": round(time.perf_counter() - t0, 3)}
    apply_report = {
        "schema": "spatail-asset-applied/1",
        "asset_id": asset_id, "subject": subject,
        "review_consumed": review,
        "applied": {
            "reorient": {"up_axis": o["up_axis"], "front_axis": o["front_axis"],
                         "rotated": bool(rotated)},
            "target_longest_m": round(target, 6), "scale_source": src,
            "repair": repaired, "pivot": pivot,
            "final_size_m": final_size, "final_longest_m": round(final_longest, 6),
            "placement_class": review["scale"]["placement_class"],
            "real_size_m": real,
        },
        "exports": {"glb": glb.name, "usdz": usdz.name if ok_usdz else ""},
        "validation": {"ok": val_ok, "checks": checks, "errors": errors},
        "verify_render": str(verify),
        "timings_seconds": timings,
    }
    (reports / "apply_report.json").write_text(json.dumps(apply_report, indent=2), encoding="utf-8")
    (logs / "apply_log.txt").write_text("\n".join(log_lines), encoding="utf-8")
    return {
        "ok": val_ok, "assetId": asset_id, "out_dir": str(out_dir),
        "glb": str(glb), "usdz": str(usdz) if ok_usdz else "",
        "verify_render": str(verify), "final_size_m": final_size,
        "placement_class": review["scale"]["placement_class"],
        "validation_ok": val_ok, "errors": errors, "applied": apply_report["applied"],
    }


def main():
    spec = json.loads(Path(sys.argv[sys.argv.index("--") + 1]).read_text(encoding="utf-8"))
    result_path = Path(spec["result_path"])
    try:
        res = run(spec)
        result_path.write_text(json.dumps(res, indent=2), encoding="utf-8")
        print(f"[apply] {'OK' if res['ok'] else 'VALIDATION FAILED'} -> {result_path}")
    except Exception as e:  # noqa: BLE001
        import traceback
        tb = traceback.format_exc()
        result_path.write_text(json.dumps(
            {"ok": False, "assetId": spec.get("assetId"), "error": str(e),
             "trace_tail": tb[-1500:]}, indent=2), encoding="utf-8")
        print(f"[apply] FAILED: {e}\n{tb}")


main()
