"""builders.py — detailed, multi-part Blender model builders for the library.

Runs INSIDE Blender (imports bpy). BUILDERS maps a library assetId to a function
that constructs a recognizable, multi-part model (NOT a primitive) in the current
scene and returns the created objects. bake_assets.py calls the matching builder,
then joins + sizes + exports the result to GLB + USDZ. Assets without a builder
fall back to their fallbackPrimitive (correct for things that really are simple
shapes — brick, block, plane).

Style: stylized-but-clearly-detailed — many parts, real proportions, materials.
"""
import bpy
import math
import random

from mathutils import Vector

# ── material + primitive helpers ─────────────────────────────────────────────
def MAT(name, rgba, rough=0.5, metallic=0.0, alpha=1.0, emit=0.0):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes.get("Principled BSDF")
    b.inputs["Base Color"].default_value = rgba
    b.inputs["Roughness"].default_value = rough
    b.inputs["Metallic"].default_value = metallic
    if "Alpha" in b.inputs:
        b.inputs["Alpha"].default_value = alpha
    if emit:
        for e in ("Emission Color", "Emission"):
            if e in b.inputs:
                try: b.inputs[e].default_value = rgba
                except Exception: pass
        if "Emission Strength" in b.inputs:
            b.inputs["Emission Strength"].default_value = emit
    m.diffuse_color = rgba
    if alpha < 1.0:
        try: m.surface_render_method = 'BLENDED'
        except Exception:
            try: m.blend_method = 'BLEND'
            except Exception: pass
    return m


def _fin(o, mat, smooth):
    if mat:
        o.data.materials.append(mat)
    if smooth:
        for p in o.data.polygons:
            p.use_smooth = True
    return o


def box(size, loc=(0, 0, 0), rot=(0, 0, 0), mat=None, bevel=0.0, smooth=False):
    bpy.ops.mesh.primitive_cube_add(size=1, location=loc, rotation=rot)
    o = bpy.context.active_object
    o.scale = size
    if bevel:
        md = o.modifiers.new("bev", "BEVEL"); md.width = bevel; md.segments = 2
    return _fin(o, mat, smooth)


def cyl(r, d, loc=(0, 0, 0), rot=(0, 0, 0), mat=None, verts=28, smooth=True):
    bpy.ops.mesh.primitive_cylinder_add(radius=r, depth=d, location=loc, rotation=rot, vertices=verts)
    return _fin(bpy.context.active_object, mat, smooth)


def cone(r1, r2, d, loc=(0, 0, 0), rot=(0, 0, 0), mat=None, verts=24, smooth=True):
    bpy.ops.mesh.primitive_cone_add(radius1=r1, radius2=r2, depth=d, location=loc, rotation=rot, vertices=verts)
    return _fin(bpy.context.active_object, mat, smooth)


def sph(r, loc=(0, 0, 0), mat=None, seg=28, ring=18, smooth=True):
    bpy.ops.mesh.primitive_uv_sphere_add(radius=r, location=loc, segments=seg, ring_count=ring)
    return _fin(bpy.context.active_object, mat, smooth)


def ico(r, loc=(0, 0, 0), mat=None, subd=3, smooth=True):
    bpy.ops.mesh.primitive_ico_sphere_add(radius=r, subdivisions=subd, location=loc)
    return _fin(bpy.context.active_object, mat, smooth)


def tor(maj, mnr, loc=(0, 0, 0), rot=(0, 0, 0), mat=None, mj=40, mn=14, smooth=True):
    bpy.ops.mesh.primitive_torus_add(major_radius=maj, minor_radius=mnr, location=loc, rotation=rot,
                                     major_segments=mj, minor_segments=mn)
    return _fin(bpy.context.active_object, mat, smooth)


def ellip(rx, ry, rz, loc=(0, 0, 0), rot=(0, 0, 0), mat=None, smooth=True):
    o = sph(1.0, loc, mat, smooth=smooth)
    o.scale = (rx, ry, rz); o.rotation_euler = rot
    return o


# ── reusable sub-assemblies ──────────────────────────────────────────────────
def _gear(r, depth, teeth, hub_r, body_mat, loc=(0, 0, 0), rot=(0, 0, 0)):
    objs = [cyl(r, depth, loc, rot, body_mat)]
    tw = (2 * math.pi * r) / teeth * 0.55
    for i in range(teeth):
        a = i * (2 * math.pi / teeth)
        x = loc[0] + (r + tw * 0.45) * math.cos(a)
        y = loc[1] + (r + tw * 0.45) * math.sin(a)
        objs.append(box((tw, tw * 1.3, depth * 0.96), (x, y, loc[2]), (0, 0, a), body_mat, bevel=tw * 0.1))
    objs.append(cyl(hub_r, depth * 1.25, loc, rot, body_mat))          # hub
    return objs


def _planet(rgba, r=0.5, ring=False, ring_rgba=(0.8, 0.75, 0.55, 1), spot=None, craters=0, rough=0.7):
    surf = MAT("planet", rgba, rough=rough)
    objs = [ico(r, (0, 0, 0), surf, subd=4)]
    if ring:
        rm = MAT("planet_ring", ring_rgba, rough=0.5, alpha=0.85)
        objs.append(tor(r * 1.7, r * 0.04, (0, 0, 0), (math.radians(18), 0, 0), rm, mj=64, mn=4))
        objs[-1].scale = (1, 1, 0.12)
    if spot:
        sm = MAT("planet_spot", spot, rough=0.6)
        objs.append(ellip(r * 0.28, r * 0.18, r * 0.05, (r * 0.55, 0, -r * 0.2), mat=sm))
    for i in range(craters):
        a, b = random.uniform(0, 6.28), random.uniform(-0.8, 0.8)
        p = Vector((math.cos(a) * math.sqrt(1 - b * b), math.sin(a) * math.sqrt(1 - b * b), b)) * r
        objs.append(tor(r * random.uniform(0.06, 0.13), r * 0.02, p, (random.uniform(0, 3), random.uniform(0, 3), 0),
                        MAT("crater", (rgba[0] * 0.7, rgba[1] * 0.7, rgba[2] * 0.7, 1))))
    return objs


# ── concept builders ─────────────────────────────────────────────────────────
def b_engine():
    blk = MAT("eng_block", (0.16, 0.18, 0.21, 1), rough=0.5, metallic=0.8)
    alloy = MAT("eng_alloy", (0.55, 0.57, 0.6, 1), rough=0.35, metallic=0.9)
    chrome = MAT("eng_chrome", (0.8, 0.82, 0.85, 1), rough=0.15, metallic=1.0)
    red = MAT("eng_cover", (0.55, 0.07, 0.07, 1), rough=0.4)
    o = [box((0.7, 0.5, 0.42), (0, 0, 0.34), mat=blk, bevel=0.02),       # block
         box((0.52, 0.44, 0.2), (0, 0, 0.05), mat=blk, bevel=0.03),      # oil pan
         cyl(0.12, 0.06, (0, -0.3, 0.2), (math.radians(90), 0, 0), chrome)]  # crank pulley
    for s in (-1, 1):
        ang = s * math.radians(28)
        o.append(box((0.17, 0.6, 0.12), (s * 0.16, 0, 0.66), (0, ang, 0), red, bevel=0.02))   # valve cover
        for i in range(4):
            y = -0.21 + i * 0.14
            o.append(cyl(0.03, 0.16, (s * 0.06, y, 0.58), (0, ang, 0), alloy))                 # intake runner
            o.append(cyl(0.028, 0.22, (s * 0.34, y, 0.34), (0, s * math.radians(62), 0), chrome))  # header
    o.append(box((0.22, 0.52, 0.1), (0, 0, 0.7), mat=alloy, bevel=0.02))   # intake plenum
    return o


def b_piston():
    al = MAT("piston_al", (0.6, 0.62, 0.66, 1), rough=0.35, metallic=0.9)
    steel = MAT("piston_steel", (0.3, 0.32, 0.35, 1), rough=0.4, metallic=0.8)
    o = [cyl(0.09, 0.13, (0, 0, 0.06), mat=al),                # skirt
         cyl(0.092, 0.012, (0, 0, 0.11), mat=steel),           # ring 1
         cyl(0.092, 0.012, (0, 0, 0.085), mat=steel),          # ring 2
         cyl(0.085, 0.02, (0, 0, 0.135), mat=al)]              # crown
    o.append(cyl(0.018, 0.28, (0, 0, -0.13), mat=steel))       # conrod
    o.append(tor(0.03, 0.012, (0, 0, -0.27), (math.radians(90), 0, 0), steel))  # big end
    return o


def b_crankshaft():
    steel = MAT("crank", (0.45, 0.47, 0.5, 1), rough=0.3, metallic=0.95)
    o = [cyl(0.04, 0.62, (0, 0, 0), (0, math.radians(90), 0), steel)]   # main shaft (along X)
    for i in range(4):
        x = -0.22 + i * 0.15
        ph = i * math.pi / 2
        cy, cz = 0.07 * math.cos(ph), 0.07 * math.sin(ph)
        o.append(cyl(0.045, 0.05, (x, cy, cz), (0, math.radians(90), 0), steel))     # rod journal
        o.append(ellip(0.02, 0.11, 0.11, (x, cy * 0.5, cz * 0.5), mat=steel))         # counterweight
    return o


def b_connecting_rod():
    steel = MAT("rod", (0.5, 0.52, 0.55, 1), rough=0.35, metallic=0.9)
    return [tor(0.03, 0.014, (0, 0, 0.16), (math.radians(90), 0, 0), steel),   # small end
            box((0.03, 0.018, 0.26), (0, 0, 0), mat=steel, bevel=0.006),       # beam
            tor(0.05, 0.018, (0, 0, -0.16), (math.radians(90), 0, 0), steel)]  # big end


def b_camshaft():
    steel = MAT("cam", (0.42, 0.44, 0.47, 1), rough=0.3, metallic=0.95)
    o = [cyl(0.03, 0.6, (0, 0, 0), (0, math.radians(90), 0), steel)]
    for i in range(6):
        x = -0.24 + i * 0.096
        lobe = ellip(0.055, 0.04, 0.02, (x, 0.02, 0), (math.radians(i * 40), 0, 0), mat=steel)
        o.append(lobe)
    return o


def b_cell():
    random.seed(7)
    mem = MAT("cell_membrane", (0.45, 0.72, 0.95, 0.16), rough=0.1, alpha=0.16)
    nuc = MAT("nucleus", (0.62, 0.35, 0.72, 1)); nlo = MAT("nucleolus", (0.4, 0.18, 0.5, 1))
    mito = MAT("mitochondria", (0.9, 0.45, 0.22, 1), rough=0.5); er = MAT("er", (0.3, 0.62, 0.55, 1))
    golg = MAT("golgi", (0.95, 0.78, 0.3, 1)); ribo = MAT("ribosome", (0.25, 0.3, 0.4, 1))
    vac = MAT("vacuole", (0.55, 0.85, 0.95, 0.3), alpha=0.3)
    o = [ico(0.55, (0, 0, 0), mem, subd=4), sph(0.2, (0.06, 0, 0.05), nuc), sph(0.07, (0.1, 0.03, 0.08), nlo)]
    for i in range(6):
        a = i * (2 * math.pi / 6) + 0.4
        loc = (0.32 * math.cos(a), 0.30 * math.sin(a), random.uniform(-0.18, 0.18))
        o.append(ellip(0.045, 0.045, 0.11, loc, (random.uniform(0, 3), random.uniform(0, 3), a), mito))
    for i in range(3):
        o.append(tor(0.26 + i * 0.03, 0.012, (0.06, 0, 0.05 - i * 0.05), mat=er, mj=28, mn=8))
    for i in range(4):
        g = ellip(0.13, 0.09, 0.012, (-0.28, 0.10, -0.06 + i * 0.05), mat=golg); o.append(g)
    for i in range(30):
        p = Vector((random.uniform(-1, 1) for _ in range(3))); p.normalize()
        o.append(ico(0.012, tuple(p * random.uniform(0.30, 0.47)), ribo, subd=1, smooth=False))
    for loc in ((-0.18, -0.22, 0.10), (0.20, -0.10, -0.18)):
        o.append(sph(random.uniform(0.06, 0.09), loc, vac))
    return o


def b_nucleus():
    nuc = MAT("nucleus", (0.6, 0.34, 0.7, 0.55), rough=0.2, alpha=0.55)
    nlo = MAT("nucleolus", (0.4, 0.18, 0.5, 1)); chr = MAT("chromatin", (0.5, 0.25, 0.6, 1))
    o = [ico(0.5, (0, 0, 0), nuc, subd=4), sph(0.16, (0.08, 0.05, 0.05), nlo)]
    random.seed(3)
    for i in range(8):
        p = Vector((random.uniform(-1, 1) for _ in range(3))); p.normalize()
        o.append(cyl(0.02, random.uniform(0.2, 0.4), tuple(p * 0.2),
                     (random.uniform(0, 3), random.uniform(0, 3), 0), chr))
    return o


def b_neuron():
    body = MAT("neuron", (0.85, 0.72, 0.45, 1), rough=0.5)
    o = [ico(0.16, (0, 0, 0), body, subd=3)]
    random.seed(5)
    for i in range(7):                                   # dendrites
        a = i * (2 * math.pi / 7)
        d = Vector((math.cos(a), math.sin(a), random.uniform(-0.4, 0.6))).normalized()
        o.append(cone(0.03, 0.006, 0.32, tuple(d * 0.26), (0, 0, 0), body))
        o[-1].rotation_euler = d.to_track_quat('Z', 'Y').to_euler()
    axon = cone(0.035, 0.02, 0.9, (0, 0, -0.5), (math.radians(180), 0, 0), body)  # axon
    o.append(axon)
    for i in range(3):                                   # terminals
        o.append(cone(0.02, 0.004, 0.12, (random.uniform(-0.1, 0.1), random.uniform(-0.1, 0.1), -0.96), mat=body))
    return o


def b_dna():
    bb1 = MAT("dna_bb1", (0.20, 0.55, 0.85, 1), rough=0.4); bb2 = MAT("dna_bb2", (0.85, 0.40, 0.30, 1), rough=0.4)
    rung = [MAT("dna_a", (0.3, 0.7, 0.4, 1)), MAT("dna_t", (0.9, 0.8, 0.3, 1)),
            MAT("dna_c", (0.4, 0.5, 0.85, 1)), MAT("dna_g", (0.8, 0.4, 0.6, 1))]
    o = []
    n = 22
    for t in range(n):
        z = -0.5 + t * (1.0 / (n - 1)); a = t * 0.55
        p1 = Vector((0.18 * math.cos(a), 0.18 * math.sin(a), z))
        p2 = Vector((0.18 * math.cos(a + math.pi), 0.18 * math.sin(a + math.pi), z))
        o.append(sph(0.05, tuple(p1), bb1, seg=12, ring=8))
        o.append(sph(0.05, tuple(p2), bb2, seg=12, ring=8))
        if t % 2 == 0:
            mid = (p1 + p2) / 2
            o.append(cyl(0.018, (p1 - p2).length, tuple(mid),
                         (p1 - p2).to_track_quat('Z', 'Y').to_euler(), rung[(t // 2) % 4]))
    return o


def b_chromosome():
    m = MAT("chromosome", (0.55, 0.3, 0.65, 1), rough=0.5)
    o = []
    for sx, sz in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
        o.append(ellip(0.05, 0.05, 0.18, (sx * 0.06, 0, sz * 0.2), (0, sx * sz * math.radians(18), 0), m))
    o.append(sph(0.07, (0, 0, 0), m))                    # centromere
    return o


def b_heart():
    m = MAT("heart", (0.78, 0.16, 0.20, 1), rough=0.4)
    art = MAT("artery", (0.85, 0.4, 0.35, 1), rough=0.4); vein = MAT("vein", (0.35, 0.4, 0.75, 1), rough=0.4)
    o = [ellip(0.22, 0.20, 0.26, (0, 0, 0), mat=m),                       # main mass
         ellip(0.13, 0.12, 0.16, (-0.12, 0.02, 0.06), mat=m),            # right bulge
         ellip(0.13, 0.12, 0.16, (0.12, 0.02, 0.06), mat=m),             # left bulge
         cone(0.1, 0.02, 0.12, (0.02, 0, -0.24), mat=m)]                 # apex
    o.append(cyl(0.05, 0.22, (-0.06, -0.02, 0.30), (math.radians(12), 0, 0), art))   # aorta
    o.append(cyl(0.045, 0.18, (0.08, -0.02, 0.28), (math.radians(-14), 0, 0), vein))  # pulmonary
    o.append(tor(0.06, 0.022, (-0.06, -0.02, 0.42), (math.radians(80), 0, 0), art))   # arch
    return o


def b_atom():
    pro = MAT("proton", (0.85, 0.30, 0.25, 1), rough=0.4); neu = MAT("neutron", (0.5, 0.52, 0.55, 1), rough=0.4)
    elec = MAT("electron", (0.25, 0.55, 0.95, 1), emit=1.5)
    shell = MAT("shell", (0.5, 0.7, 0.95, 0.5), alpha=0.5)
    o = []
    random.seed(2)
    for i in range(12):                                   # nucleus cluster
        p = Vector((random.uniform(-1, 1) for _ in range(3))).normalized() * random.uniform(0, 0.12)
        o.append(sph(0.06, tuple(p), pro if i % 2 else neu, seg=16, ring=10))
    for k, tilt in enumerate((0, 60, 120)):               # electron shells + electrons
        rr = 0.32 + k * 0.12
        o.append(tor(rr, 0.006, (0, 0, 0), (math.radians(70), 0, math.radians(tilt)), shell, mj=48, mn=6))
        ea = k * 1.7
        o.append(sph(0.03, (rr * math.cos(ea), rr * math.sin(ea) * 0.3, rr * math.sin(ea)), elec, seg=12, ring=8))
    return o


def b_electron_orbit():
    shell = MAT("shell", (0.5, 0.7, 0.95, 0.7), alpha=0.7); elec = MAT("electron", (0.25, 0.55, 0.95, 1), emit=1.5)
    nuc = MAT("nuc", (0.85, 0.4, 0.3, 1))
    return [sph(0.08, (0, 0, 0), nuc), tor(0.45, 0.008, (0, 0, 0), (math.radians(72), 0, 0), shell, mj=56, mn=6),
            sph(0.04, (0.45, 0, 0), elec)]


def b_virus():
    body = MAT("virus", (0.6, 0.35, 0.7, 1), rough=0.5); spike = MAT("spike", (0.9, 0.55, 0.3, 1))
    o = [ico(0.22, (0, 0, 0), body, subd=3)]
    random.seed(9)
    for i in range(22):
        p = Vector((random.uniform(-1, 1) for _ in range(3))).normalized()
        o.append(cyl(0.012, 0.1, tuple(p * 0.27), p.to_track_quat('Z', 'Y').to_euler(), spike))
        o.append(sph(0.025, tuple(p * 0.34), spike, seg=10, ring=6))
    return o


def b_planet(rgba, **kw): return _planet(rgba, **kw)
def b_sun():
    s = MAT("sun", (1.0, 0.62, 0.12, 1), emit=3.0, rough=1.0)
    cor = MAT("corona", (1.0, 0.75, 0.25, 0.25), emit=1.2, alpha=0.25)
    o = [ico(0.5, (0, 0, 0), s, subd=4)]
    d = o[0].modifiers.new("disp", "DISPLACE")
    tx = bpy.data.textures.new("sun_t", "CLOUDS"); tx.noise_scale = 0.35; d.texture = tx; d.strength = 0.05
    o.append(ico(0.62, (0, 0, 0), cor, subd=3))
    return o


def b_gear(r=0.4, teeth=14, depth=0.12):
    mat = MAT("gear", (0.5, 0.52, 0.56, 1), rough=0.35, metallic=0.9)
    o = _gear(r, depth, teeth, r * 0.22, mat)
    for i in range(5):                                    # spokes
        a = i * (2 * math.pi / 5)
        o.append(box((r * 0.08, r * 1.3, depth * 0.5), (0, 0, 0), (0, 0, a), mat))
    return o


def b_apple():
    skin = MAT("apple", (0.80, 0.12, 0.12, 1), rough=0.25); stem = MAT("stem", (0.35, 0.22, 0.1, 1), rough=0.7)
    leaf = MAT("leaf", (0.3, 0.55, 0.2, 1), rough=0.6)
    o = [ellip(0.09, 0.09, 0.082, (0, 0, 0.08), mat=skin)]
    o[0].modifiers.new("sub", "SUBSURF").levels = 1
    o.append(cyl(0.006, 0.05, (0.005, 0, 0.17), (math.radians(8), 0, 0), stem))
    lf = ellip(0.03, 0.012, 0.05, (0.04, 0, 0.18), (0, math.radians(40), 0), mat=leaf); o.append(lf)
    return o


def b_pendulum():
    frame = MAT("frame", (0.3, 0.32, 0.35, 1), rough=0.4, metallic=0.7); rod = MAT("rod", (0.6, 0.62, 0.65, 1), metallic=0.9)
    bob = MAT("bob", (0.75, 0.6, 0.2, 1), rough=0.3, metallic=0.8)
    o = [box((0.4, 0.06, 0.04), (0, 0, 0.78), mat=frame, bevel=0.01),     # top bar
         cyl(0.025, 0.78, (0.16, 0, 0.39), mat=frame), cyl(0.025, 0.78, (-0.16, 0, 0.39), mat=frame)]
    o.append(cyl(0.012, 0.62, (0, 0, 0.42), mat=rod))                     # rod
    o.append(sph(0.09, (0, 0, 0.09), bob))                                # bob
    o.append(cyl(0.03, 0.07, (0, 0, 0.78), (math.radians(90), 0, 0), frame))  # pivot
    return o


def b_lever():
    bar = MAT("bar", (0.55, 0.4, 0.25, 1), rough=0.6); ful = MAT("fulcrum", (0.4, 0.42, 0.45, 1), rough=0.4, metallic=0.7)
    wt = MAT("weight", (0.3, 0.32, 0.35, 1), rough=0.4, metallic=0.8)
    o = [box((0.6, 0.05, 0.03), (0, 0, 0.16), (0, math.radians(-6), 0), bar, bevel=0.008),  # plank
         cone(0.09, 0.0, 0.16, (0, 0, 0.08), mat=ful)]                    # fulcrum
    o.append(box((0.08, 0.08, 0.08), (-0.26, 0, 0.23), mat=wt, bevel=0.006))  # load
    return o


def b_fulcrum():
    return [cone(0.12, 0.0, 0.18, (0, 0, 0.09), mat=MAT("fulcrum", (0.4, 0.42, 0.45, 1), rough=0.4, metallic=0.7), verts=3)]


def b_ramp():
    m = MAT("ramp", (0.55, 0.45, 0.32, 1), rough=0.7)
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 0.25))
    o = bpy.context.active_object; o.scale = (0.4, 0.3, 0.5)
    # shear into a wedge
    import bmesh
    me = o.data
    for v in me.vertices:
        if v.co.z > 0:
            v.co.x = -0.5
    _fin(o, m, False)
    return [o]


def b_beaker():
    g = MAT("glass", (0.7, 0.85, 0.9, 0.2), rough=0.05, alpha=0.2); liq = MAT("liquid", (0.3, 0.6, 0.85, 0.6), alpha=0.6)
    return [cyl(0.09, 0.16, (0, 0, 0.08), mat=g),
            cyl(0.085, 0.08, (0, 0, 0.05), mat=liq),
            cyl(0.095, 0.01, (0, 0, 0.16), mat=g),               # rim
            box((0.03, 0.02, 0.04), (0.09, 0, 0.13), mat=g)]     # spout


def b_prism():
    m = MAT("prism", (0.8, 0.85, 0.95, 0.35), rough=0.05, alpha=0.35)
    return [cone(0.12, 0.12, 0.3, (0, 0, 0.15), (math.radians(90), 0, 0), m, verts=3)]


def b_bridge_pillar():
    con = MAT("concrete", (0.7, 0.69, 0.66, 1), rough=0.8)
    return [cone(0.13, 0.1, 1.0, (0, 0, 0.5), mat=con), box((0.36, 0.36, 0.08), (0, 0, 1.0), mat=con, bevel=0.01),
            box((0.4, 0.4, 0.1), (0, 0, 0.04), mat=con)]


def b_bridge_deck():
    asph = MAT("asphalt", (0.22, 0.23, 0.25, 1), rough=0.9); rail = MAT("rail", (0.55, 0.56, 0.6, 1), metallic=0.7)
    line = MAT("line", (0.9, 0.85, 0.3, 1), emit=0.2)
    o = [box((1.5, 0.42, 0.06), (0, 0, 0), mat=asph, bevel=0.01)]
    for s in (-1, 1):
        o.append(box((1.5, 0.02, 0.08), (0, s * 0.2, 0.06), mat=rail))
        for i in range(8):
            o.append(cyl(0.008, 0.1, (-0.65 + i * 0.18, s * 0.2, 0.08), mat=rail))
    o.append(box((1.4, 0.02, 0.005), (0, 0, 0.032), mat=line))
    return o


def b_cable_stayed_tower():
    steel = MAT("tower", (0.7, 0.71, 0.74, 1), rough=0.4, metallic=0.7); cab = MAT("cable", (0.3, 0.32, 0.35, 1), metallic=0.8)
    o = [cyl(0.05, 1.5, (-0.12, 0, 0.75), (0, math.radians(8), 0), steel),
         cyl(0.05, 1.5, (0.12, 0, 0.75), (0, math.radians(-8), 0), steel),
         box((0.34, 0.08, 0.06), (0, 0, 1.1), mat=steel)]
    for i in range(5):
        x = 0.2 + i * 0.18
        o.append(cyl(0.006, math.hypot(x, 1.3), (x / 2, 0, 0.78),
                     (0, math.atan2(x, 1.3), 0), cab))
        o.append(cyl(0.006, math.hypot(x, 1.3), (-x / 2, 0, 0.78), (0, -math.atan2(x, 1.3), 0), cab))
    return o


def b_truss():
    s = MAT("truss", (0.55, 0.4, 0.3, 1), rough=0.6, metallic=0.5)
    o = [box((0.9, 0.04, 0.04), (0, 0, 0.3), mat=s), box((0.9, 0.04, 0.04), (0, 0, 0), mat=s)]   # chords
    for i in range(5):
        x = -0.36 + i * 0.18
        o.append(cyl(0.018, 0.3, (x, 0, 0.15), mat=s))                     # verticals
        if i < 4:
            o.append(cyl(0.014, math.hypot(0.18, 0.3), (x + 0.09, 0, 0.15),
                         (0, math.atan2(0.18, 0.3), 0), s))                 # diagonals
    return o


def b_column():
    st = MAT("stone", (0.82, 0.80, 0.74, 1), rough=0.8)
    o = [cyl(0.13, 0.84, (0, 0, 0.5), mat=st), cyl(0.17, 0.08, (0, 0, 0.04), mat=st),   # shaft + base
         cyl(0.17, 0.08, (0, 0, 0.96), mat=st)]                                          # capital
    for i in range(12):                                                                  # flutes
        a = i * (2 * math.pi / 12)
        o.append(cyl(0.012, 0.8, (0.12 * math.cos(a), 0.12 * math.sin(a), 0.5), mat=st))
    return o


def b_ibeam():
    s = MAT("steel", (0.55, 0.57, 0.6, 1), rough=0.4, metallic=0.85)
    return [box((0.14, 1.0, 0.02), (0, 0, 0.09), mat=s), box((0.02, 1.0, 0.16), (0, 0, 0), mat=s),
            box((0.14, 1.0, 0.02), (0, 0, -0.09), mat=s)]


def b_colosseum():
    stone = MAT("stone", (0.80, 0.74, 0.62, 1), rough=0.85); floor = MAT("arena", (0.62, 0.5, 0.36, 1), rough=0.9)
    o = []
    for tier in range(3):                                  # 3 arched wall tiers (elliptical)
        z = 0.12 + tier * 0.22; rx, ry = 0.55 - tier * 0.03, 0.42 - tier * 0.03
        n = 28
        for i in range(n):
            a = i * (2 * math.pi / n)
            o.append(box((0.045, 0.045, 0.2), (rx * math.cos(a), ry * math.sin(a), z), (0, 0, a), stone))
        ring = tor(1.0, 0.025, (0, 0, z + 0.1), mat=stone, mj=64, mn=6); ring.scale = (rx, ry, 1)
        o.append(ring)
    for k in range(5):                                     # stepped seating
        rx, ry, z = 0.46 - k * 0.07, 0.34 - k * 0.05, 0.05 + k * 0.03
        seat = tor(1.0, 0.03, (0, 0, z), mat=stone, mj=56, mn=6); seat.scale = (rx, ry, 0.6)
        o.append(seat)
    fl = ellip(0.26, 0.18, 0.02, (0, 0, 0.02), mat=floor); o.append(fl)   # arena floor
    return o


def b_skyscraper():
    glass = MAT("glass", (0.35, 0.5, 0.62, 1), rough=0.15, metallic=0.5); frame = MAT("frame", (0.6, 0.62, 0.66, 1), metallic=0.8)
    o = []
    widths = [(0.3, 0.0, 0.6), (0.24, 0.6, 0.5), (0.18, 1.1, 0.4)]        # setbacks
    for w, z0, h in widths:
        o.append(box((w, w, h), (0, 0, z0 + h / 2), mat=glass, bevel=0.005))
        floors = int(h / 0.06)
        for f in range(floors):                            # floor bands
            o.append(box((w * 1.01, w * 1.01, 0.008), (0, 0, z0 + f * 0.06 + 0.03), mat=frame))
    o.append(cyl(0.01, 0.2, (0, 0, 1.6), mat=frame))                      # spire
    return o


def b_weight():
    return [box((0.16, 0.16, 0.16), (0, 0, 0.08), mat=MAT("weight", (0.28, 0.3, 0.34, 1), rough=0.4, metallic=0.85), bevel=0.01)]


def b_spring():
    m = MAT("spring", (0.6, 0.62, 0.66, 1), metallic=0.9, rough=0.3)
    return [tor(0.045, 0.008, (0, 0, -0.1 + i * 0.026), mat=m, mj=20, mn=6) for i in range(8)]


def b_spark_plug():
    cer = MAT("ceramic", (0.9, 0.88, 0.82, 1), rough=0.4); met = MAT("metal", (0.6, 0.62, 0.66, 1), metallic=0.95, rough=0.3)
    return [cyl(0.025, 0.12, (0, 0, 0.1), mat=cer), cyl(0.03, 0.05, (0, 0, 0.025), mat=met),
            cyl(0.018, 0.04, (0, 0, -0.02), mat=met), cyl(0.004, 0.03, (0, 0, -0.05), mat=met)]


def b_turbine():
    m = MAT("turbine", (0.6, 0.62, 0.66, 1), metallic=0.95, rough=0.3)
    o = [cyl(0.06, 0.04, (0, 0, 0), mat=m)]
    for i in range(11):
        a = i * (2 * math.pi / 11)
        bl = box((0.012, 0.05, 0.04), (0.07 * math.cos(a), 0.07 * math.sin(a), 0), (math.radians(25), 0, a), m)
        o.append(bl)
    return o


# ── registry (assetId -> builder) ───────────────────────────────────────────
BUILDERS = {
    "engine_block_simplified": b_engine, "cylinder_block_simplified": b_engine,
    "piston": b_piston, "crankshaft": b_crankshaft, "connecting_rod": b_connecting_rod,
    "camshaft": b_camshaft, "gear_small": lambda: b_gear(0.3, 12, 0.1),
    "gear_medium": lambda: b_gear(0.42, 16, 0.12), "gear_large": lambda: b_gear(0.6, 20, 0.14),
    "gear_pair": lambda: b_gear(0.4, 14, 0.12) + [o for o in _gear_offset()],
    "turbine_wheel": b_turbine, "fan_blade": b_turbine, "spark_plug": b_spark_plug, "spring": b_spring,
    "generic_cell": b_cell, "nucleus": b_nucleus, "neuron": b_neuron, "dna_double_helix": b_dna,
    "chromosome": b_chromosome, "heart_simplified": b_heart, "virus_particle": b_virus,
    "atom_nucleus": b_atom, "electron_orbit": b_electron_orbit,
    "sun": b_sun, "earth": lambda: _planet((0.18, 0.42, 0.75, 1), craters=0, spot=(0.25, 0.55, 0.3, 1)),
    "moon": lambda: _planet((0.62, 0.62, 0.64, 1), r=0.4, craters=6),
    "mars": lambda: _planet((0.72, 0.34, 0.22, 1), r=0.4, craters=3),
    "jupiter": lambda: _planet((0.78, 0.66, 0.5, 1), spot=(0.7, 0.3, 0.2, 1)),
    "saturn": lambda: _planet((0.84, 0.76, 0.55, 1), ring=True),
    "mercury": lambda: _planet((0.58, 0.56, 0.53, 1), r=0.35, craters=5),
    "venus": lambda: _planet((0.86, 0.76, 0.5, 1)), "uranus": lambda: _planet((0.6, 0.82, 0.85, 1)),
    "neptune": lambda: _planet((0.25, 0.4, 0.82, 1)), "asteroid": lambda: _planet((0.45, 0.43, 0.4, 1), r=0.3, craters=4),
    "apple": b_apple, "pendulum": b_pendulum, "lever": b_lever, "fulcrum": b_fulcrum,
    "ramp": b_ramp, "inclined_plane": b_ramp, "beaker": b_beaker, "prism": b_prism, "weight_block": b_weight,
    "bridge_pillar": b_bridge_pillar, "bridge_deck": b_bridge_deck, "cable_stayed_tower": b_cable_stayed_tower,
    "truss_segment": b_truss, "column": b_column, "beam": b_ibeam, "steel_i_beam": b_ibeam,
    "colosseum": b_colosseum, "skyscraper": b_skyscraper,
}


def _gear_offset():
    o = b_gear(0.3, 12, 0.1)
    for x in o:
        x.location.x += 0.62
    return o
