"""spatial_placer.py — run INSIDE Blender headless: the AUTHORITATIVE spatial placer.

    blender --background --python studio/blender/spatial_placer.py -- <spec.json>

The "correct spatial object placer" (docs/blender-spatial-intelligence-plan.md Phase 3,
the 2026-06-14 redirect): instead of the phone guessing placement against a coarse
plane, Blender RECREATES the scanned room as real geometry and places the hero into it
with grounded scale and real collision — then bakes the transform the phone applies.

Pipeline:
  1. parse the RoomModel (device frame: metres, +Y up, user near origin looking -Z)
  2. recreate the space in Blender as named collision proxies — floor, walls, the
     support surface (table) box, and one box per detected obstacle/hitbox
  3. import the hero GLB and GROUND its scale two ways (we offer BOTH, per §5):
       - "true"  : the subject's real-world size (LLM-estimated metres)
       - "table" : fit-to-surface, longest footprint = min(table) x coverage (~0.65)
  4. SEAT it: raycast straight down onto the support surface so it RESTS (never floats
     or sinks), centred on the surface, facing the user
  5. COLLIDE: AABB keep-out vs obstacle boxes + a BVH penetration test vs the surface;
     verify the footprint fits the surface extent; report fits + a human reason
  6. bake {position_m (device Y-up), yaw_rad, scale, size_m, fits} per variant, and
     render a preview PNG so the placement is VISIBLE.

Output positions are in the device Y-up frame (blender_to_yup) — exactly the frame the
RoomModel, the placement_solver Placement, and the iOS runtime already use.
"""
import json
import math
import os
import sys
from pathlib import Path

import bpy
import bmesh
from mathutils import Vector
from mathutils.bvhtree import BVHTree

RES = 900
COVERAGE_DEFAULT = 0.65
EYE_HEIGHT_M = 1.45


# ── frame conversion: device (Y-up, -Z fwd) <-> Blender (Z-up, +Y fwd) ───────────

def d2b(p):
    """device (x, y_up, z_fwd) -> Blender (x, y_fwd, z_up). A proper +90deg-about-X
    rotation: device -Z forward becomes Blender +Y forward, device +Y up -> +Z up."""
    return Vector((p[0], -p[2], p[1]))


def b2d(v):
    """Blender (x, y, z) -> device Y-up [x, z, -y] (matches build_studio.blender_to_yup
    and studio/meshy exporters)."""
    return [round(v.x, 5), round(v.z, 5), round(-v.y, 5)]


# ── scene / render ───────────────────────────────────────────────────────────────

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


def _mat(name, rgba, *, rough=0.85, emit=False):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    if emit:
        nt.nodes.clear()
        em = nt.nodes.new("ShaderNodeEmission")
        em.inputs[0].default_value = rgba
        em.inputs[1].default_value = 1.0
        out = nt.nodes.new("ShaderNodeOutputMaterial")
        nt.links.new(em.outputs[0], out.inputs[0])
        return mat
    bsdf = nt.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = rgba
        try:
            bsdf.inputs["Roughness"].default_value = rough
        except Exception:
            pass
    return mat


def _box(name, size, center, rgba):
    """An axis-aligned box (size = full extents) centred at `center` (Blender)."""
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    for v in bm.verts:
        v.co = Vector((v.co.x * size[0], v.co.y * size[1], v.co.z * size[2]))
    bm.to_mesh(me)
    bm.free()
    obj = bpy.data.objects.new(name, me)
    obj.location = center
    obj.data.materials.append(_mat(f"m_{name}", rgba))
    bpy.context.collection.objects.link(obj)
    return obj


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


def _world_aabb(obj):
    bpy.context.view_layer.update()
    pts = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    mn = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    mx = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return mn, mx


def _bvh(obj):
    deps = bpy.context.evaluated_depsgraph_get()
    bm = bmesh.new()
    bm.from_object(obj, deps)
    bm.transform(obj.matrix_world)
    tree = BVHTree.FromBMesh(bm)
    bm.free()
    return tree


# ── room recreation ──────────────────────────────────────────────────────────────

def _surface(room, cls):
    surfs = [s for s in room.get("surfaces", []) if s.get("cls") == cls]
    return max(surfs, key=lambda s: s["size_m"][0] * s["size_m"][1], default=None) if surfs else None


def recreate_room(room):
    """Build the scanned room as named collision proxies in Blender. Returns the
    proxy objects + the chosen support surface dict (table preferred, else floor)."""
    proxies, obstacles = [], []
    floor_y = 0.0
    floor_obj = None
    fl = _surface(room, "floor")
    if fl:
        floor_y = float(fl.get("height_m", 0.0))
        c = d2b(fl["center_m"]); c.z = floor_y - 0.01
        floor_obj = _box("room_floor", (fl["size_m"][0], fl["size_m"][1], 0.02),
                         c, (0.82, 0.80, 0.75, 1))
        proxies.append(floor_obj)
    # walls (collision context; thin vertical slabs)
    for i, s in enumerate(room.get("surfaces", [])):
        if s.get("cls") == "wall":
            c = d2b(s["center_m"])
            w, h = s["size_m"][0], s["size_m"][1]
            proxies.append(_box(f"room_wall_{i}", (max(w, 0.05), 0.04, max(h, 0.05)),
                                c, (0.88, 0.87, 0.83, 1)))
    # support surface (the table the user is at), as a box whose TOP is at height_m
    table = _surface(room, "table")
    support = table or fl
    support_obj = floor_obj                               # seat on the floor proxy by default
    if table:
        h = float(table.get("height_m", 0.74))
        c = d2b(table["center_m"]); c.z = h - 0.02
        support_obj = _box("room_table", (table["size_m"][0], table["size_m"][1], 0.04),
                           c, (0.62, 0.50, 0.40, 1))
        proxies.append(support_obj)
    # obstacles -> keep-out boxes (real hitboxes from the scan)
    for i, o in enumerate(room.get("obstacles", [])):
        mn, mx = o.get("bbox_min_m"), o.get("bbox_max_m")
        if not (mn and mx):
            continue
        bmn, bmx = d2b(mn), d2b(mx)
        lo = Vector((min(bmn.x, bmx.x), min(bmn.y, bmx.y), min(bmn.z, bmx.z)))
        hi = Vector((max(bmn.x, bmx.x), max(bmn.y, bmx.y), max(bmn.z, bmx.z)))
        size = hi - lo
        if size.length < 1e-4:
            continue
        ob = _box(f"obstacle_{i}_{o.get('category', 'x')}",
                  (max(size.x, 0.02), max(size.y, 0.02), max(size.z, 0.02)),
                  (lo + hi) / 2, (0.85, 0.35, 0.30, 1))
        obstacles.append((ob, lo, hi, o.get("category", "unknown")))
        proxies.append(ob)
    return proxies, support, support_obj, obstacles, floor_y


# ── hero scaling, seating, collision ──────────────────────────────────────────────

def _delete(obj):
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.ops.object.delete()


def _build_and_place(hero, mode, support, anchor, coverage, real, target_xy,
                     support_bvh, support_top_z, floor_y, obstacles):
    """Import a FRESH hero, ground its scale for `mode`, seat it on the support and
    test collision. Returns (variant_dict, obj). The caller deletes obj before the
    next variant so each is placed in isolation (no stale hero under the raycast)."""
    meshes = [o for o in _import_glb(hero["glb_in"]) if o.type == "MESH"]
    if not meshes:
        raise RuntimeError("no mesh in hero GLB")
    obj = _join(meshes)
    obj.name = f"hero_{mode}"
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True); bpy.context.view_layer.objects.active = obj
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    bpy.context.view_layer.update()
    dims = obj.dimensions
    if mode == "true":
        s = max(float(x) for x in real) / (max(dims.x, dims.y, dims.z) or 1.0)
        note = f"true scale (real {max(real):.2f} m)"
    else:
        avail = (min(support["size_m"]) * coverage) if support else 0.3
        footprint = max(dims.x, dims.y) or 1.0           # horizontal extent (Z-up)
        s = avail / footprint
        note = f"fit to {anchor} ({coverage:.0%})"
    obj.scale = (max(s, 1e-4),) * 3
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    rest_z = _seat_and_place(obj, target_xy, support_bvh, support_top_z, floor_y)
    # search for a free spot on the support (clutter keep-out), then re-seat there
    mn, mx = _world_aabb(obj)
    foot = (mx.x - mn.x, mx.y - mn.y)
    xy, moved, _clear = _resolve_xy(foot, target_xy, support, coverage, obstacles,
                                    (mn.z, mx.z))
    if moved:
        rest_z = _seat_and_place(obj, xy, support_bvh, support_top_z, floor_y)
    fits, fit_reason = _footprint_fits(obj, support, coverage)
    hit = _obstacle_overlap(obj, obstacles)
    if hit:
        fits = False
        fit_reason += f"; overlaps {hit} (no free spot found)"
    elif moved:
        fit_reason += "; nudged clear of clutter"
    mn, mx = _world_aabb(obj)
    variant = {
        "position_m": b2d(obj.location),
        "yaw_rad": round(obj.rotation_euler.z, 4),
        "scale": round(s, 5),
        "size_m": [round(mx.x - mn.x, 4), round(mx.z - mn.z, 4), round(mx.y - mn.y, 4)],
        "restHeight_m": round(rest_z, 4),
        "fits": bool(fits),
        "reason": f"{note}; {fit_reason}",
    }
    return variant, obj


def _yaw_face_user(x, y):
    """Yaw (about Z) so the hero's -Y(front) turns toward the user at the origin."""
    return math.atan2(-x, -y) if (abs(x) + abs(y)) > 1e-4 else 0.0


def _seat_and_place(obj, target_xy, support_bvh, support_top_z, floor_y):
    """Centre the hero on target_xy, raycast DOWN onto the SUPPORT-only BVH (so the
    ray never catches the hero itself or clutter) to find the rest height, drop the
    hero's bottom onto it, and face the user."""
    mn, mx = _world_aabb(obj)
    cx, cy = (mn.x + mx.x) / 2, (mn.y + mx.y) / 2
    obj.location += Vector((target_xy[0] - cx, target_xy[1] - cy, 0.0))
    bpy.context.view_layer.update()
    rest_z = support_top_z if support_top_z is not None else floor_y
    if support_bvh is not None:
        origin = Vector((target_xy[0], target_xy[1], rest_z + 1.0))
        loc, _n, _i, _d = support_bvh.ray_cast(origin, Vector((0, 0, -1)))
        if loc is not None:
            rest_z = loc.z
    mn, mx = _world_aabb(obj)
    obj.location += Vector((0.0, 0.0, rest_z - mn.z))      # bottom rests on the surface
    obj.rotation_euler = (0.0, 0.0, _yaw_face_user(target_xy[0], target_xy[1]))
    bpy.context.view_layer.update()
    return rest_z


def _footprint_fits(obj, support, coverage):
    """Does the hero's XY footprint fit within `coverage` of the support extent?"""
    if not support:
        return True, "no support surface"
    mn, mx = _world_aabb(obj)
    fw, fd = mx.x - mn.x, mx.y - mn.y
    eps = 1e-3                                            # 1 mm: ignore rounding slivers
    aw = support["size_m"][0] * coverage + eps
    ad = support["size_m"][1] * coverage + eps
    if fw <= aw and fd <= ad:
        return True, f"footprint {fw:.2f}x{fd:.2f} m fits {coverage:.0%} of {support['cls']}"
    return False, (f"footprint {fw:.2f}x{fd:.2f} m exceeds {coverage:.0%} of "
                   f"{support['cls']} ({aw:.2f}x{ad:.2f} m)")


def _obstacle_overlap(obj, obstacles, margin=0.02):
    """AABB keep-out: does the hero's footprint intersect any obstacle box (in XY,
    at overlapping Z)? Returns the first hit category or None."""
    mn, mx = _world_aabb(obj)
    for _ob, lo, hi, cat in obstacles:
        if (mn.x - margin <= hi.x and mx.x + margin >= lo.x and
                mn.y - margin <= hi.y and mx.y + margin >= lo.y and
                mn.z <= hi.z and mx.z >= lo.z):
            return cat
    return None


def _resolve_xy(foot, base_xy, support, coverage, obstacles, z_range, margin=0.03):
    """Find a free XY for a footprint (foot=(w,d)) on the support: try the centre,
    then a widening ring, keeping the footprint inside `coverage` of the surface and
    clear of any obstacle box that lives within the surface's vertical span. Returns
    (xy, moved_bool, clear_bool)."""
    aw = support["size_m"][0] * coverage if support else foot[0]
    ad = support["size_m"][1] * coverage if support else foot[1]
    hx = max(0.0, aw / 2 - foot[0] / 2)                  # how far the centre can roam
    hy = max(0.0, ad / 2 - foot[1] / 2)
    on_surface = [(lo, hi) for _o, lo, hi, _c in obstacles
                  if lo.z <= z_range[1] + 0.05 and hi.z >= z_range[0] - 0.05]

    def clear(x, y):
        fmnx, fmxx = x - foot[0] / 2 - margin, x + foot[0] / 2 + margin
        fmny, fmxy = y - foot[1] / 2 - margin, y + foot[1] / 2 + margin
        return not any(fmnx <= hi.x and fmxx >= lo.x and fmny <= hi.y and fmxy >= lo.y
                       for lo, hi in on_surface)

    cx, cy = base_xy
    cands = [(0.0, 0.0)]
    for r in (0.3, 0.55, 0.8, 1.0):
        for ang in range(0, 360, 45):
            cands.append((r * hx * math.cos(math.radians(ang)),
                          r * hy * math.sin(math.radians(ang))))
    for ox, oy in cands:
        if clear(cx + ox, cy + oy):
            return (cx + ox, cy + oy), (abs(ox) + abs(oy) > 1e-4), True
    return base_xy, False, False


# ── preview render ─────────────────────────────────────────────────────────────

def _setup_render(target, floor_y):
    scn = bpy.context.scene
    for eng in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "BLENDER_WORKBENCH"):
        try:
            scn.render.engine = eng
            break
        except TypeError:
            continue
    scn.render.resolution_x = RES
    scn.render.resolution_y = int(RES * 0.7)
    scn.render.film_transparent = False
    scn.render.image_settings.file_format = "PNG"
    world = bpy.data.worlds.new("SPATAIL_placer_world")
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs[0].default_value = (0.95, 0.96, 0.98, 1.0)
        bg.inputs[1].default_value = 1.0
    scn.world = world
    sun_d = bpy.data.lights.new("SPATAIL_placer_sun", type="SUN")
    sun_d.energy = 3.0
    sun = bpy.data.objects.new("SPATAIL_placer_sun", sun_d)
    bpy.context.collection.objects.link(sun)
    sun.rotation_euler = (math.radians(52), 0.0, math.radians(40))
    # camera at the user's eye, looking at the placed hero
    cam_d = bpy.data.cameras.new("SPATAIL_placer_cam")
    cam_d.lens = 38
    cam = bpy.data.objects.new("SPATAIL_placer_cam", cam_d)
    bpy.context.collection.objects.link(cam)
    cam.location = Vector((target.x * 0.4, -0.2, max(floor_y, target.z) + EYE_HEIGHT_M * 0.55))
    look = Vector((target.x, target.y, target.z)) - cam.location
    cam.rotation_euler = look.to_track_quat("-Z", "Y").to_euler()
    scn.camera = cam


def _render(path):
    bpy.context.scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    return str(path)


# ── per-asset placement ────────────────────────────────────────────────────────

def place(spec):
    room = spec["room"]
    hero = spec["hero"]
    out_dir = Path(spec.get("out_dir") or ".")
    out_dir.mkdir(parents=True, exist_ok=True)
    coverage = float(hero.get("coverage", COVERAGE_DEFAULT))
    real = hero.get("realSize_m")                         # [w,h,d] metres, LLM-grounded

    _clear()
    proxies, support, support_obj, obstacles, floor_y = recreate_room(room)
    anchor = (support or {}).get("cls", "floor")
    support_bvh = _bvh(support_obj) if support_obj is not None else None
    support_top_z = float((support or {}).get("height_m", floor_y))
    # target XY = the support centre (in Blender)
    if support:
        sc = d2b(support["center_m"])
        target_xy = (sc.x, sc.y)
    else:
        target_xy = (0.0, -1.0)

    variants = {}
    for mode in ("table", "true"):
        if mode == "true" and not real:
            continue
        variant, obj = _build_and_place(hero, mode, support, anchor, coverage, real,
                                        target_xy, support_bvh, support_top_z, floor_y,
                                        obstacles)
        variants[mode] = variant
        _delete(obj)                                      # isolate each variant

    # default: true scale if it FITS the surface, else fit-to-table (the §5 fallback)
    default = "true" if ("true" in variants and variants["true"]["fits"]) else "table"

    preview = None
    if spec.get("render_preview", True) and default in variants:
        try:
            _, obj = _build_and_place(hero, default, support, anchor, coverage, real,
                                      target_xy, support_bvh, support_top_z, floor_y,
                                      obstacles)
            _setup_render(Vector((target_xy[0], target_xy[1],
                                  variants[default]["restHeight_m"])), floor_y)
            preview = _render(out_dir / f"{hero.get('assetId', 'hero')}_placed.png")
        except Exception as e:  # noqa: BLE001
            print(f"[placer] preview skipped ({e!r})"[:160])
    return {
        "assetId": hero.get("assetId", "hero"),
        "anchor": anchor,
        "default": default,
        "variants": variants,
        "support": {"cls": anchor, "center_m": (support or {}).get("center_m"),
                    "size_m": (support or {}).get("size_m"),
                    "height_m": (support or {}).get("height_m")} if support else None,
        "obstacles": len(obstacles),
        "preview": preview,
        "diagnostics": {"source": room.get("source", "?"), "coverage": coverage,
                        "floor_y": round(floor_y, 4)},
    }


def main():
    spec = json.loads(Path(sys.argv[sys.argv.index("--") + 1]).read_text(encoding="utf-8"))
    try:
        result = place(spec)
        err = None
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        result, err = None, str(e)[:400]
    Path(spec["result_path"]).write_text(
        json.dumps({"done": result, "error": err}, indent=2), encoding="utf-8")
    print(f"[placer] {'ok: ' + result['default'] + ' default' if result else 'FAILED: ' + str(err)}")


main()
