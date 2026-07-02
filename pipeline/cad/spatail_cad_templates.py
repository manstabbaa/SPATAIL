"""spatail_cad_templates.py — turn a usermanualXR build-plan PART into
build123d generator source (a `gen_step()` script the `$cad` skill's
`scripts/step` consumes).

This is the bridge between the manual-segmenter's crude primitive plan and
REAL parametric CAD. Each part in the plan can carry an optional ``cad`` block;
when present (or when ``cad_all`` mode derives one) this module emits build123d
Python that models the part as a true OpenCascade B-rep solid — filleted
panels, drilled dowel holes, real annular tubes, L-brackets — instead of a bare
primitive box.

Design notes
------------
- **Pure text emitter.** No build123d import here, so it runs under any Python.
  The emitted source is executed later by ``scripts/step`` inside the CAD venv.
- **Units.** Plans are authored in CENTIMETRES. CAD is authored in MILLIMETRES
  (the build123d/STEP convention), so sizes are multiplied by ``CM_TO_MM``.
  ``scripts/step`` exports a METRES-magnitude GLB (mm/1000), and the CAD stage
  (``spatail_cad_build.py``) bakes that into a metres ``.npz`` mesh payload, so
  the Blender stage imports it at ×1.0 — matching the driver's cm→m primitive
  scaling exactly. (Do NOT add a ×0.001 in Blender; the payload is already metres.)
- **Orientation.** build123d is +Z up, same axis convention as the plan and the
  Blender scene. ``scripts/step`` exports a Y-up GLB; the CAD stage converts the
  vertices back to Blender Z-up in numpy (``(x,y,z)_gltf → (x,-z,y)_blender``)
  while baking the payload, so the part round-trips to its authored orientation.
  (Blender loads the ``.npz`` via ``from_pydata`` — there is no glTF import.)
- **Centred origin.** All templates centre the solid on the origin, and the bake
  re-centres the baked mesh on its bbox centre, so the Blender driver can place
  it by its ``location`` exactly like a primitive.

Part ``cad`` block (all fields optional)::

    "cad": {
      "shape": "panel|bar|dowel|tube|lbracket|box",
      "fillet": 2.0,                  # edge ease radius, mm (panels/box)
      "thickness_axis": "x",          # thin axis for panels (else inferred)
      "axis": "z",                    # long axis for bar/dowel/tube
      "inner_radius": 1.0,            # tube bore, cm
      "holes": [                      # drilled holes, part-local, cm
        {"axis": "y", "d": 0.8, "at": [u, v], "through": true}
      ],
      "generator": "side_left.py"     # bespoke source authored by an agent
    }

``holes[].at`` is a 2-tuple in the panel's face plane (the two non-hole axes),
measured in cm from the part centre. ``axis`` is the drilling direction.
"""
from __future__ import annotations

import re

CM_TO_MM = 10.0


# ─────────────────────────────────────────────────────────────────────────
# Default CAD spec derivation (cad_all mode) — turn any primitive part into a
# sensible real-CAD shape from its role + primitive + proportions.
# ─────────────────────────────────────────────────────────────────────────

_PANEL_ROLES = {
    "side_panel", "panel", "shelf", "top", "bottom", "back", "back_panel",
    "door", "lid", "base", "divider",
}
_BAR_ROLES = {"leg", "rail", "stretcher", "post", "frame", "support", "bar"}


def derive_cad_spec(part: dict) -> dict:
    """Derive a default ``cad`` block for a part lacking an explicit one.

    Used by cad_all mode so an entire segmented manual upgrades to real CAD
    without the segmenter knowing anything about CAD.
    """
    explicit = dict(part.get("cad") or {})
    prim = part.get("primitive", "box")
    role = (part.get("role") or "").lower()
    size = part.get("size") or [1, 1, 1]

    if "shape" not in explicit:
        if prim == "tube":
            explicit["shape"] = "tube"
        elif prim == "cylinder":
            explicit["shape"] = "dowel" if role in {"dowel", "pin", "fastener"} else "bar"
        elif role in _PANEL_ROLES or _looks_like_panel(size):
            explicit["shape"] = "panel"
        elif role in _BAR_ROLES:
            explicit["shape"] = "bar"
        else:
            explicit["shape"] = "box"

    # A tasteful default edge ease for panels/boxes (clamped later to thickness).
    if explicit["shape"] in {"panel", "box"} and "fillet" not in explicit:
        explicit["fillet"] = 0.2  # cm → 2 mm, clamped per part

    return explicit


def _looks_like_panel(size) -> bool:
    s = sorted(float(v) for v in size)
    return len(s) == 3 and s[0] > 0 and s[2] / max(s[0], 1e-6) >= 4.0


# ─────────────────────────────────────────────────────────────────────────
# Source emission
# ─────────────────────────────────────────────────────────────────────────

def _label(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", name) or "part"


def emit_generator(part: dict, *, cad: dict | None = None) -> str:
    """Return build123d ``gen_step()`` source text for one plan part.

    ``cad`` overrides ``part['cad']`` (e.g. a derived spec from cad_all mode).
    """
    cad = cad if cad is not None else (part.get("cad") or {})
    shape = (cad.get("shape") or "box").lower()
    name = part.get("name", "part")
    label = _label(name)
    size_cm = [float(v) for v in (part.get("size") or [1, 1, 1])]
    sx, sy, sz = (v * CM_TO_MM for v in size_cm)

    if shape == "panel" or shape == "box":
        body = _emit_panel(sx, sy, sz, cad, size_cm)
    elif shape in ("bar", "leg"):
        body = _emit_bar(sx, sy, sz, cad)
    elif shape in ("dowel", "pin", "cylinder"):
        body = _emit_dowel(part, cad)
    elif shape in ("tube", "washer"):
        body = _emit_tube(part, cad)
    elif shape == "lbracket":
        body = _emit_lbracket(sx, sy, sz, cad)
    elif shape in ("screw", "bolt"):
        body = _emit_screw(part, cad)
    elif shape in ("camlock", "cam_lock"):
        body = _emit_camlock(part, cad)
    elif shape == "cap":
        body = _emit_cap(part, cad)
    elif shape == "nut":
        body = _emit_nut(part, cad)
    elif shape == "hinge":
        body = _emit_hinge(sx, sy, sz, cad)
    else:
        body = _emit_panel(sx, sy, sz, cad, size_cm)

    holes_src = _emit_holes(cad.get("holes") or [])

    return _TEMPLATE.format(
        doc=f"Auto-generated CAD part '{name}' (shape={shape}) for usermanualXR.",
        body=body,
        holes=holes_src,
        label=label,
    )


_TEMPLATE = '''"""{doc}

Generated by spatail_cad_templates.py - edit the build-plan `cad` block or
supply a bespoke generator instead of hand-editing this file.
Units: millimetres, +Z up, origin at part centre.
"""
from build123d import Box, Cylinder, Pos, Rot, Align, Axis, fillet


def gen_step():
{body}
{holes}
    part.label = "{label}"
    return part
'''


def _emit_panel(sx: float, sy: float, sz: float, cad: dict, size_cm) -> str:
    thickness_mm = min(sx, sy, sz)
    fillet_cm = cad.get("fillet")
    r = (float(fillet_cm) * CM_TO_MM) if fillet_cm is not None else 0.0
    # Clamp the ease so the corner fillet can never exceed ~40% of the thinnest
    # dimension (OpenCascade fails on over-large radii).
    r = max(0.0, min(r, 0.4 * thickness_mm))
    lines = [
        f"    part = Box({sx:.4f}, {sy:.4f}, {sz:.4f})",
    ]
    if r > 1e-3:
        lines += [
            f"    try:",
            f"        part = fillet(part.edges(), radius={r:.4f})",
            f"    except Exception:",
            f"        pass",
        ]
    return "\n".join(lines)


def _emit_bar(sx: float, sy: float, sz: float, cad: dict) -> str:
    # A bar is a box eased along its long edges (more than a panel).
    thickness_mm = min(sx, sy, sz)
    fillet_cm = cad.get("fillet")
    r = (float(fillet_cm) * CM_TO_MM) if fillet_cm is not None else 0.25 * thickness_mm
    r = max(0.0, min(r, 0.45 * thickness_mm))
    longest = max((sx, "Axis.X"), (sy, "Axis.Y"), (sz, "Axis.Z"))[1]
    lines = [f"    part = Box({sx:.4f}, {sy:.4f}, {sz:.4f})"]
    if r > 1e-3:
        lines += [
            f"    try:",
            f"        part = fillet(part.edges().filter_by({longest}), radius={r:.4f})",
            f"    except Exception:",
            f"        pass",
        ]
    return "\n".join(lines)


def _emit_dowel(part: dict, cad: dict) -> str:
    axis = (cad.get("axis") or part.get("axis") or "z").lower()
    radius_cm = float(part.get("radius") or (cad.get("radius") or 0.4))
    # length: cylinder primitives carry `depth`; else use the long size axis.
    depth_cm = part.get("depth")
    if depth_cm is None:
        size = part.get("size") or [1, 1, max(1.0, radius_cm * 6)]
        depth_cm = max(float(v) for v in size)
    R = radius_cm * CM_TO_MM
    L = float(depth_cm) * CM_TO_MM
    rot = {"x": "Rot(0, 90, 0) * ", "y": "Rot(90, 0, 0) * ", "z": ""}.get(axis, "")
    return f"    part = {rot}Cylinder(radius={R:.4f}, height={L:.4f})"


def _emit_tube(part: dict, cad: dict) -> str:
    axis = (cad.get("axis") or part.get("axis") or "z").lower()
    R = float(part.get("radius") or 1.0) * CM_TO_MM
    ri = float(part.get("inner_radius") or cad.get("inner_radius") or (R / CM_TO_MM) * 0.6) * CM_TO_MM
    ri = max(0.0, min(ri, 0.95 * R))
    depth_cm = part.get("depth")
    if depth_cm is None:
        size = part.get("size") or [1, 1, 1]
        depth_cm = max(float(v) for v in size)
    L = float(depth_cm) * CM_TO_MM
    rot = {"x": "Rot(0, 90, 0) * ", "y": "Rot(90, 0, 0) * ", "z": ""}.get(axis, "")
    lines = [
        f"    _outer = Cylinder(radius={R:.4f}, height={L:.4f})",
        f"    _bore = Cylinder(radius={ri:.4f}, height={L * 1.05:.4f})",
        f"    part = {rot}(_outer - _bore)",
    ]
    return "\n".join(lines)


def _emit_lbracket(sx: float, sy: float, sz: float, cad: dict) -> str:
    # Right-angle bracket: a horizontal leg (X) and a vertical leg (Z), each a
    # plate of thickness `t`, the unit's width along Y. Centred on the origin.
    t = max(2.0, min(sx, sy, sz) * 0.12)
    r = max(0.0, min(float(cad.get("fillet", 0.4)) * CM_TO_MM, 0.4 * t))
    lines = [
        f"    t = {t:.4f}",
        f"    sx, sy, sz = {sx:.4f}, {sy:.4f}, {sz:.4f}",
        f"    horiz = Pos(0, 0, -sz / 2 + t / 2) * Box(sx, sy, t)",
        f"    vert = Pos(-sx / 2 + t / 2, 0, 0) * Box(t, sy, sz)",
        f"    part = horiz + vert",
    ]
    if r > 1e-3:
        lines += [
            f"    try:",
            f"        part = fillet(part.edges().filter_by(Axis.Y), radius={r:.4f})",
            f"    except Exception:",
            f"        pass",
        ]
    return "\n".join(lines)


_AXIS_ROT = {"x": "Rot(0, 90, 0) * ", "y": "Rot(90, 0, 0) * ", "z": ""}


def _emit_screw(part: dict, cad: dict) -> str:
    """A screw/bolt: a cylindrical shank with a wider head at one end."""
    axis = (cad.get("axis") or part.get("axis") or "x").lower()
    Rs = float(part.get("radius") or 0.3) * CM_TO_MM
    L = float(part.get("depth") or 4.0) * CM_TO_MM
    Rh = Rs * 1.9
    Hh = max(2.0, L * 0.14)
    rot = _AXIS_ROT.get(axis, "")
    return "\n".join([
        f"    _shank = Cylinder(radius={Rs:.4f}, height={L:.4f})",
        f"    _head = Pos(0, 0, {L / 2:.4f}) * Cylinder(radius={Rh:.4f}, "
        f"height={Hh:.4f}, align=(Align.CENTER, Align.CENTER, Align.MIN))",
        f"    part = {rot}(_shank + _head)",
    ])


def _emit_camlock(part: dict, cad: dict) -> str:
    """A cam-lock fitting: a squat disc with a central bore."""
    axis = (cad.get("axis") or part.get("axis") or "z").lower()
    R = float(part.get("radius") or 0.75) * CM_TO_MM
    L = float(part.get("depth") or 1.3) * CM_TO_MM
    rb = R * 0.4
    rot = _AXIS_ROT.get(axis, "")
    return "\n".join([
        f"    _body = Cylinder(radius={R:.4f}, height={L:.4f})",
        f"    _bore = Cylinder(radius={rb:.4f}, height={L * 1.1:.4f})",
        f"    part = {rot}(_body - _bore)",
    ])


def _emit_cap(part: dict, cad: dict) -> str:
    """A cover cap: a shallow disc that hides a screw head."""
    axis = (cad.get("axis") or part.get("axis") or "z").lower()
    R = float(part.get("radius") or 0.6) * CM_TO_MM
    L = float(part.get("depth") or 0.45) * CM_TO_MM
    rot = _AXIS_ROT.get(axis, "")
    return f"    part = {rot}Cylinder(radius={R:.4f}, height={L:.4f})"


def _emit_nut(part: dict, cad: dict) -> str:
    """A nut: a short ring (bored disc)."""
    axis = (cad.get("axis") or part.get("axis") or "z").lower()
    R = float(part.get("radius") or 0.5) * CM_TO_MM
    L = float(part.get("depth") or 0.5) * CM_TO_MM
    rb = R * 0.55
    rot = _AXIS_ROT.get(axis, "")
    return "\n".join([
        f"    _body = Cylinder(radius={R:.4f}, height={L:.4f})",
        f"    _bore = Cylinder(radius={rb:.4f}, height={L * 1.1:.4f})",
        f"    part = {rot}(_body - _bore)",
    ])


def _emit_hinge(sx: float, sy: float, sz: float, cad: dict) -> str:
    """A butt hinge: two flat leaves joined by a knuckle barrel along Y."""
    t = max(2.0, min(sx, sy, sz))
    return "\n".join([
        f"    sx, sy, sz = {sx:.4f}, {sy:.4f}, {sz:.4f}",
        f"    t = {t:.4f}",
        f"    _leafA = Pos(-sx / 4, 0, 0) * Box(sx / 2, sy, t)",
        f"    _leafB = Pos(sx / 4, 0, 0) * Box(sx / 2, sy, t)",
        f"    _knuckle = Rot(90, 0, 0) * Cylinder(radius=t * 0.9, height=sy)",
        f"    part = _leafA + _leafB + _knuckle",
    ])


def _emit_holes(holes) -> str:
    """Emit subtractive drilled holes. Each hole: axis + diameter + (u,v) in cm."""
    if not holes:
        return ""
    out = []
    for h in holes:
        axis = (h.get("axis") or "z").lower()
        d_mm = float(h.get("d", 0.5)) * CM_TO_MM
        at = h.get("at") or [0.0, 0.0]
        u, v = float(at[0]) * CM_TO_MM, float(at[1]) * CM_TO_MM
        depth_mm = float(h.get("depth", 0.0)) * CM_TO_MM
        # A "through" hole is long enough to pierce any plausible plate; a blind
        # hole uses the given depth. We over-shoot then rely on the boolean.
        h_len = depth_mm if depth_mm > 0 else 10000.0
        # Position + drilling direction. (u, v) lie in the plane perpendicular
        # to `axis`, ordered (X,Y,Z) minus the drill axis.
        if axis == "x":
            pos = f"Pos({0.0:.1f}, {u:.4f}, {v:.4f})"
            rot = "Rot(0, 90, 0) * "
        elif axis == "y":
            pos = f"Pos({u:.4f}, {0.0:.1f}, {v:.4f})"
            rot = "Rot(90, 0, 0) * "
        else:
            pos = f"Pos({u:.4f}, {v:.4f}, {0.0:.1f})"
            rot = ""
        out.append(
            f"    part = part - ({pos} * {rot}Cylinder(radius={d_mm / 2:.4f}, height={h_len:.1f}))"
        )
    return "\n".join(out)


if __name__ == "__main__":
    import json
    import sys

    demo = {
        "name": "side_left",
        "role": "side_panel",
        "primitive": "box",
        "size": [3.8, 39.0, 147.0],
        "cad": {"shape": "panel", "fillet": 0.3,
                "holes": [{"axis": "x", "d": 0.8, "at": [10.0, 30.0]},
                          {"axis": "x", "d": 0.8, "at": [-10.0, 30.0]}]},
    }
    part = json.loads(sys.argv[1]) if len(sys.argv) > 1 else demo
    print(emit_generator(part, cad=derive_cad_spec(part)))
