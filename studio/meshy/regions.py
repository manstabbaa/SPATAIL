"""regions.py — run INSIDE Blender headless: STORY-driven addressable sub-mesh regions.

    blender --background --python studio/meshy/regions.py -- <spec.json>

spec: {result_path, export_usdz?:bool,
       assets:[{assetId, glb_in, out_dir,
                regions:[{id, label?, role?, effects?,
                          pos_world?:[x,y,z],   # raw Blender Z-up world point (preferred)
                          pos_m?:[x,y,z],       # exported Y-up point (anchors.py) — reconstructed
                          offset?:[x,y,z],      # normalized bbox offset (reused if given)
                          radius_frac?:float}]}]}

Slice 1 of the story-first plan. The director DECLARES which parts the lesson will
point at / make emissive (asset_brief). The anchors stage localizes them on the mesh.
This stage turns each one into something the runtime can light up PRECISELY:

  1. grow a face patch around the part's surface point (spatail_mesh_select.sphere)
  2. bake that patch into a SEPARATE offset overlay mesh with an emissive highlight
     material (spatail_mesh_region.bake_region_overlay) — the base mesh is untouched
  3. re-export the GLB (+ optional USDZ) WITH the overlays, so they ride through the
     downstream decimate/animate stages and ship as named entities
     `spatail_region__<mesh>__<id>` the iOS runtime can find, enable, and effect.

Why an overlay (not a mesh split): non-destructive — the authored asset keeps its
exact geometry/materials; the region is addable/removable; and because each overlay is
its OWN mesh, the existing whole-mesh runtime highlight path lights exactly that patch.

Region records are reported in the asset's EXPORTED Y-up space: `offset` normalized to
the bbox ((pos-min)/extent), matching anchors / iOS step.anchorOffset.
"""
import json
import sys
from pathlib import Path

import bpy
from mathutils import Vector

_HERE = Path(__file__).resolve().parent
for _p in (str(_HERE.parents[1] / "pipeline" / "blender"),   # spatail_mesh_select / _region
           str(_HERE.parent / "library")):                   # bake_assets (proven exporters)
    if _p not in sys.path:
        sys.path.insert(0, _p)

import spatail_mesh_select as ms   # noqa: E402  (selection: sphere → face patch)
import spatail_mesh_region as mr   # noqa: E402  (transport: overlay bake + sidecar)
import bake_assets as BA           # noqa: E402  (apply+join, GLB/USDZ exporters — pipeline parity)


# ── scene ─────────────────────────────────────────────────────────────────────

def _clear():
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for blk in (bpy.data.meshes, bpy.data.materials, bpy.data.images, bpy.data.objects):
        for b in list(blk):
            if getattr(b, "users", 0) == 0:
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


# ── coordinate helpers (Blender Z-up world ↔ exported Y-up bbox offset) ──────────

def _world_bbox(obj):
    lo = Vector((float("inf"),) * 3)
    hi = Vector((-float("inf"),) * 3)
    mw = obj.matrix_world
    for c in obj.bound_box:
        p = mw @ Vector(c)
        lo = Vector(map(min, lo, p))
        hi = Vector(map(max, hi, p))
    return lo, hi


def _offset_yup(p, lo, hi):
    """(p - bbox.min)/extents in EXPORTED Y-up frame — anchors.py / iOS anchorOffset.
    Y-up maps Blender (x, y, z) → (x, z, -y)."""
    ex = [max(hi.x - lo.x, 1e-6), max(hi.z - lo.z, 1e-6), max(hi.y - lo.y, 1e-6)]
    off = [(p[0] - lo.x) / ex[0], (p[2] - lo.z) / ex[1], (hi.y - p[1]) / ex[2]]
    return [round(min(max(o, 0.0), 1.0), 4) for o in off]


def _center_world(r):
    """Resolve a region's Blender Z-up world centre from the richest hint available.
    pos_world (raw Blender) > pos_m (exported Y-up, reconstructed) > offset→bbox later."""
    if r.get("pos_world") and len(r["pos_world"]) == 3:
        return [float(x) for x in r["pos_world"]]
    if r.get("pos_m") and len(r["pos_m"]) == 3:
        x, y, z = (float(v) for v in r["pos_m"])     # exported Y-up = (bx, bz, -by)
        return [x, -z, y]                             # → Blender Z-up (bx, by, bz)
    return None


def _unhide_overlays():
    for o in bpy.data.objects:
        if o.name.startswith(mr.OVERLAY_PREFIX):
            o.hide_render = False
            o.hide_viewport = False
            try:
                o.hide_set(False)
            except RuntimeError:
                pass


# ── per-asset ───────────────────────────────────────────────────────────────────

def _region_asset(a, export_usdz=False):
    glb_in = a["glb_in"]
    out_dir = Path(a["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    _clear()
    new = _import_glb(glb_in)
    meshes = [o for o in new if o.type == "MESH" and o.data and o.data.vertices]
    if not meshes:
        raise RuntimeError("no mesh in GLB")
    # apply transforms + join (matches meshy_normalize) so the mesh sits in true metric
    # coords with an IDENTITY object transform — required for spatail_mesh_select's
    # world-radius sphere to map cleanly to local space (a leftover import scale would
    # otherwise blow the local radius up and select the whole mesh).
    src = BA._apply_and_join(meshes)
    for o in list(bpy.data.objects):
        if o is not src and o.type != "MESH":
            bpy.data.objects.remove(o, do_unlink=True)
    src_name = src.name
    bpy.context.view_layer.update()
    lo, hi = _world_bbox(src)
    diag = max((hi - lo).length, 1e-4)

    baked, skipped = [], []
    for r in a.get("regions") or []:
        rid = str(r.get("id") or "").strip()
        if not rid:
            continue
        center = _center_world(r)
        offset = r.get("offset") if (r.get("offset") and len(r.get("offset")) == 3) else None
        if center is None and offset is None:
            skipped.append({"id": rid, "why": "no center (pos_world/pos_m/offset)"})
            continue
        if center is None:
            # offset → Blender world point (inverse of _offset_yup)
            ox, oy, oz = (float(v) for v in offset)
            center = [lo.x + ox * (hi.x - lo.x), hi.y - oz * (hi.y - lo.y),
                      lo.z + oy * (hi.z - lo.z)]

        # grow a face patch around the point; widen if the first radius misses faces
        sel, used_r = None, 0.0
        for mult in (1.0, 1.6, 2.4):
            rad = float(r.get("radius_frac", 0.06)) * diag * mult
            cand = ms.select_region(src_name, {"sphere": {"center_world": center, "radius": rad}})
            if cand.get("counts", {}).get("faces", 0) > 0:
                sel, used_r = cand, rad
                break
        if sel is None:
            skipped.append({"id": rid, "why": "no faces near point"})
            continue

        bake = mr.bake_region_overlay(src_name, rid, sel,
                                      label=r.get("label"), hide_by_default=True)
        if bake.get("skipped"):
            skipped.append({"id": rid, "why": bake["skipped"]})
            continue

        # report offset: reuse the anchor's if given (stays consistent with the step),
        # else compute from the patch centroid.
        if offset is None:
            cw = sel.get("centroid_world") or center
            offset = _offset_yup(cw, lo, hi)
        baked.append({
            "id": rid,
            "label": r.get("label") or rid.replace("_", " ").title(),
            "role": r.get("role") or rid,
            "overlayNode": bake["overlayMesh"],
            "offset": [round(float(o), 4) for o in offset],
            "radiusNorm": round(min(max(used_r / diag, 0.0), 1.0), 4),
            "effects": list(r.get("effects") or ["highlight"]),
            "verified": bool(r.get("verified", False)),
            "nFaces": bake.get("n_faces", 0),
        })

    # re-export WITH the overlays so decimate/animate carry them downstream. Reuse the
    # library exporters (pipeline parity + correct Blender-5.1 USD kwargs).
    _unhide_overlays()
    out_glb = out_dir / f"{a['assetId']}.glb"
    if not BA._export_glb(out_glb):
        raise RuntimeError("GLB export failed")
    usdz_ok = BA._export_usdz(out_dir / f"{a['assetId']}.usdz") if export_usdz else False

    print(f"[regions] {a['assetId']}: baked {len(baked)} "
          f"({', '.join(b['id'] for b in baked) or 'none'}); skipped {len(skipped)}")
    return {"assetId": a["assetId"], "regions": baked, "skipped": skipped,
            "meshId": src_name, "glb": str(out_glb), "usdz": usdz_ok}


def main():
    spec = json.loads(Path(sys.argv[sys.argv.index("--") + 1]).read_text(encoding="utf-8"))
    export_usdz = bool(spec.get("export_usdz", False))
    done, failed = [], []
    for a in spec["assets"]:
        try:
            done.append(_region_asset(a, export_usdz=export_usdz))
        except Exception as e:  # noqa: BLE001
            failed.append({"assetId": a.get("assetId"), "error": str(e)[:400]})
            print(f"[regions] FAILED {a.get('assetId')}: {e}")
    Path(spec["result_path"]).write_text(
        json.dumps({"done": done, "failed": failed}, indent=2), encoding="utf-8")
    print(f"[regions] {len(done)} ok, {len(failed)} failed")


main()
