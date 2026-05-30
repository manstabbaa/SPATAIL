"""realworld.py - a library of RECOGNIZABLE real-world objects, modeled in Blender.

The SPATAIL Studio rule: we never stand a bare grey box in for a real thing. A
demo about inertia shows a puck on an *air-hockey table*; F=ma rolls a ball down
a *wooden ramp on a stand*; action-reaction fires two *lab carts with wheels*
apart with a *coil spring*. Primitives are only the raw clay — every builder here
composes, proportions, and materials them into something a person recognizes.

All geometry is built with bmesh (context-free, deterministic in --background) in
Blender-native metres, +Z up. Builders create their parts parented to a passed
`root` and return a dict including handles to any MOVABLE part the caller will
keyframe, plus footprint_w / min_z / max_z for the Developer's staging.

There is NO primitive-fallback path. If Blender can't build it, that's an error
to fix — not a box to ship.
"""
import bmesh
import bpy
import math
from mathutils import Matrix, Vector

# --- materials ----------------------------------------------------------------
_MATS = {}


def material(name, rgba, rough=0.6, metal=0.0, emit=None, emit_str=1.0):
    if name in _MATS:
        return _MATS[name]
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes.get("Principled BSDF")
    if b:
        b.inputs["Base Color"].default_value = rgba
        if "Roughness" in b.inputs:
            b.inputs["Roughness"].default_value = rough
        if "Metallic" in b.inputs:
            b.inputs["Metallic"].default_value = metal
        if emit and "Emission Color" in b.inputs:
            b.inputs["Emission Color"].default_value = emit
            if "Emission Strength" in b.inputs:
                b.inputs["Emission Strength"].default_value = emit_str
    m.diffuse_color = rgba
    _MATS[name] = m
    return m


PALETTE = {
    "table_top": ((0.92, 0.93, 0.96, 1), 0.18, 0.0),     # glossy white rink
    "table_rim": ((0.13, 0.16, 0.22, 1), 0.4, 0.0),
    "table_leg": ((0.20, 0.22, 0.26, 1), 0.5, 0.3),
    "table_mark": ((0.78, 0.20, 0.22, 1), 0.4, 0.0),     # red rink lines
    "puck": ((0.07, 0.08, 0.10, 1), 0.25, 0.1),          # black glossy puck
    "wood": ((0.62, 0.42, 0.23, 1), 0.55, 0.0),          # ramp plank
    "wood_dark": ((0.45, 0.30, 0.16, 1), 0.6, 0.0),      # stand
    "steel_ball": ((0.80, 0.82, 0.86, 1), 0.22, 0.35),   # ball bearing
    "cart_red": ((0.86, 0.26, 0.22, 1), 0.45, 0.0),
    "cart_blue": ((0.20, 0.45, 0.85, 1), 0.45, 0.0),
    "wheel": ((0.10, 0.11, 0.13, 1), 0.6, 0.0),
    "hub": ((0.75, 0.76, 0.80, 1), 0.3, 0.4),
    "spring": ((0.78, 0.80, 0.85, 1), 0.3, 0.25),        # bright steel coil
}


def _m(key):
    rgba, rough, metal = PALETTE[key]
    return material(f"rw_{key}", rgba, rough=rough, metal=metal)


# --- bmesh primitives (the clay) ----------------------------------------------

def _box(name, sx, sy, sz):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=Vector((sx, sy, sz)), verts=bm.verts)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me); bm.free()
    return me


def _cylinder(name, radius, depth, axis="z", seg=40, smooth=True):
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=seg,
                          radius1=radius, radius2=radius, depth=depth)
    if axis == "x":
        bmesh.ops.rotate(bm, verts=bm.verts, matrix=Matrix.Rotation(math.radians(90), 3, "Y"))
    elif axis == "y":
        bmesh.ops.rotate(bm, verts=bm.verts, matrix=Matrix.Rotation(math.radians(90), 3, "X"))
    me = bpy.data.meshes.new(name)
    if smooth:
        for f in bm.faces:
            if len(f.verts) > 4:
                f.smooth = True
    bm.to_mesh(me); bm.free()
    return me


def _sphere(name, radius, subd=3):
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=subd, radius=radius)
    for f in bm.faces:
        f.smooth = True
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me); bm.free()
    return me


def _wedge(name, length, height, width):
    """A right-triangular prism (a ramp support): rises from 0 at +x/2 to
    `height` at -x/2, depth `width` along y. Origin at geometric centre-ish."""
    hl, hw = length / 2.0, width / 2.0
    v = [(-hl, -hw, 0), (hl, -hw, 0), (hl, hw, 0), (-hl, hw, 0),       # base
         (-hl, -hw, height), (-hl, hw, height)]                        # tall edge
    f = [(0, 1, 2, 3), (0, 4, 5, 3), (1, 2, 5, 4), (0, 1, 4), (3, 2, 5)]
    me = bpy.data.meshes.new(name)
    me.from_pydata([Vector(p) for p in v], [], [list(x) for x in f])
    me.update()
    return me


def _helix_tube(name, length, coil_r, wire_r, turns, axis_seg=12, ring_seg=10):
    """A real coil spring: a tube swept along a helix using parallel-transport
    frames so the wire stays round. Coil axis = +X, centred on origin."""
    n = max(8, int(turns * axis_seg))
    pts, tans = [], []
    for i in range(n + 1):
        t = i / n
        ang = 2 * math.pi * turns * t
        x = (t - 0.5) * length
        pts.append(Vector((x, coil_r * math.cos(ang), coil_r * math.sin(ang))))
    for i in range(n + 1):
        a = pts[max(0, i - 1)]; b = pts[min(n, i + 1)]
        tans.append((b - a).normalized())
    # parallel transport an initial normal along the curve
    ref = Vector((0, 0, 1))
    nrm = (ref - tans[0] * ref.dot(tans[0]))
    nrm = nrm.normalized() if nrm.length > 1e-6 else Vector((0, 1, 0))
    rings = []
    for i in range(n + 1):
        t = tans[i]
        nrm = (nrm - t * nrm.dot(t))
        nrm = nrm.normalized() if nrm.length > 1e-6 else nrm
        bin=t.cross(nrm)
        ring = []
        for j in range(ring_seg):
            a = 2 * math.pi * j / ring_seg
            off = nrm * (wire_r * math.cos(a)) + bin * (wire_r * math.sin(a))
            ring.append(pts[i] + off)
        rings.append(ring)
    verts, faces = [], []
    for ring in rings:
        verts.extend(ring)
    for i in range(n):
        for j in range(ring_seg):
            a = i * ring_seg + j
            b = i * ring_seg + (j + 1) % ring_seg
            c = (i + 1) * ring_seg + (j + 1) % ring_seg
            d = (i + 1) * ring_seg + j
            faces.append((a, b, c, d))
    me = bpy.data.meshes.new(name)
    me.from_pydata([Vector(v) for v in verts], [], [list(f) for f in faces])
    for poly in me.polygons:
        poly.use_smooth = True
    me.update()
    return me


def _obj(name, mesh, root, loc=(0, 0, 0), rot=(0, 0, 0), mat=None):
    o = bpy.data.objects.new(name, mesh)
    if mat:
        o.data.materials.append(mat)
    bpy.context.scene.collection.objects.link(o)
    o.parent = root
    o.location = Vector(loc)
    o.rotation_euler = Vector(rot)
    return o


def _empty(name, root, loc=(0, 0, 0)):
    e = bpy.data.objects.new(name, None)
    e.empty_display_size = 0.02
    bpy.context.scene.collection.objects.link(e)
    if root is not None:
        e.parent = root
    e.location = Vector(loc)
    return e


# --- real-world objects -------------------------------------------------------

def air_hockey_table(root, pfx, length=0.9, width=0.46, leg=0.34):
    """White glossy rink with a dark rim, red centre line + face-off rings, and
    four legs. Returns the playing-surface top z (local) and footprint."""
    top_t = 0.03
    surf_z = leg + top_t / 2
    _obj(f"{pfx}_top", _box(f"{pfx}_top", length, width, top_t), root,
         loc=(0, 0, surf_z), mat=_m("table_top"))
    # rim: four thin raised borders
    rim_h, rim_t = 0.03, 0.02
    rz = surf_z + top_t / 2 + rim_h / 2
    for nm, sx, sy, lx, ly in [
        ("rim_n", length + rim_t, rim_t, 0, width / 2),
        ("rim_s", length + rim_t, rim_t, 0, -width / 2),
        ("rim_e", rim_t, width, length / 2, 0),
        ("rim_w", rim_t, width, -length / 2, 0)]:
        _obj(f"{pfx}_{nm}", _box(f"{pfx}_{nm}", sx, sy, rim_h), root,
             loc=(lx, ly, rz), mat=_m("table_rim"))
    # red rink markings sitting just above the surface
    mz = surf_z + top_t / 2 + 0.001
    _obj(f"{pfx}_centerline", _box(f"{pfx}_centerline", 0.004, width * 0.9, 0.002),
         root, loc=(0, 0, mz), mat=_m("table_mark"))
    for sx in (-1, 1):
        _obj(f"{pfx}_faceoff_{sx}", _cylinder(f"{pfx}_faceoff_{sx}", width * 0.16, 0.002, axis="z"),
             root, loc=(sx * length * 0.3, 0, mz), mat=_m("table_mark"))
    # legs
    for lx in (-1, 1):
        for ly in (-1, 1):
            _obj(f"{pfx}_leg_{lx}_{ly}", _cylinder(f"{pfx}_leg_{lx}_{ly}", 0.018, leg, axis="z"),
                 root, loc=(lx * (length / 2 - 0.05), ly * (width / 2 - 0.04), leg / 2),
                 mat=_m("table_leg"))
    return {"surface_z": surf_z + top_t / 2, "footprint_w": length + rim_t,
            "min_z": 0.0, "top": surf_z + top_t / 2}


def puck(root, pfx, radius=0.045, surface_z=0.0):
    h = 0.02
    o = _obj(f"{pfx}_puck", _cylinder(f"{pfx}_puck", radius, h, axis="z"),
             root, loc=(0, 0, surface_z + h / 2), mat=_m("puck"))
    return {"obj": o, "z": surface_z + h / 2, "radius": radius, "half_h": h / 2}


def wooden_ramp(root, pfx, length=0.9, angle_deg=22, leg=0.0):
    """A wooden plank tilted on a triangular stand. Returns the slope frame the
    caller rolls a ball down: top point, downhill dir, surface normal."""
    theta = math.radians(angle_deg)
    plank_t = 0.025
    width = 0.18
    # stand: a wedge whose tall edge is at -x, so the plank tilts down toward +x
    stand_h = length * math.sin(theta)
    _obj(f"{pfx}_stand", _wedge(f"{pfx}_stand", length * math.cos(theta) + 0.04, stand_h, width + 0.02),
         root, loc=(0, 0, 0), mat=_m("wood_dark"))
    # plank lying on the wedge hypotenuse
    plank = _obj(f"{pfx}_plank", _box(f"{pfx}_plank", length, width, plank_t),
                 root, loc=(0, 0, stand_h / 2), rot=(0, theta, 0), mat=_m("wood"))
    # side rails so the ball reads as guided
    for sy in (-1, 1):
        _obj(f"{pfx}_rail_{sy}", _box(f"{pfx}_rail_{sy}", length, 0.012, 0.02),
             root, loc=(0, sy * (width / 2), stand_h / 2 + plank_t / 2 + 0.008),
             rot=(0, theta, 0), mat=_m("wood_dark"))
    down = Vector((math.cos(theta), 0.0, -math.sin(theta)))
    normal = Vector((math.sin(theta), 0.0, math.cos(theta)))
    top = Vector((-length / 2 * math.cos(theta), 0.0, stand_h / 2 + length / 2 * math.sin(theta)))
    return {"top": top, "down": down, "normal": normal, "plank_t": plank_t,
            "length": length, "theta": theta, "footprint_w": length * math.cos(theta) + 0.06,
            "min_z": -stand_h / 2, "max_z": top.z}


def ball(root, pfx, radius=0.05):
    o = _obj(f"{pfx}_ball", _sphere(f"{pfx}_ball", radius, subd=3), root, mat=_m("steel_ball"))
    return {"obj": o, "radius": radius}


def lab_cart(root, pfx, color="cart_red", deck=0.13, height=0.05):
    """A small rolling cart: a coloured deck on four black wheels with steel
    hubs. Returns a sub-root EMPTY that the caller moves (wheels + deck ride it),
    plus the wheel objects so the caller can spin them."""
    sub = _empty(f"{pfx}_root", root)
    wheel_r = 0.028
    deck_z = wheel_r + height / 2
    _obj(f"{pfx}_deck", _box(f"{pfx}_deck", deck, deck * 0.8, height), sub,
         loc=(0, 0, deck_z), mat=_m(color))
    # a little post + bumper so carts read as lab carts, not dice
    _obj(f"{pfx}_post", _cylinder(f"{pfx}_post", 0.006, 0.05, axis="z"), sub,
         loc=(0, 0, deck_z + height / 2 + 0.025), mat=_m("hub"))
    wheels = []
    for wx in (-1, 1):
        for wy in (-1, 1):
            w = _obj(f"{pfx}_wheel_{wx}_{wy}", _cylinder(f"{pfx}_wheel_{wx}_{wy}", wheel_r, 0.016, axis="y"),
                     sub, loc=(wx * deck * 0.34, wy * (deck * 0.4 + 0.008), wheel_r),
                     mat=_m("wheel"))
            _obj(f"{pfx}_hub_{wx}_{wy}", _cylinder(f"{pfx}_hub_{wx}_{wy}", wheel_r * 0.35, 0.018, axis="y"),
                 sub, loc=(wx * deck * 0.34, wy * (deck * 0.4 + 0.008), wheel_r), mat=_m("hub"))
            wheels.append(w)
    return {"root": sub, "wheels": wheels, "deck": deck, "wheel_r": wheel_r,
            "top_z": deck_z + height / 2}


def coil_spring(root, pfx, length=0.06, coil_r=0.03, wire_r=0.006, turns=6):
    o = _obj(f"{pfx}_spring", _helix_tube(f"{pfx}_spring", length, coil_r, wire_r, turns, axis="x")
             if False else _helix_tube(f"{pfx}_spring", length, coil_r, wire_r, turns),
             root, mat=_m("spring"))
    return {"obj": o, "length": length, "coil_r": coil_r}
