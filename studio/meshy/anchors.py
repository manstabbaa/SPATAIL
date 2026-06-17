"""anchors.py — run INSIDE Blender headless: Blender-computed semantic surface anchors.

    blender --background --python studio/meshy/anchors.py -- <spec.json>

spec: {result_path, assets:[{assetId, subject, desc, glb_in, out_dir,
                             views?, max_anchors?}]}

Phase 1 of docs/blender-spatial-intelligence-plan.md: the phone stops guessing where
"the frog's eye" is. Blender owns the real mesh, so it computes the anchors:

  1. render K canonical views of the normalized mesh (we own the cameras → exact rays)
  2. Gemini points at each semantic feature in each view (2D [y, x], 0-1000)
  3. back-project: ray from each camera through the 2D point onto the mesh (BVHTree),
     cluster the per-view hits → surface anchors
  4. QA: re-render with colored marker dots, Gemini verifies, one retry, drop failures

Anchors are reported in the asset's EXPORTED Y-up space (both exporters ship Y-up,
-Z forward): pos_m in metres, plus `offset` normalized to the bbox ((pos-min)/extent)
— exactly iOS ModularRuntime's step.anchorOffset convention (visual-bounds space).
"""
import base64
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import bpy
import bmesh
from mathutils import Vector
from mathutils.bvhtree import BVHTree

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402  (env var > ~/.spatail/secrets.env — works inside Blender)

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
MODEL = os.environ.get("SPATAIL_ANCHOR_MODEL", "gemini-2.5-flash")
RES = 768
VIEW_ORDER = ("front", "right", "back", "left", "top", "front_low")
MARKER_COLORS = [("red", (1.0, 0.02, 0.02, 1.0)), ("blue", (0.05, 0.15, 1.0, 1.0)),
                 ("green", (0.02, 0.85, 0.05, 1.0)), ("yellow", (1.0, 0.85, 0.02, 1.0)),
                 ("magenta", (1.0, 0.02, 1.0, 1.0)), ("cyan", (0.02, 0.85, 0.9, 1.0))]


# ── scene ─────────────────────────────────────────────────────────────────────

def _clear():
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for blk in (bpy.data.meshes, bpy.data.materials, bpy.data.images, bpy.data.lights,
                bpy.data.cameras):
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


def _setup_render():
    scn = bpy.context.scene
    for eng in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "BLENDER_WORKBENCH"):
        try:
            scn.render.engine = eng
            break
        except TypeError:
            continue
    scn.render.resolution_x = RES
    scn.render.resolution_y = RES
    scn.render.resolution_percentage = 100
    scn.render.film_transparent = False
    scn.render.image_settings.file_format = "PNG"
    scn.render.image_settings.color_mode = "RGB"
    try:  # true colors: keep textures + emissive markers saturated
        scn.view_settings.view_transform = "Standard"
    except Exception:
        pass
    try:
        scn.eevee.taa_render_samples = 32
    except Exception:
        pass
    world = bpy.data.worlds.new("SPATAIL_anchor_world")
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs[0].default_value = (1.0, 1.0, 1.0, 1.0)
        bg.inputs[1].default_value = 0.9
    scn.world = world
    sun_d = bpy.data.lights.new("SPATAIL_anchor_sun", type="SUN")
    sun_d.energy = 2.5
    sun = bpy.data.objects.new("SPATAIL_anchor_sun", sun_d)
    bpy.context.collection.objects.link(sun)
    sun.rotation_euler = (math.radians(50), 0.0, math.radians(35))


def _make_camera(name, location, look_at):
    cam_d = bpy.data.cameras.new(name)
    cam_d.type = "PERSP"
    cam_d.lens = 50
    cam_d.clip_start = 0.001
    cam_d.clip_end = 1000.0
    cam = bpy.data.objects.new(name, cam_d)
    bpy.context.collection.objects.link(cam)
    cam.location = location
    direction = Vector(look_at) - Vector(location)
    cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    return cam


def _rig_views(center, diag, n):
    """K perspective cameras circling the asset (front = -Y, matching the rig
    conventions of the parked multiview renderer), slight downward elevation."""
    dist = max(diag, 0.05) * 2.1
    elev = math.radians(22.0)
    views = []
    for i in range(min(n, 4)):                       # 4 yaws; extra views below
        yaw = math.radians(90.0 * i)
        loc = center + Vector((math.sin(yaw) * dist * math.cos(elev),
                               -math.cos(yaw) * dist * math.cos(elev),
                               dist * math.sin(elev)))
        views.append((VIEW_ORDER[i], _make_camera(f"SPATAIL_anchor_cam_{VIEW_ORDER[i]}",
                                                  loc, center)))
    if n >= 5:
        views.append(("top", _make_camera("SPATAIL_anchor_cam_top",
                                          center + Vector((0, -0.15 * dist, dist)), center)))
    if n >= 6:
        views.append(("front_low", _make_camera(
            "SPATAIL_anchor_cam_front_low",
            center + Vector((0, -dist, 0.05 * dist)), center)))
    return views


def _render(cam, path):
    scn = bpy.context.scene
    scn.camera = cam
    scn.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    return Path(path)


# ── geometry: bbox, rays, BVH ────────────────────────────────────────────────

def _bbox_world(obj):
    bpy.context.view_layer.update()
    pts = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    mn = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    mx = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return mn, mx


def _bvh_world(obj):
    """BVH over the evaluated mesh IN WORLD SPACE — ray origins/dirs stay in world,
    hit normals come back in world. (The bmesh must stay alive with the tree.)"""
    deps = bpy.context.evaluated_depsgraph_get()
    bm = bmesh.new()
    bm.from_object(obj, deps)
    bm.transform(obj.matrix_world)
    return BVHTree.FromBMesh(bm), bm


def _pixel_ray(cam, u, v):
    """World ray through normalized image coords (u right 0..1, v DOWN 0..1).
    view_frame gives the camera-space view rectangle; derive corners by coordinate
    (robust to the API's corner ordering)."""
    camd = cam.data
    mw = cam.matrix_world
    frame = [f.copy() for f in camd.view_frame(scene=bpy.context.scene)]
    min_x, max_x = min(f.x for f in frame), max(f.x for f in frame)
    min_y, max_y = min(f.y for f in frame), max(f.y for f in frame)
    pt = Vector((min_x + u * (max_x - min_x), max_y - v * (max_y - min_y), frame[0].z))
    if camd.type == "ORTHO":
        origin = mw @ pt
        direction = (mw.to_quaternion() @ Vector((0, 0, -1))).normalized()
    else:
        origin = mw.translation.copy()
        direction = ((mw @ pt) - origin).normalized()
    return origin, direction


def _median(vals):
    s = sorted(vals)
    n = len(s)
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


def _cluster(hits, diag):
    """Median-inlier cluster of per-view surface hits → one anchor, or None."""
    if not hits:
        return None
    med = Vector((_median([h[0].x for h in hits]), _median([h[0].y for h in hits]),
                  _median([h[0].z for h in hits])))
    thr = max(diag * 0.12, 0.01)
    inliers = [h for h in hits if (h[0] - med).length <= thr]
    if not inliers:
        return None
    pos = sum((h[0] for h in inliers), Vector()) / len(inliers)
    normal = sum((h[1] for h in inliers), Vector())
    normal = normal.normalized() if normal.length > 1e-6 else Vector((0, 0, 1))
    return {"pos": pos, "normal": normal,
            "confidence": round(len(inliers) / len(hits), 3), "views": len(inliers)}


# ── exported-space conversion (Blender Z-up → asset Y-up, the shipped GLB/USDZ) ──

def _yup(v):
    return [round(v.x, 5), round(v.z, 5), round(-v.y, 5)]


def _offset_yup(pos, mn, mx):
    """(pos - bbox.min) / extents in the EXPORTED Y-up frame — iOS anchorOffset."""
    ex = [max(mx.x - mn.x, 1e-6), max(mx.z - mn.z, 1e-6), max(mx.y - mn.y, 1e-6)]
    off = [(pos.x - mn.x) / ex[0], (pos.z - mn.z) / ex[1], (mx.y - pos.y) / ex[2]]
    return [round(min(max(o, 0.0), 1.0), 4) for o in off]


# ── Gemini (REST pattern of gemini_images/llm; strict JSON) ──────────────────

def _gemini_json(parts, *, temperature=0.2, timeout=120):
    key = config.require("GEMINI_API_KEY")
    body = {"contents": [{"parts": parts}],
            "generationConfig": {"responseMimeType": "application/json",
                                 "temperature": temperature}}
    last = "unknown"
    for _ in range(3):
        try:
            req = urllib.request.Request(API.format(model=MODEL, key=key),
                                         data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
            data = json.load(urllib.request.urlopen(req, timeout=timeout))
            txt = "".join(p.get("text", "")
                          for p in data["candidates"][0]["content"]["parts"])
            return json.loads(txt)
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code} {e.read()[:160].decode('utf-8', 'ignore')!r}"
            if e.code in (429, 500, 503):
                time.sleep(3)
                continue
            raise RuntimeError(f"gemini anchors call failed — {last}")
        except Exception as e:  # noqa: BLE001
            last = repr(e)[:200]
            time.sleep(2)
    raise RuntimeError(f"gemini anchors call failed — {last}")


def _img_parts(images):
    return [{"inlineData": {"mimeType": "image/png",
                            "data": base64.b64encode(p.read_bytes()).decode()}}
            for _, p in images]


POINTS_PROMPT = """You are a precise visual annotator. The first {n} images are renders of the SAME
single 3D object — {desc} — from these viewpoints, in order: {views}.

{task}

For each feature, give its 2D location in EVERY view where it is clearly visible
(skip views where it is occluded or ambiguous). The location must sit ON the
object's surface, at the feature's visual center.

Coordinates: "point": [y, x] — two integers 0-1000, normalized to that image
(y: 0 = top edge, 1000 = bottom edge; x: 0 = left edge, 1000 = right edge).

Return STRICT JSON ONLY:
{{"features": [{{"name": "eye", "points": [{{"view": "front", "point": [430, 510]}},
                                       {{"view": "left", "point": [445, 320]}}]}}]}}"""

VERIFY_PROMPT = """The {n} images show one 3D object — {desc} — from these viewpoints, in order:
{views}. Colored marker dots were placed on its surface, one color per feature:
{mapping}

For EACH feature, judge whether its dot sits ON that feature (the correct spot),
considering every view where the dot is visible. Be strict: a dot floating off the
feature or sitting on a different part of the object is wrong.

Return STRICT JSON ONLY:
{{"results": [{{"name": "eye", "ok": true, "why": "short reason"}}]}}"""


def _ask_points(subject, desc, images, max_anchors, only=None, feedback=None,
                marker_images=None, required=None):
    names = ", ".join(n for n, _ in images)
    if only:
        lines = "; ".join(f"{n}: {feedback.get(n, 'misplaced')}" for n in only)
        task = (f"Locate ONLY these features: {', '.join(only)}. A previous attempt "
                f"placed their markers wrongly ({lines}). Images {len(images) + 1}-"
                f"{len(images) + len(marker_images or [])} show the SAME views with "
                f"the current WRONG marker dots — give corrected locations.")
    else:
        # Story-driven bias: when the lesson DECLARES the parts it will explain
        # (asset_brief.required_part_names), force those by exact name so the part the
        # story cares about (e.g. the eye) is FOUND, not dropped as "not meaningful".
        must = ""
        if required:
            must = (f" You MUST also locate these specific named features if they are "
                    f"visible anywhere on the object: {', '.join(required)}. Look "
                    f"carefully — small features like an eye are easy to miss but are "
                    f"required. ")
        task = (f"Identify the 3-{max_anchors} most semantically meaningful surface "
                f"features of this {subject} — the spots a teacher would point at "
                f"while explaining it (for an animal: eye, mouth, leg; for a tool: "
                f"handle, trigger, nozzle).{must}Use short lowercase snake_case names.")
    prompt = POINTS_PROMPT.format(n=len(images), desc=desc, views=names, task=task)
    parts = [{"text": prompt}] + _img_parts(images) + _img_parts(marker_images or [])
    data = _gemini_json(parts)
    out = {}
    for f in (data.get("features") or []):
        name = re.sub(r"[^a-z0-9]+", "_", str(f.get("name") or "").lower()).strip("_")[:48]
        pts = [p for p in (f.get("points") or [])
               if isinstance(p.get("point"), (list, tuple)) and len(p["point"]) == 2]
        if name and pts and (not only or name in only):
            out[name] = pts
    return out


def _ask_verify(subject, desc, marker_images, color_by_name):
    mapping = "\n".join(f"- {c} dot = {n}" for n, c in color_by_name.items())
    prompt = VERIFY_PROMPT.format(n=len(marker_images), desc=desc,
                                  views=", ".join(n for n, _ in marker_images),
                                  mapping=mapping)
    data = _gemini_json([{"text": prompt}] + _img_parts(marker_images))
    verdicts = {}
    for r in (data.get("results") or []):
        name = re.sub(r"[^a-z0-9]+", "_", str(r.get("name") or "").lower()).strip("_")
        if name:
            verdicts[name] = (bool(r.get("ok")), str(r.get("why") or "")[:160])
    return verdicts


# ── markers (the QA dots) ─────────────────────────────────────────────────────

def _marker_material(idx, rgba):
    mat = bpy.data.materials.new(f"SPATAIL_marker_{idx}")
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs[0].default_value = rgba
    em.inputs[1].default_value = 5.0
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(em.outputs[0], out.inputs[0])
    return mat


def _place_markers(anchors, diag, existing):
    """One emissive colored sphere per anchor, seated on the surface point.
    Reuses spheres across QA rounds (same color stays with the same feature)."""
    radius = max(diag * 0.02, 0.004)
    for i, (name, a) in enumerate(anchors.items()):
        loc = a["pos"] + a["normal"] * radius * 0.6
        sph = existing.get(name)
        if sph is None:
            bpy.ops.mesh.primitive_uv_sphere_add(radius=radius, segments=16,
                                                 ring_count=8, location=loc)
            sph = bpy.context.active_object
            sph.name = f"SPATAIL_marker_{name}"
            sph.data.materials.append(_marker_material(i, MARKER_COLORS[i % 6][1]))
            existing[name] = sph
        else:
            sph.location = loc
    for name, sph in list(existing.items()):
        sph.hide_render = name not in anchors
    bpy.context.view_layer.update()
    return existing


def _render_views(views, out_dir, prefix):
    return [(name, _render(cam, out_dir / f"{prefix}_{name}.png")) for name, cam in views]


# ── per-asset pipeline ────────────────────────────────────────────────────────

def _project(name_points, cams_by_view, bvh, diag):
    anchors = {}
    for name, pts in name_points.items():
        hits = []
        for p in pts:
            cam = cams_by_view.get(str(p.get("view") or "").strip().lower())
            if cam is None:
                continue
            y, x = float(p["point"][0]) / 1000.0, float(p["point"][1]) / 1000.0
            if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
                continue
            origin, direction = _pixel_ray(cam, x, y)
            loc, nrm, _idx, _dist = bvh.ray_cast(origin, direction)
            if loc is not None:
                hits.append((loc, nrm))
        c = _cluster(hits, diag)
        if c:
            anchors[name] = c
    return anchors


def _anchor_asset(a):
    subject = a.get("subject") or a["assetId"].replace("_", " ")
    desc = a.get("desc") or subject
    n_views = max(2, min(int(a.get("views", 4)), len(VIEW_ORDER)))
    required = [re.sub(r"[^a-z0-9]+", "_", str(n).lower()).strip("_")
                for n in (a.get("required_names") or [])]
    required = [n for n in required if n]
    # ensure the budget can hold the story's required parts PLUS a few generic ones
    max_anchors = max(1, min(max(int(a.get("max_anchors", 6)), len(required) + 2),
                             len(MARKER_COLORS)))
    out_dir = Path(a["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    _clear()
    meshes = [o for o in _import_glb(a["glb_in"]) if o.type == "MESH"]
    if not meshes:
        raise RuntimeError("no mesh in normalized GLB")
    obj = _join(meshes)
    _setup_render()
    mn, mx = _bbox_world(obj)
    center, diag = (mn + mx) / 2, (mx - mn).length
    views = _rig_views(center, diag, n_views)
    cams_by_view = {name: cam for name, cam in views}
    bvh, bm = _bvh_world(obj)

    try:
        clean = _render_views(views, out_dir, "render")
        print(f"[anchors] {a['assetId']}: {len(clean)} views rendered")

        # Gemini points → back-project → cluster
        name_points = _ask_points(subject, desc, clean, max_anchors, required=required)
        anchors = _project(dict(list(name_points.items())[:max_anchors]),
                           cams_by_view, bvh, diag)
        print(f"[anchors] {a['assetId']}: {len(name_points)} features pointed, "
              f"{len(anchors)} projected onto the mesh")
        if not anchors:
            return {"assetId": a["assetId"], "anchors": [], "dropped": list(name_points)}

        # QA loop: markers → Gemini verifies → one retry → drop still-failing.
        # A QA hiccup (flaky call) ships the projected anchors unverified instead
        # of discarding them — confidence still reflects the view consensus.
        verified = set()
        try:
            color_by_name = {n: MARKER_COLORS[i % 6][0] for i, n in enumerate(anchors)}
            spheres = _place_markers(anchors, diag, {})
            markers = _render_views(views, out_dir, "marker")
            verdicts = _ask_verify(subject, desc, markers, color_by_name)
            failed = [n for n in anchors if verdicts.get(n, (True, ""))[0] is False]
            verified = {n for n in anchors if n not in failed}
            if failed:
                print(f"[anchors] {a['assetId']}: retrying {failed}")
                feedback = {n: verdicts.get(n, (False, "misplaced"))[1] for n in failed}
                retry_pts = _ask_points(subject, desc, clean, max_anchors, only=failed,
                                        feedback=feedback, marker_images=markers)
                moved = _project(retry_pts, cams_by_view, bvh, diag)
                for n in failed:
                    if n in moved:
                        anchors[n] = moved[n]
                _place_markers(anchors, diag, spheres)
                markers2 = _render_views(views, out_dir, "marker_retry")
                verdicts2 = _ask_verify(subject, desc, markers2,
                                        {n: color_by_name[n] for n in failed})
                still = [n for n in failed if verdicts2.get(n, (True, ""))[0] is False]
                verified |= {n for n in failed if n not in still}
                for n in still:
                    anchors.pop(n, None)
                if still:
                    print(f"[anchors] {a['assetId']}: dropped after retry: {still}")
        except Exception as e:  # noqa: BLE001
            print(f"[anchors] {a['assetId']}: QA pass skipped ({e!r})"[:200])
    finally:
        bm.free()

    result = []
    for name, c in anchors.items():
        result.append({"name": name, "pos_m": _yup(c["pos"]),
                       # raw Blender Z-up world point — the regions stage re-imports the
                       # SAME GLB, so it can sphere-select around this directly (no
                       # exported-frame reconstruction). pos_m stays the iOS contract.
                       "pos_world": [round(c["pos"].x, 5), round(c["pos"].y, 5),
                                     round(c["pos"].z, 5)],
                       "normal": _yup(c["normal"]),
                       "offset": _offset_yup(c["pos"], mn, mx),
                       "confidence": c["confidence"], "views": c["views"],
                       "verified": name in verified})
    dropped = [n for n in name_points if n not in anchors]
    print(f"[anchors] {a['assetId']}: {len(result)} anchors "
          f"({', '.join(x['name'] for x in result) or 'none'})")
    return {"assetId": a["assetId"], "anchors": result, "dropped": dropped,
            "renders": str(out_dir)}


def main():
    spec = json.loads(Path(sys.argv[sys.argv.index("--") + 1]).read_text(encoding="utf-8"))
    done, failed = [], []
    for a in spec["assets"]:
        try:
            done.append(_anchor_asset(a))
        except Exception as e:  # noqa: BLE001
            failed.append({"assetId": a["assetId"], "error": str(e)[:400]})
            print(f"[anchors] FAILED {a['assetId']}: {e}")
    Path(spec["result_path"]).write_text(
        json.dumps({"done": done, "failed": failed}, indent=2), encoding="utf-8")
    print(f"[anchors] {len(done)} ok, {len(failed)} failed")


main()
