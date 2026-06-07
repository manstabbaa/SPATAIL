"""spatail_style.py — the SPATAIL asset render style (the "Peter Tarka clay look").

This is an ASSET-GENERATION concern (Pillar 1 / XR Content Design): how every
Blender-authored 3D asset looks. NOT the SPATAIL brand (the spatial UI identity,
supplied separately).

Two reliable, USDZ-surviving moves give the soft pastel-clay aesthetic:
  * MATTE materials   — high roughness, zero metal, soft specular (UsdPreviewSurface
                        carries roughness/metallic/baseColor, so this survives to
                        RealityKit; SSS/clearcoat do not, so we don't rely on them).
  * ROUNDED edges     — a small Bevel modifier + smooth shading (the depsgraph is
                        evaluated on USD export, so the rounded geometry bakes in).

These are applied DETERMINISTICALLY after the LLM authors the shape, so the brand
look is guaranteed even if the model ignores the palette hint. We deliberately do
NOT recolour parts (a banana must stay yellow) — only the FINISH is enforced; the
palette is a *hint* to the author, not a clamp.

Everything here is plain strings (no `bpy` import) so it's safe to import anywhere;
the strings are exec'd inside Blender via the generator's run_code (live socket OR
the per-phone headless executor — both paths get the same pass).
"""
from __future__ import annotations

# SPATAIL pastel-clay palette (warm, desaturated) — a HINT for the LLM author.
PALETTE = [
    ("soft blush",   "#E8B7B1"),
    ("muted blue",   "#9DB9D6"),
    ("sage green",   "#A9C4A0"),
    ("warm sand",    "#E6CDA8"),
    ("clay terracotta", "#D89A78"),
    ("dusty lilac",  "#C3B2D1"),
    ("cream",        "#F0E8DA"),
    ("slate",        "#6E7681"),
]

PALETTE_HINT = "; ".join(f"{name} {hexv}" for name, hexv in PALETTE)

# Bevel size as a fraction of the object's largest dimension (keeps the rounding
# proportional whether the part is 2 cm or 90 cm), clamped to a sane absolute band.
_BEVEL_FRACTION = 0.02
_BEVEL_MIN_M = 0.002
_BEVEL_MAX_M = 0.02

# ── the style pass: run AFTER authoring, BEFORE export ──────────────────────────
STYLE_CODE = r'''
import bpy
from mathutils import Vector

FRAC = %(FRAC)f
BMIN = %(BMIN)f
BMAX = %(BMAX)f

root = bpy.data.objects.get("gen_root")
changed = {"beveled": 0, "matted": 0, "smoothed": 0}
meshes = []
if root:
    for o in root.children_recursive:
        if o.type == 'MESH' and o.data:
            meshes.append(o)

for o in meshes:
    # proportional rounded edges (clay softness) via a Bevel modifier
    try:
        dims = o.dimensions
        longest = max(dims.x, dims.y, dims.z, 1e-4)
        width = min(BMAX, max(BMIN, longest * FRAC))
        bev = next((m for m in o.modifiers if m.type == 'BEVEL'), None)
        if bev is None:
            bev = o.modifiers.new(name="SPATAIL_Bevel", type='BEVEL')
        bev.width = width
        bev.segments = 2
        bev.limit_method = 'ANGLE'
        bev.angle_limit = 0.6109  # ~35 deg: round hard corners, leave smooth faces
        changed["beveled"] += 1
    except Exception as e:
        print("[spatail_style] bevel skip:", e)
    # smooth shading
    try:
        for p in o.data.polygons:
            p.use_smooth = True
        changed["smoothed"] += 1
    except Exception as e:
        print("[spatail_style] smooth skip:", e)
    # matte clay finish (keep the author's colours; only change the FINISH)
    for slot in o.material_slots:
        mat = slot.material
        if not mat:
            continue
        try:
            mat.use_nodes = True
            bsdf = mat.node_tree.nodes.get("Principled BSDF")
            if not bsdf:
                continue
            if "Roughness" in bsdf.inputs:
                bsdf.inputs["Roughness"].default_value = 0.62
            if "Metallic" in bsdf.inputs:
                bsdf.inputs["Metallic"].default_value = 0.0
            # soften reflections (Blender 4.x/5.x renamed Specular -> Specular IOR Level)
            for sname in ("Specular IOR Level", "Specular"):
                if sname in bsdf.inputs:
                    bsdf.inputs[sname].default_value = 0.2
            bc = bsdf.inputs["Base Color"].default_value
            mat.diffuse_color = (bc[0], bc[1], bc[2], bc[3])  # viewport/preview match
            changed["matted"] += 1
        except Exception as e:
            print("[spatail_style] matte skip:", e)

# soft neutral ambient world — for Blender preview + any future AO bake. (RealityKit
# relights with the real room's IBL in AR, so this does not travel; it's harmless.)
try:
    world = bpy.context.scene.world
    if world is None:
        world = bpy.data.worlds.new("SPATAIL_World")
        bpy.context.scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs[0].default_value = (0.92, 0.92, 0.95, 1.0)
        bg.inputs[1].default_value = 0.55
except Exception as e:
    print("[spatail_style] world skip:", e)

result = {"style": "spatail-clay-v1", "changed": changed, "meshes": len(meshes)}
''' % {"FRAC": _BEVEL_FRACTION, "BMIN": _BEVEL_MIN_M, "BMAX": _BEVEL_MAX_M}


# ── manifest introspection: run AFTER export (geometry unchanged) ───────────────
# Produces the ASSET PACKAGE manifest — parts, sockets, materials, clips — the
# foundation the game-manager runtime (Phase 3) and placement (Phase 2) consume.
INTROSPECT_CODE = r'''
import bpy

root = bpy.data.objects.get("gen_root")
parts, sockets, materials = [], [], []
seen = set()
if root:
    for o in root.children_recursive:
        if o.type == 'MESH':
            parts.append(o.name)
            for slot in o.material_slots:
                m = slot.material
                if m and m.name not in seen:
                    seen.add(m.name)
                    bc = [0.8, 0.8, 0.8, 1.0]
                    if m.use_nodes:
                        bsdf = m.node_tree.nodes.get("Principled BSDF")
                        if bsdf and "Base Color" in bsdf.inputs:
                            bc = [round(c, 3) for c in bsdf.inputs["Base Color"].default_value]
                    materials.append({"name": m.name, "baseColor": bc})
        elif o.type == 'EMPTY' and o.name.lower().startswith("socket"):
            w = o.matrix_world.translation
            sockets.append({"name": o.name,
                            "pos_m": [round(w[0], 4), round(w[1], 4), round(w[2], 4)]})

# baked animation clips present in the file (the asset's "states/motions")
clips = []
for a in bpy.data.actions:
    fr = a.frame_range
    clips.append({"name": a.name, "frame_start": round(fr[0], 1), "frame_end": round(fr[1], 1)})

result = {"parts": parts, "sockets": sockets, "materials": materials, "clips": clips}
'''
