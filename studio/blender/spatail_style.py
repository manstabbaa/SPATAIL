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
changed = {"beveled": 0, "matted": 0, "smoothed": 0, "textured_preserved": 0}
meshes = []


def _is_textured(bsdf):
    """A material with ANY image texture wired into the BSDF is a real texture pack
    (e.g. a Meshy PBR asset) — the master material must use it AS-IS, never clay it."""
    for inp in bsdf.inputs:
        if inp.is_linked:
            for ln in inp.links:
                n = ln.from_node
                if n.type in ('TEX_IMAGE', 'TEX_ENVIRONMENT') or (
                        n.type == 'NORMAL_MAP') or n.bl_idname == 'ShaderNodeTexImage':
                    return True
    return False
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
            # Seamless Meshy ingest: a textured PBR material is left exactly as authored
            # (its albedo/normal/roughness-metallic/emissive maps ARE the look). The clay
            # matte pass only restyles flat, untextured authored materials.
            if _is_textured(bsdf):
                changed["textured_preserved"] += 1
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
highlight_regions = []          # the named meshes a step.effect can target individually
seen = set()


def _bsdf(mat):
    return mat.node_tree.nodes.get("Principled BSDF") if (mat and mat.use_nodes) else None


def _linked(bsdf, key):
    return bool(bsdf and key in bsdf.inputs and bsdf.inputs[key].is_linked)


def _emission_of(mat):
    """Read (emissive_bool, [r,g,b,a], strength) tolerantly across Blender versions.
    Trusts the SpatailUber `spatail_emissive` tag, a linked emission MAP (Meshy), or a
    non-zero flat emission."""
    if not mat:
        return (False, [0.0, 0.0, 0.0, 1.0], 0.0)
    tagged = bool(mat.get("spatail_emissive", False))
    col = [0.0, 0.0, 0.0, 1.0]
    strength = 0.0
    bsdf = _bsdf(mat)
    linked_em = False
    if bsdf:
        if "Emission Strength" in bsdf.inputs:
            try: strength = float(bsdf.inputs["Emission Strength"].default_value)
            except Exception: pass
        for key in ("Emission Color", "Emission"):
            if key in bsdf.inputs:
                linked_em = linked_em or _linked(bsdf, key)
                try: col = [round(float(c), 3) for c in bsdf.inputs[key].default_value]
                except Exception: pass
                break
    is_em = tagged or linked_em or (strength > 0.001 and any(c > 0.001 for c in col[:3]))
    return (is_em, col, round(strength, 3))


def _maps_of(mat):
    """Which texture-pack channels are wired into the material (Meshy ingest). Returns
    the list of present maps + a `textured` flag — so the manifest advertises that this
    material carries its OWN PBR maps and must be used as-is, not restyled."""
    bsdf = _bsdf(mat)
    if not bsdf:
        return (False, [])
    probe = [("baseColor", "Base Color"), ("roughness", "Roughness"),
             ("metallic", "Metallic"), ("normal", "Normal"),
             ("emissive", "Emission Color"), ("emissive", "Emission"),
             ("alpha", "Alpha")]
    maps = []
    for label, key in probe:
        if _linked(bsdf, key) and label not in maps:
            maps.append(label)
    return (len(maps) > 0, maps)


if root:
    for o in root.children_recursive:
        if o.type == 'MESH':
            loc = o.matrix_world.translation
            dim = o.dimensions
            slot_mats = [s.material for s in o.material_slots if s.material]
            part_emissive = False
            part_textured = False
            for m in slot_mats:
                em, ecol, estr = _emission_of(m)
                part_emissive = part_emissive or em
                part_textured = part_textured or _maps_of(m)[0]
                if m.name not in seen:
                    seen.add(m.name)
                    bc = [0.8, 0.8, 0.8, 1.0]
                    bsdf = _bsdf(m)
                    if bsdf and "Base Color" in bsdf.inputs:
                        bc = [round(c, 3) for c in bsdf.inputs["Base Color"].default_value]
                    textured, maps = _maps_of(m)
                    materials.append({"name": m.name, "baseColor": bc,
                                      "emissive": em, "emissionColor": ecol,
                                      "emissionStrength": estr,
                                      "textured": textured, "maps": maps})
            # geometry facts per part, in the asset's RENDERED Y-up frame
            # (USDZ export turns Blender +Z-up into +Y-up: [x, z, -y]).
            parts.append({
                "name": o.name,
                "pivot_m": [round(loc.x, 4), round(loc.z, 4), round(-loc.y, 4)],
                "size_m": [round(dim.x, 4), round(dim.z, 4), round(dim.y, 4)],
                "material": slot_mats[0].name if slot_mats else None,
                "emissive": part_emissive,
                "textured": part_textured,
            })
            # every distinctly-named mesh is an addressable highlight region: the
            # runtime resolves step.target -> this prim/entity by name and applies
            # the step's effect to JUST this sub-region (not the whole object).
            highlight_regions.append({"name": o.name, "emissive": part_emissive,
                                      "textured": part_textured})
        elif o.type == 'EMPTY' and o.name.lower().startswith("socket"):
            w = o.matrix_world.translation
            sockets.append({"name": o.name,
                            "pos_m": [round(w[0], 4), round(w[1], 4), round(w[2], 4)]})

# baked animation clips present in the file (the asset's "states/motions")
clips = []
for a in bpy.data.actions:
    fr = a.frame_range
    clips.append({"name": a.name, "frame_start": round(fr[0], 1), "frame_end": round(fr[1], 1)})

result = {"parts": parts, "sockets": sockets, "materials": materials,
          "clips": clips, "highlightRegions": highlight_regions}
'''
