"""skeleton.py — run INSIDE Blender headless: VLM multi-view ARTICULATION SKELETON.

    blender --background --python studio/meshy/skeleton.py -- <spec.json>

spec: {result_path, assets:[{assetId, subject, desc, glb_in, out_dir, views?, max_joints?}]}

Phase (b) of the rig pivot — "anchors for a skeleton". Reuses anchors.py's multi-view
render + Gemini machinery, but instead of pointing at SURFACE features it asks the VLM for
the ARTICULATION JOINTS (which usually sit INSIDE the mesh) + their parent hierarchy, then
TRIANGULATES each joint's 2D points across views into a 3D position (closest point to the
camera rays — NOT a surface ray-cast, because joints are internal). Output: a skeleton
spec the armature builder + the LLM author consume to rig the asset for the requested
motion. Also writes a joint-overlay contact sheet (skeleton over the ghosted mesh) for
visual QA — the multi-view input the rigging brain reasons over.
"""
import json
import re
import sys
from pathlib import Path

import bpy
import numpy as np
from mathutils import Vector

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
import anchors as A   # noqa: E402  (import-safe: its main() is __main__-guarded)


SKELETON_PROMPT = """You are a 3D rigging assistant. The {n} images are renders of the SAME object — {desc} — from these viewpoints, in order: {views}.

Identify the ARTICULATION SKELETON an animator would build to pose/animate this object: the key JOINTS (pivot points) and how they connect parent -> child.
  - animal: hip(root), spine -> chest -> neck -> head; each leg: upper(hip/shoulder) -> knee -> foot; tail segments.
  - plant: base(root) -> stem segments -> branch tips.
  - mechanism: each hinge / axle / slider as a joint.
Keep to the {maxj} most important joints. Use short snake_case names; make left/right explicit.

For EACH joint give: name, parent (the parent joint's name, or "root" for the base), and its 2D location in EVERY view where you can infer it. Joints are usually INSIDE the object — place the point where the joint CENTER appears (e.g. the knee pivot inside the leg).

Coordinates: "point":[y,x] integers 0-1000 (y: 0 top -> 1000 bottom, x: 0 left -> 1000 right).

Return STRICT JSON ONLY:
{{"joints":[{{"name":"hip","parent":"root","points":[{{"view":"front","point":[520,500]}},{{"view":"left","point":[510,480]}}]}}],
  "motion":"one short phrase for how it should move (e.g. 'walk cycle', 'grow from base', 'lid opens')"}}"""


def _ask_skeleton(subject, desc, images, max_joints):
    prompt = SKELETON_PROMPT.format(n=len(images), desc=desc,
                                    views=", ".join(n for n, _ in images), maxj=max_joints)
    data = A._gemini_json([{"text": prompt}] + A._img_parts(images))
    joints = []
    for j in (data.get("joints") or []):
        name = re.sub(r"[^a-z0-9]+", "_", str(j.get("name") or "").lower()).strip("_")[:48]
        parent = re.sub(r"[^a-z0-9]+", "_", str(j.get("parent") or "root").lower()).strip("_")[:48] or "root"
        pts = [p for p in (j.get("points") or [])
               if isinstance(p.get("point"), (list, tuple)) and len(p["point"]) == 2]
        if name and pts:
            joints.append({"name": name, "parent": parent, "points": pts})
    return joints[:max_joints], str(data.get("motion") or "")


def _triangulate(rays):
    """Closest 3D point to a set of camera rays (least squares): minimize sum of squared
    distances to each ray. rays: [(origin Vector, dir Vector)]."""
    mat = np.zeros((3, 3))
    vec = np.zeros(3)
    for o, d in rays:
        dd = np.array([d.x, d.y, d.z], float)
        n = np.linalg.norm(dd)
        if n < 1e-9:
            continue
        dd /= n
        proj = np.eye(3) - np.outer(dd, dd)          # projector onto the plane ⟂ ray
        mat += proj
        vec += proj @ np.array([o.x, o.y, o.z], float)
    try:
        p = np.linalg.solve(mat, vec)
    except Exception:
        return None
    return Vector((float(p[0]), float(p[1]), float(p[2])))


def _project_joints(joints, cams_by_view, mn, mx):
    """Triangulate each joint's 2D points into a 3D position; clamp to a padded bbox so a
    single bad view can't fling a joint to infinity."""
    pad = (mx - mn) * 0.15
    lo, hi = mn - pad, mx + pad
    out = {}
    for j in joints:
        rays = []
        for p in j["points"]:
            cam = cams_by_view.get(str(p.get("view") or "").strip().lower())
            if cam is None:
                continue
            y, x = float(p["point"][0]) / 1000.0, float(p["point"][1]) / 1000.0
            if 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0:
                rays.append(A._pixel_ray(cam, x, y))
        if len(rays) < 2:                            # need ≥2 views to triangulate
            continue
        pos = _triangulate(rays)
        if pos is None:
            continue
        pos = Vector((min(max(pos.x, lo.x), hi.x), min(max(pos.y, lo.y), hi.y),
                      min(max(pos.z, lo.z), hi.z)))
        out[j["name"]] = {"pos": pos, "parent": j["parent"], "views": len(rays)}
    return out


# ── visual QA: skeleton over the ghosted mesh ─────────────────────────────────

def _emissive(rgba, strength=4.0):
    m = bpy.data.materials.new("SKEL_VIZ")
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    e = nt.nodes.new("ShaderNodeEmission")
    e.inputs[0].default_value = rgba
    e.inputs[1].default_value = strength
    o = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(e.outputs[0], o.inputs[0])
    return m


def _ghost(obj, alpha=0.22):
    for m in obj.data.materials:
        if not m:
            continue
        try:
            m.blend_method = "BLEND"
        except Exception:
            pass
        if m.use_nodes:
            b = m.node_tree.nodes.get("Principled BSDF")
            if b and "Alpha" in b.inputs:
                b.inputs["Alpha"].default_value = alpha


def _bone_cyl(p0, p1, rad, mat):
    d = p1 - p0
    if d.length < 1e-5:
        return
    bpy.ops.mesh.primitive_cylinder_add(radius=rad, depth=d.length, location=(p0 + p1) / 2)
    c = bpy.context.active_object
    c.rotation_mode = "QUATERNION"
    c.rotation_quaternion = d.to_track_quat("Z", "Y")
    c.data.materials.append(mat)


def _viz_skeleton(joints3d, diag):
    jr = max(diag * 0.018, 0.004)
    jmat = _emissive((1.0, 0.15, 0.05, 1.0))
    bmat = _emissive((0.1, 0.55, 1.0, 1.0), 3.0)
    for name, j in joints3d.items():
        bpy.ops.mesh.primitive_uv_sphere_add(radius=jr, segments=14, ring_count=8,
                                             location=j["pos"])
        s = bpy.context.active_object
        s.name = f"SKELJ_{name}"
        s.data.materials.append(jmat)
    for name, j in joints3d.items():
        par = joints3d.get(j["parent"])
        if par:
            _bone_cyl(par["pos"], j["pos"], jr * 0.45, bmat)


def _contact_sheet(out_dir, prefix, view_names):
    from PIL import Image, ImageDraw
    tiles = []
    for v in view_names:
        p = Path(out_dir) / f"{prefix}_{v}.png"
        if p.exists():
            tiles.append((v, Image.open(p).convert("RGB")))
    if not tiles:
        return ""
    cols = 2
    tw, th = tiles[0][1].size
    rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * tw, rows * th + 20), (245, 245, 247))
    d = ImageDraw.Draw(sheet)
    for i, (v, im) in enumerate(tiles):
        x, y = (i % cols) * tw, (i // cols) * th + 20
        sheet.paste(im, (x, y))
        d.text((x + 4, y + 2), v, fill=(255, 80, 40))
    d.text((4, 4), "skeleton over ghosted mesh", fill=(30, 30, 30))
    out = Path(out_dir) / f"{prefix}_contact.png"
    sheet.save(out)
    return str(out)


# ── per-asset ─────────────────────────────────────────────────────────────────

def _skeleton_asset(a):
    subject = a.get("subject") or a["assetId"].replace("_", " ")
    desc = a.get("desc") or subject
    n_views = max(2, min(int(a.get("views", 4)), len(A.VIEW_ORDER)))
    max_joints = int(a.get("max_joints", 12))
    out_dir = Path(a["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    A._clear()
    meshes = [o for o in A._import_glb(a["glb_in"]) if o.type == "MESH"]
    if not meshes:
        raise RuntimeError("no mesh in GLB")
    obj = A._join(meshes)
    A._setup_render()
    mn, mx = A._bbox_world(obj)
    center, diag = (mn + mx) / 2, (mx - mn).length
    views = A._rig_views(center, diag, n_views)
    cams_by_view = {n: c for n, c in views}

    clean = A._render_views(views, out_dir, "skel")
    joints_raw, motion = _ask_skeleton(subject, desc, clean, max_joints)
    joints3d = _project_joints(joints_raw, cams_by_view, mn, mx)
    print(f"[skeleton] {a['assetId']}: {len(joints_raw)} proposed, "
          f"{len(joints3d)} triangulated; motion={motion!r}")

    _ghost(obj)
    _viz_skeleton(joints3d, diag)
    A._render_views(views, out_dir, "skel_rig")
    sheet = _contact_sheet(out_dir, "skel_rig", [n for n, _ in views])

    recs = []
    for name, j in joints3d.items():
        recs.append({"name": name, "parent": j["parent"],
                     "pos_world": [round(j["pos"].x, 5), round(j["pos"].y, 5), round(j["pos"].z, 5)],
                     "pos_m": A._yup(j["pos"]), "views": j["views"]})
    return {"assetId": a["assetId"], "joints": recs, "motion": motion,
            "n_proposed": len(joints_raw), "contact_sheet": sheet, "renders": str(out_dir)}


def main():
    spec = json.loads(Path(sys.argv[sys.argv.index("--") + 1]).read_text(encoding="utf-8"))
    done, failed = [], []
    for a in spec["assets"]:
        try:
            done.append(_skeleton_asset(a))
        except Exception as e:  # noqa: BLE001
            failed.append({"assetId": a.get("assetId"), "error": str(e)[:400]})
            print(f"[skeleton] FAILED {a.get('assetId')}: {e}")
    Path(spec["result_path"]).write_text(
        json.dumps({"done": done, "failed": failed}, indent=2), encoding="utf-8")
    print(f"[skeleton] {len(done)} ok, {len(failed)} failed")


if __name__ == "__main__":
    main()
