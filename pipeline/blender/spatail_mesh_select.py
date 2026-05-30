"""
spatail_mesh_select.py — strong sub-mesh selection for SPATAIL XR authoring.

Two jobs the rest of the pipeline could not do before:

  1. MESH ISOLATOR / FINDER  — given a fuzzy human query ("the lantern", "the
     tower", "left arm"), rank the scene's mesh objects and return the best
     match(es). Whole-mesh granularity.

  2. PER-VERTEX / FACE / EDGE FINDER — given a mesh and a *region spec*, return
     the exact set of vertex / face / edge indices, in the mesh's own frame.
     Region specs are composable geometric predicates plus a semantic phrase
     resolver ("top", "rim", "tip", "outward faces", "the seam"…).

Selection runs in NORMALIZED local-bbox coordinates (0..1 along each axis of
the object's local bounding box) so the same phrase works on a 6 cm fan and a
6 m lighthouse. World-space outputs (centroid, bbox, radius) are also returned
so downstream transport / runtime can place a highlight without the mesh data.

This module is selection only. Transport (sidecar JSON, overlay-mesh bake,
vertex-color attribute) lives in spatail_mesh_region.py and consumes the
`Selection` dicts produced here.

USAGE in Blender:
    import sys; sys.path.insert(0, r"C:/SPATAIL_MAX/pipeline/blender")
    import importlib, spatail_mesh_select as ms; importlib.reload(ms)

    ms.find_mesh("lantern")                       # → ranked candidates
    sel = ms.select_region("LH_Tower", {"axis_band": {"axis": "z", "lo": 0.85}})
    sel = ms.region_from_phrase("LH_Tower", "top rim")
    ms.paint_group("LH_Tower", "spatail.region.top_rim", sel)
"""
import bpy
import bmesh
import math
from mathutils import Vector, Matrix


# ─────────────────────────────────────────────────────────────────────────
# Small utilities
# ─────────────────────────────────────────────────────────────────────────

_AXIS = {"x": 0, "y": 1, "z": 2}
_SKIP_PREFIXES = ("SPATAIL_", "spatail_region__")
_SKIP_NAMES = ("HeroCam", "Camera", "XRExportCam")


def _is_logical_mesh(o):
    if o.type != "MESH":
        return False
    if o.name in _SKIP_NAMES:
        return False
    if any(o.name.startswith(p) for p in _SKIP_PREFIXES):
        return False
    return bool(o.data.vertices)


def _get_mesh(name):
    o = bpy.data.objects.get(name)
    if o is None or o.type != "MESH":
        raise ValueError(f"No mesh object named {name!r}")
    return o


def _local_bbox(obj):
    """Local-space (min, max) corners over the mesh vertices."""
    lo = Vector((float("inf"),) * 3)
    hi = Vector((-float("inf"),) * 3)
    for v in obj.data.vertices:
        lo = Vector(map(min, lo, v.co))
        hi = Vector(map(max, hi, v.co))
    return lo, hi


def _world_bbox(obj):
    lo = Vector((float("inf"),) * 3)
    hi = Vector((-float("inf"),) * 3)
    mw = obj.matrix_world
    for c in obj.bound_box:
        p = mw @ Vector(c)
        lo = Vector(map(min, lo, p))
        hi = Vector(map(max, hi, p))
    return lo, hi


def _norm_coord(co, lo, span):
    """Map a local coordinate into 0..1 per axis (clamped span guards /0)."""
    return Vector((
        (co.x - lo.x) / span.x if span.x > 1e-9 else 0.5,
        (co.y - lo.y) / span.y if span.y > 1e-9 else 0.5,
        (co.z - lo.z) / span.z if span.z > 1e-9 else 0.5,
    ))


def _normal_matrix(obj):
    """3x3 that maps local normals to world (inverse-transpose of upper 3x3)."""
    return obj.matrix_world.to_3x3().inverted_safe().transposed()


# ─────────────────────────────────────────────────────────────────────────
# MESH ISOLATOR / FINDER
# ─────────────────────────────────────────────────────────────────────────

def _tokens(s):
    out = []
    cur = []
    for ch in s.lower():
        if ch.isalnum():
            cur.append(ch)
        else:
            if cur:
                out.append("".join(cur))
                cur = []
    if cur:
        out.append("".join(cur))
    return out


def find_mesh(query, registry=None, limit=5, spatial=None):
    """Rank logical mesh objects against a fuzzy query.

    query     : free text — name fragment, role, or descriptive noun.
    registry  : optional part-registry dict ({"parts": {...}, aliases…}) whose
                role/alias keys map onto mesh names; boosts matches.
    spatial   : optional dict to bias by world position, any of:
                  {"side": "left"|"right"|"top"|"bottom"|"front"|"back"}
                  {"near_world": [x,y,z]}
    Returns a list of dicts sorted best-first:
        {name, score, reasons[], centroid_world, bbox_world, dims, n_verts}
    """
    q = query.strip().lower()
    qtoks = set(_tokens(q))

    # Build alias → mesh-name hints from a registry if given.
    alias_to_name = {}
    if registry:
        for mesh_name, meta in (registry.get("parts") or {}).items():
            role = (meta or {}).get("role")
            if role:
                alias_to_name.setdefault(role.lower(), mesh_name)
            for al in (meta or {}).get("aliases", []) or []:
                alias_to_name.setdefault(al.lower(), mesh_name)

    meshes = [o for o in bpy.data.objects if _is_logical_mesh(o)]
    if not meshes:
        return []

    # Scene world bbox for "side" biasing.
    scene_lo = Vector((float("inf"),) * 3)
    scene_hi = Vector((-float("inf"),) * 3)
    for o in meshes:
        wlo, whi = _world_bbox(o)
        scene_lo = Vector(map(min, scene_lo, wlo))
        scene_hi = Vector(map(max, scene_hi, whi))
    scene_span = scene_hi - scene_lo

    results = []
    for o in meshes:
        wlo, whi = _world_bbox(o)
        centroid = (wlo + whi) * 0.5
        name_l = o.name.lower()
        ntoks = set(_tokens(name_l))
        score = 0.0
        reasons = []

        if q == name_l:
            score += 100; reasons.append("exact name")
        elif q and q in name_l:
            score += 50; reasons.append("name substring")
        shared = qtoks & ntoks
        if shared:
            score += 20 * len(shared); reasons.append(f"token:{'+'.join(sorted(shared))}")
        # data-block name (often the primitive: Sphere, Cylinder…)
        if o.data and q and q in o.data.name.lower():
            score += 8; reasons.append("datablock")
        # registry alias / role
        for al, mesh_name in alias_to_name.items():
            if al in qtoks or al == q:
                if mesh_name == o.name:
                    score += 40; reasons.append(f"alias:{al}")

        if spatial:
            side = spatial.get("side")
            if side and scene_span.length > 1e-9:
                frac = _norm_coord(centroid, scene_lo, scene_span)
                side_score = {
                    "left": 1 - frac.x, "right": frac.x,
                    "front": 1 - frac.y, "back": frac.y,
                    "bottom": 1 - frac.z, "top": frac.z,
                }.get(side)
                if side_score is not None:
                    score += 15 * side_score
                    reasons.append(f"{side}:{side_score:.2f}")
            near = spatial.get("near_world")
            if near is not None:
                d = (centroid - Vector(near)).length
                diag = scene_span.length or 1.0
                score += 15 * max(0.0, 1 - d / diag)
                reasons.append("near")

        if score <= 0:
            continue
        results.append({
            "name": o.name,
            "score": round(score, 2),
            "reasons": reasons,
            "centroid_world": [round(c, 5) for c in centroid],
            "bbox_world": [[round(c, 5) for c in wlo], [round(c, 5) for c in whi]],
            "dims": [round(d, 5) for d in (whi - wlo)],
            "n_verts": len(o.data.vertices),
        })

    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:limit]


# ─────────────────────────────────────────────────────────────────────────
# PER-VERTEX / FACE / EDGE FINDER
# ─────────────────────────────────────────────────────────────────────────
#
# A region spec is a dict of predicates. By default predicates are ANDed over
# vertices ("combine": "all"); set "combine": "any" to OR them. Face / edge
# predicates (normal_dir, material, sharp_edges, boundary) select faces/edges
# directly; the resulting vertices are the union of their corner vertices.
#
# Vertex predicates (normalized 0..1 along the local bbox unless noted):
#   axis_band   {axis:'z', lo:0.0, hi:1.0}            keep verts whose norm
#                                                       coord on axis ∈ [lo,hi]
#   half_space  {axis:'x', side:'+'|'-', at:0.5}      keep one side of a plane
#   sphere      {center_world|center_local:[x,y,z], radius:r}   (world units)
#   box         {lo_norm:[x,y,z], hi_norm:[x,y,z]}    keep verts in norm box
#   radial_band {axis:'z', r_lo:0.0, r_hi:1.0}        distance from the axis
#                                                       through bbox centre,
#                                                       normalized by max radius
#   near_vertex {index:i, radius:r}                   world-radius around a vert
#   linked_from {index:i}  | {near_world:[x,y,z]}     connected-component
#                                                       flood fill from a seed
#
# Face predicates:
#   normal_dir  {dir:[x,y,z], min_dot:0.5}            faces whose WORLD normal
#                                                       aligns with dir
#   material    "MatName"  | {name:"MatName"}         faces using that material
#
# Edge predicates:
#   sharp_edges {angle_deg:30}                        crease edges (face angle)
#   boundary    true                                  open boundary edges
#
# Combination across predicate *kinds*: vertex predicates AND/OR among
# themselves; then if any face predicate is present the face set is intersected
# with "all faces whose verts survived" (so "top + outward faces" works).


def _eval_vertex_predicates(obj, spec, lo, span, restrict=None):
    """Return the set of vertex indices satisfying the vertex-level predicates.

    restrict : optional iterable of vertex indices forming the universe. When
               given, relative predicates (radial_band) normalize over *this*
               subset rather than the whole mesh — so chained phrases like
               "top rim" mean the outer ring **of the top band**, not the outer
               ring of the entire mesh. Absolute predicates (axis_band, box,
               half_space) still reference the global bbox passed in lo/span."""
    verts = obj.data.vertices
    universe = list(range(len(verts)) if restrict is None else restrict)
    combine = spec.get("combine", "all")
    preds = []

    if "axis_band" in spec:
        ab = spec["axis_band"]
        ax = _AXIS[ab["axis"]]
        lo_b = ab.get("lo", 0.0)
        hi_b = ab.get("hi", 1.0)

        def _f(co, _ax=ax, _lo=lo_b, _hi=hi_b):
            t = _norm_coord(co, lo, span)[_ax]
            return _lo <= t <= _hi
        preds.append(_f)

    if "half_space" in spec:
        hs = spec["half_space"]
        ax = _AXIS[hs["axis"]]
        at = hs.get("at", 0.5)
        plus = hs.get("side", "+") == "+"

        def _f(co, _ax=ax, _at=at, _plus=plus):
            t = _norm_coord(co, lo, span)[_ax]
            return (t >= _at) if _plus else (t <= _at)
        preds.append(_f)

    if "box" in spec:
        bx = spec["box"]
        bl = Vector(bx.get("lo_norm", [0, 0, 0]))
        bh = Vector(bx.get("hi_norm", [1, 1, 1]))

        def _f(co, _bl=bl, _bh=bh):
            t = _norm_coord(co, lo, span)
            return (_bl.x <= t.x <= _bh.x and _bl.y <= t.y <= _bh.y
                    and _bl.z <= t.z <= _bh.z)
        preds.append(_f)

    if "sphere" in spec:
        sp = spec["sphere"]
        r = sp["radius"]
        if "center_world" in sp:
            c_local = obj.matrix_world.inverted() @ Vector(sp["center_world"])
        else:
            c_local = Vector(sp.get("center_local", [0, 0, 0]))
        # radius is world units → convert to local via average scale
        scl = obj.matrix_world.to_scale()
        avg = (abs(scl.x) + abs(scl.y) + abs(scl.z)) / 3.0 or 1.0
        r_local = r / avg

        def _f(co, _c=c_local, _r=r_local):
            return (co - _c).length <= _r
        preds.append(_f)

    if "radial_band" in spec:
        rb = spec["radial_band"]
        ax = _AXIS[rb["axis"]]
        centre = (lo + (lo + span)) * 0.5
        # max radius for normalization, measured over the *universe* subset so
        # "rim/outer" is relative to the verts that reached this stage.
        maxr = 1e-9
        for i in universe:
            d = verts[i].co - centre
            d[ax] = 0.0
            maxr = max(maxr, d.length)
        r_lo = rb.get("r_lo", 0.0)
        r_hi = rb.get("r_hi", 1.0)

        def _f(co, _ax=ax, _c=centre, _m=maxr, _lo=r_lo, _hi=r_hi):
            d = co - _c
            d[_ax] = 0.0
            t = d.length / _m
            return _lo <= t <= _hi
        preds.append(_f)

    if "near_vertex" in spec:
        nv = spec["near_vertex"]
        seed = verts[nv["index"]].co.copy()
        scl = obj.matrix_world.to_scale()
        avg = (abs(scl.x) + abs(scl.y) + abs(scl.z)) / 3.0 or 1.0
        r_local = nv["radius"] / avg

        def _f(co, _s=seed, _r=r_local):
            return (co - _s).length <= _r
        preds.append(_f)

    if not preds:
        # No vertex predicate → the whole universe (face/edge preds narrow later).
        base = set(universe)
    else:
        base = set()
        for i in universe:
            v = verts[i]
            hits = [p(v.co) for p in preds]
            keep = all(hits) if combine == "all" else any(hits)
            if keep:
                base.add(i)

    # Connectivity flood fill (applied after, intersected).
    if "linked_from" in spec:
        base &= _linked_component(obj, spec["linked_from"])

    return base


def _linked_component(obj, lf):
    """Set of vertex indices in the connected component of a seed."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    try:
        if "index" in lf:
            seed = bm.verts[lf["index"]]
        else:
            target = obj.matrix_world.inverted() @ Vector(lf["near_world"])
            seed = min(bm.verts, key=lambda v: (v.co - target).length)
        seen = {seed.index}
        stack = [seed]
        while stack:
            v = stack.pop()
            for e in v.link_edges:
                ov = e.other_vert(v)
                if ov.index not in seen:
                    seen.add(ov.index)
                    stack.append(ov)
        return seen
    finally:
        bm.free()


def _eval_face_predicates(obj, spec, surviving_verts):
    """Return (face_indices, edge_pairs) honoring face/edge predicates.

    If face/edge predicates exist, faces are filtered by them AND by lying
    within surviving_verts. If none exist, faces fully inside surviving_verts
    are returned (so a pure vertex-band still yields its faces)."""
    mesh = obj.data
    nmat = _normal_matrix(obj)

    want_normal = "normal_dir" in spec
    want_material = "material" in spec
    want_sharp = "sharp_edges" in spec
    want_boundary = bool(spec.get("boundary"))

    # Material index target
    mat_idx = None
    if want_material:
        mname = spec["material"]
        if isinstance(mname, dict):
            mname = mname.get("name")
        for i, m in enumerate(mesh.materials):
            if m and m.name == mname:
                mat_idx = i
                break

    nd = spec.get("normal_dir") or {}
    nd_vec = Vector(nd.get("dir", [0, 0, 1])).normalized() if want_normal else None
    nd_dot = nd.get("min_dot", 0.5) if want_normal else None

    sv = surviving_verts
    has_vert_filter = len(sv) != len(mesh.vertices)

    faces = []
    for poly in mesh.polygons:
        vi = list(poly.vertices)
        if has_vert_filter and not all(idx in sv for idx in vi):
            # require the whole face inside the vertex region (unless the only
            # filters are face-level — then vertex set is "all" anyway)
            if want_normal or want_material:
                # face-level predicate present: allow if face passes it AND a
                # majority of its verts survive (lets normal/material drive)
                if sum(1 for idx in vi if idx in sv) < max(1, len(vi) - 0):
                    continue
            else:
                continue
        if want_normal:
            wn = (nmat @ poly.normal).normalized()
            if wn.dot(nd_vec) < nd_dot:
                continue
        if want_material and poly.material_index != mat_idx:
            continue
        faces.append(poly.index)

    # Edges
    edges = []
    if want_sharp or want_boundary:
        bm = bmesh.new()
        bm.from_mesh(mesh)
        bm.edges.ensure_lookup_table()
        try:
            ang = math.radians(spec.get("sharp_edges", {}).get("angle_deg", 30)) \
                if want_sharp else None
            for e in bm.edges:
                a, b = e.verts[0].index, e.verts[1].index
                if has_vert_filter and not (a in sv and b in sv):
                    continue
                if want_boundary and e.is_boundary:
                    edges.append((a, b)); continue
                if want_sharp and len(e.link_faces) == 2:
                    if e.calc_face_angle() >= ang:
                        edges.append((a, b))
        finally:
            bm.free()
    else:
        # Derive edges from selected faces (their boundary loop edges).
        face_set = set(faces)
        seen = set()
        for fi in faces:
            poly = mesh.polygons[fi]
            vs = list(poly.vertices)
            for k in range(len(vs)):
                a, b = vs[k], vs[(k + 1) % len(vs)]
                key = (min(a, b), max(a, b))
                if key not in seen:
                    seen.add(key)
                    edges.append((a, b))

    return faces, edges


def select_region(mesh_name, spec, restrict=None):
    """Resolve a region spec on a mesh → exact vertex / face / edge index sets.

    restrict : optional iterable of vertex indices to confine the result to
               (and to normalize relative predicates against). See
               _eval_vertex_predicates / select_pipeline.

    Returns a Selection dict:
        {meshId, vertices:[...], faces:[...], edges:[[a,b],...],
         centroid_world, bbox_world, radius_world,
         counts:{verts,faces,edges}, spec}
    """
    obj = _get_mesh(mesh_name)
    lo, hi = _local_bbox(obj)
    span = hi - lo

    vset = _eval_vertex_predicates(obj, spec, lo, span, restrict=restrict)
    faces, edges = _eval_face_predicates(obj, spec, vset)

    # If face predicates narrowed things, the authoritative vertex set is the
    # union of selected-face corners (keeps verts/faces consistent).
    if faces and ("normal_dir" in spec or "material" in spec):
        fverts = set()
        for fi in faces:
            fverts.update(obj.data.polygons[fi].vertices)
        vset = fverts if not vset or len(vset) == len(obj.data.vertices) else (vset | fverts)

    # World-space summary of the selected vertices.
    mw = obj.matrix_world
    if vset:
        pts = [mw @ obj.data.vertices[i].co for i in vset]
    else:
        pts = [mw @ v.co for v in obj.data.vertices]
    wlo = Vector((float("inf"),) * 3)
    whi = Vector((-float("inf"),) * 3)
    centroid = Vector((0, 0, 0))
    for p in pts:
        wlo = Vector(map(min, wlo, p))
        whi = Vector(map(max, whi, p))
        centroid += p
    centroid /= max(1, len(pts))
    radius = max((p - centroid).length for p in pts) if pts else 0.0

    return {
        "meshId": obj.name,
        "vertices": sorted(vset),
        "faces": sorted(faces),
        "edges": [list(e) for e in edges],
        "centroid_world": [round(c, 6) for c in centroid],
        "bbox_world": [[round(c, 6) for c in wlo], [round(c, 6) for c in whi]],
        "radius_world": round(radius, 6),
        "counts": {"verts": len(vset), "faces": len(faces), "edges": len(edges)},
        "spec": spec,
    }


def select_pipeline(mesh_name, stages):
    """Apply specs sequentially, each narrowing (and re-normalizing relative
    predicates against) the previous survivors. Returns the final Selection.

    Example: [{"box": {...top...}}, {"radial_band": {...outer...}}] →
    the outer ring **of the top band**."""
    current = None
    last = None
    for stage in stages:
        last = select_region(mesh_name, stage, restrict=current)
        current = set(last["vertices"])
    if last is None:
        return select_region(mesh_name, {})
    last["spec"] = {"pipeline": stages}
    return last


# ─────────────────────────────────────────────────────────────────────────
# SEMANTIC PHRASE RESOLVER
# ─────────────────────────────────────────────────────────────────────────
#
# Maps everyday region words onto specs. The mesh's UP axis is assumed Z
# (Blender convention); pass up="y" to override. Phrases compose: "top rim",
# "front face", "outer shell".

_BANDS = {  # phrase token → (axis, lo, hi) in normalized bbox
    "top": ("z", 0.78, 1.0), "upper": ("z", 0.6, 1.0), "crown": ("z", 0.88, 1.0),
    "tip": ("z", 0.9, 1.0), "head": ("z", 0.8, 1.0), "cap": ("z", 0.85, 1.0),
    "bottom": ("z", 0.0, 0.22), "base": ("z", 0.0, 0.25), "foot": ("z", 0.0, 0.15),
    "lower": ("z", 0.0, 0.4), "middle": ("z", 0.35, 0.65), "mid": ("z", 0.35, 0.65),
    "left": ("x", 0.0, 0.3), "right": ("x", 0.7, 1.0),
    "front": ("y", 0.0, 0.3), "back": ("y", 0.7, 1.0), "rear": ("y", 0.7, 1.0),
}
_RIM_TOKENS = {"rim", "edge", "lip", "ring", "seam"}
_OUTER_TOKENS = {"outer", "outside", "shell", "skin", "surface", "exterior"}
_CORE_TOKENS = {"core", "inner", "center", "centre", "axis", "interior"}


def region_from_phrase(mesh_name, phrase, up="z"):
    """Best-effort region for an everyday phrase, resolved as a pipeline.

    A positional band ("top"/"left"/"front"…) is applied first; a shell word
    ("rim"/"outer"/"core") is then applied **within** that band so it tracks the
    local radius of the survivors. Returns a Selection dict."""
    toks = _tokens(phrase)
    stages = []

    # 1) Positional band → normalized box (intersect ranges per axis).
    by_axis = {}
    for t in toks:
        if t in _BANDS:
            ax, lo_b, hi_b = _BANDS[t]
            if ax in by_axis:
                plo, phi = by_axis[ax]
                by_axis[ax] = (max(plo, lo_b), min(phi, hi_b))
            else:
                by_axis[ax] = (lo_b, hi_b)
    if by_axis:
        loN = [0.0, 0.0, 0.0]
        hiN = [1.0, 1.0, 1.0]
        for ax, (l, h) in by_axis.items():
            i = _AXIS[ax]
            loN[i], hiN[i] = l, h
        stages.append({"box": {"lo_norm": loN, "hi_norm": hiN}})

    # 2) Shell word → radial band, normalized within the band from stage 1.
    if any(t in _RIM_TOKENS for t in toks):
        stages.append({"radial_band": {"axis": up, "r_lo": 0.8, "r_hi": 1.0}})
    elif any(t in _OUTER_TOKENS for t in toks):
        stages.append({"radial_band": {"axis": up, "r_lo": 0.7, "r_hi": 1.0}})
    elif any(t in _CORE_TOKENS for t in toks):
        stages.append({"radial_band": {"axis": up, "r_lo": 0.0, "r_hi": 0.3}})

    if not stages:
        return select_region(mesh_name, {})  # whole mesh
    return select_pipeline(mesh_name, stages)


# ─────────────────────────────────────────────────────────────────────────
# PAINT (Blender-native vertex group; convention: spatail.region.<id>)
# ─────────────────────────────────────────────────────────────────────────

def paint_group(mesh_name, group_name, selection, weight=1.0):
    """Write the selection's vertices to a vertex group (idempotent)."""
    obj = _get_mesh(mesh_name)
    vg = obj.vertex_groups.get(group_name) or obj.vertex_groups.new(name=group_name)
    vg.remove([v.index for v in obj.data.vertices])
    idxs = selection["vertices"] if isinstance(selection, dict) else list(selection)
    vg.add(list(idxs), weight, "REPLACE")
    return {"group": group_name, "n": len(idxs)}


def select_in_viewport(mesh_name, selection, kind="VERT"):
    """Make the selection live in the Blender viewport for visual confirmation.

    kind: 'VERT' | 'FACE' | 'EDGE'. Switches the mesh to edit mode and selects.
    """
    obj = _get_mesh(mesh_name)
    bpy.context.view_layer.objects.active = obj
    for o in bpy.context.selected_objects:
        o.select_set(False)
    obj.select_set(True)
    if bpy.context.object.mode != "EDIT":
        bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="DESELECT")
    bpy.ops.mesh.select_mode(type=kind)
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    if kind == "VERT":
        for i in selection["vertices"]:
            bm.verts[i].select_set(True)
    elif kind == "FACE":
        for i in selection["faces"]:
            bm.faces[i].select_set(True)
    elif kind == "EDGE":
        vsel = set(selection["vertices"])
        for e in bm.edges:
            if e.verts[0].index in vsel and e.verts[1].index in vsel:
                e.select_set(True)
    bmesh.update_edit_mesh(obj.data)
    return {"selected_kind": kind}


print("[spatail_mesh_select] module loaded.")
