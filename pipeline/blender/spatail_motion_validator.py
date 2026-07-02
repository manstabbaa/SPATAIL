"""
spatail_motion_validator.py — multi-angle, multi-phase motion + geometry
validator for any rigged asset in the SPATAIL pipeline.

Run via:
  exec(open(r"C:/SPATAIL_MAX/pipeline/blender/spatail_motion_validator.py").read())
  validate_asset(asset_id="fan", ..., out_root=r"C:/tmp/motion_validate")

For each animation clip:
  1. Sample 4 phases (0%, 25%, 50%, 75%)
  2. At each phase, snapshot per-part world positions
  3. At each phase, render 6 mugshot views with a grid overlay
  4. Cluster parts by their motion vector → "cohorts"
  5. Compare cohorts to the asset's declared kinematic groups → findings
  6. Write _validation.json with the structured report

See skills/spatail-motion-validator/SKILL.md.
"""
import bpy
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from mathutils import Vector


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PHASES = [0.0, 0.25, 0.50, 0.75]      # animation positions to sample
# Mugshot views — 12 angles like a forensic photo set.
# "axial_*" looks ALONG the rotation axis (the most diagnostic view for a
# rotor). "broadside_*" looks perpendicular to the axis. The four 3/4
# corner shots show all faces at once.
VIEWS = [
  "axial_front", "axial_back",
  "broadside_left", "broadside_right",
  "broadside_top", "broadside_bottom",
  "corner_FL_up", "corner_FR_up", "corner_BL_up", "corner_BR_up",
  "tilt_low",   "tilt_high",
]
COHORT_TOL_M = 0.0005                 # parts within 0.5mm of each other's motion vector → same cohort


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _all_meshes(name_prefix=None):
    return [o for o in bpy.context.scene.objects
            if o.type == "MESH" and (not name_prefix or name_prefix in o.name)]


def _asset_bbox():
    """World-space bbox + diagonal of every visible mesh."""
    mn = Vector((1e9, 1e9, 1e9)); mx = Vector((-1e9, -1e9, -1e9))
    for o in _all_meshes():
        for c in o.bound_box:
            wp = o.matrix_world @ Vector(c)
            for i in range(3):
                if wp[i] < mn[i]: mn[i] = wp[i]
                if wp[i] > mx[i]: mx[i] = wp[i]
    size = mx - mn
    return mn, mx, size, size.length


def _set_animation_phase(action_name, phase01):
    """Seek the named action at fractional position phase01 [0..1].

    Blender evaluates an object's active Action when scene.frame_set is
    called and the depsgraph updates — we don't need to find the owner
    explicitly. We just need to (a) confirm the action exists, (b) pick
    a frame inside its frame_range, (c) call frame_set, (d) force a
    depsgraph update so subsequent matrix_world reads are correct.
    """
    act = bpy.data.actions.get(action_name)
    if act is None:
        # No such action — leave the scene at its current frame
        bpy.context.view_layer.update()
        return False
    fr_start = int(round(act.frame_range[0]))
    fr_end   = int(round(act.frame_range[1]))
    duration = max(1, fr_end - fr_start)
    f = fr_start + int(round(phase01 * duration))
    # Make sure the scene's range is wide enough that frame_set can land
    bpy.context.scene.frame_start = min(bpy.context.scene.frame_start, fr_start)
    bpy.context.scene.frame_end   = max(bpy.context.scene.frame_end,   fr_end)
    bpy.context.scene.frame_set(f)
    bpy.context.evaluated_depsgraph_get().update()
    bpy.context.view_layer.update()
    return True


def _snapshot_positions(meshes):
    """Return {name: (x,y,z)} world bbox center of each mesh. We use the
    bbox center, NOT obj.matrix_world.translation, because a child parented
    to an empty often inherits the parent's origin (0,0,0) — its origin
    doesn't move even when the parent rotates, but its vertices do. The
    bbox center reflects where the actual geometry sits in world space."""
    bpy.context.view_layer.update()
    out = {}
    for o in meshes:
        corners = [o.matrix_world @ Vector(c) for c in o.bound_box]
        cx = sum(c.x for c in corners) / 8.0
        cy = sum(c.y for c in corners) / 8.0
        cz = sum(c.z for c in corners) / 8.0
        out[o.name] = (cx, cy, cz)
    return out


def _displacement_vectors(snapshots_per_phase):
    """Given [{name: pos}, {name: pos}, ...] across phases, return per-part
    displacement vectors relative to phase 0."""
    base = snapshots_per_phase[0]
    out = {}
    for name, p0 in base.items():
        seq = []
        for snap in snapshots_per_phase:
            p = snap.get(name)
            if p is None:
                seq.append((0.0, 0.0, 0.0))
            else:
                seq.append((p[0] - p0[0], p[1] - p0[1], p[2] - p0[2]))
        out[name] = seq
    return out


def _cohort_signature(disp_seq, q=4):
    """Quantize a displacement-vector sequence so near-equal motion clusters
    to the same key. q=4 → round to ~0.5mm given COHORT_TOL_M."""
    return tuple(tuple(round(v / COHORT_TOL_M) for v in vec) for vec in disp_seq)


def _cluster_cohorts(displacements):
    """Group {name: [(dx,dy,dz), ...]} by quantized signature."""
    cohorts = {}
    for name, seq in displacements.items():
        sig = _cohort_signature(seq)
        cohorts.setdefault(sig, []).append(name)
    # Convert to ordered list with summary metrics
    out = []
    for sig, members in cohorts.items():
        # Magnitude of total displacement at last phase
        last = sig[-1]
        mag = math.sqrt(sum((m * COHORT_TOL_M) ** 2 for m in last))
        out.append({
            "members": sorted(members),
            "count": len(members),
            "total_displacement_m": round(mag, 4),
            "is_stationary": mag < 0.0005,
        })
    out.sort(key=lambda c: -c["total_displacement_m"])
    return out


# ---------------------------------------------------------------------------
# Mugshot rendering
# ---------------------------------------------------------------------------

def _make_grid_overlay_world(scene_bbox_diag):
    """Build / refresh a flat empty-grid overlay parented at origin, with
    1cm spacing, sized to encompass the scene. Used by the workbench
    overlay so every render has a ruler.

    Implementation: a single Empty with a 2D grid using ImageEmpty would be
    ideal, but for portability we draw a child of the scene that's a
    semi-transparent plane with a generated grid texture. Easiest: rely on
    Blender's built-in viewport grid by using a Workbench shading config
    with "Floor/X/Y/Z axes" enabled and snapping its scale to 1cm.
    We accept the default viewport grid here for simplicity.
    """
    # Workbench / EEVEE both respect scene.gravity and the world grid; the
    # grid scale is controlled by space_data.overlay.grid_scale which is
    # only accessible from a 3D viewport context. For headless renders we
    # instead OVERLAY a procedurally-generated PNG ruler in _composite_overlay.
    return None


def _setup_camera_view(name, target, distance, axis_world=None):
    """Place the validator camera at a known angle relative to the asset.

    `axis_world` is the asset's rotation axis (unit Vector) from its
    registry's `rotation_axis_world`. Camera positions are derived as
    follows so they're consistent across asset orientations:

      axis_front / axis_back  → camera ON the rotation axis (most diagnostic for rotors)
      broadside_*             → camera perpendicular to the axis
      corner_*_up             → 3/4 corner shots, halfway between axis + perpendicular
      tilt_low / tilt_high    → small tilt above/below for shading detail
    """
    cam = bpy.data.objects.get("__mv_cam__")
    if cam is None:
        cam_data = bpy.data.cameras.new("__mv_cam__")
        cam = bpy.data.objects.new("__mv_cam__", cam_data)
        bpy.context.collection.objects.link(cam)
    cam.data.lens = 50
    bpy.context.scene.camera = cam
    bpy.context.scene.render.resolution_x = 640
    bpy.context.scene.render.resolution_y = 480

    # Default axis = world Y (the most common rotation axis for fans / wheels);
    # callers should pass axis_world from the asset's registry.
    if axis_world is None:
        axis_world = Vector((0, 1, 0))
    A = Vector(axis_world).normalized()
    # Build two perpendicular axes (P1, P2) to A so we can place orbital views
    ref = Vector((0, 0, 1)) if abs(A.z) < 0.9 else Vector((1, 0, 0))
    P1 = ref.cross(A).normalized()              # "right" in the rotor plane
    P2 = A.cross(P1).normalized()               # "up" in the rotor plane

    pos_offsets = {
      "axial_front":   A *  distance,
      "axial_back":    A * -distance,
      "broadside_left":  -P1 * distance,
      "broadside_right":  P1 * distance,
      "broadside_top":    P2 * distance,
      "broadside_bottom":-P2 * distance,
      "corner_FL_up":  ( A * 0.55 - P1 * 0.55 + P2 * 0.55) * distance,
      "corner_FR_up":  ( A * 0.55 + P1 * 0.55 + P2 * 0.55) * distance,
      "corner_BL_up":  (-A * 0.55 - P1 * 0.55 + P2 * 0.55) * distance,
      "corner_BR_up":  (-A * 0.55 + P1 * 0.55 + P2 * 0.55) * distance,
      "tilt_low":      ( A * 0.85 + P2 * -0.35) * distance,
      "tilt_high":     ( A * 0.85 + P2 *  0.35) * distance,
    }
    offset = pos_offsets.get(name)
    if offset is None:
        offset = A * distance
    cam.location = target + offset
    direction = target - cam.location
    cam.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()


def _ensure_render_world():
    if bpy.context.scene.world is None:
        bpy.context.scene.world = bpy.data.worlds.new("World")
    bpy.context.scene.world.use_nodes = False
    # SPATAIL paper cream so renders match the web aesthetic
    bpy.context.scene.world.color = (0.961, 0.957, 0.937)
    try:
        bpy.context.scene.render.engine = "BLENDER_EEVEE_NEXT"
    except Exception:
        try: bpy.context.scene.render.engine = "BLENDER_EEVEE"
        except Exception: pass
    # Bright film exposure so cream comes out as actual cream
    bpy.context.scene.view_settings.exposure = 0.5
    bpy.context.scene.view_settings.view_transform = "Standard"


def _ensure_render_lights():
    """Two suns + a hemi so subjects read evenly from every angle without
    one side going pitch black. Tuned for the cream paper world."""
    if not bpy.data.objects.get("__mv_sun_key__"):
        d = bpy.data.lights.new("__mv_sun_key__", "SUN")
        o = bpy.data.objects.new("__mv_sun_key__", d)
        bpy.context.collection.objects.link(o)
        o.rotation_euler = (0.7, 0.3, 0)
        d.energy = 4.5
    if not bpy.data.objects.get("__mv_sun_fill__"):
        d = bpy.data.lights.new("__mv_sun_fill__", "SUN")
        o = bpy.data.objects.new("__mv_sun_fill__", d)
        bpy.context.collection.objects.link(o)
        o.rotation_euler = (-0.6, -0.4, 1.2)
        d.energy = 2.0
        d.color = (0.95, 0.96, 1.0)


def _render_mugshot(out_path):
    """Render to PNG + composite a 1cm-grid overlay + text label."""
    bpy.context.scene.render.image_settings.file_format = "PNG"
    bpy.context.scene.render.filepath = str(out_path)
    bpy.ops.render.render(write_still=True)


def _composite_overlay(image_path, *, view_name, phase01, clip_name,
                       cam_world_pos, target_world_pos, scene_bbox_size):
    """Open the rendered PNG, draw a 1cm grid + 1cm scale ruler + caption,
    write back. Uses PIL (already installed in Blender per task #41)."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("[motion_validator] PIL not installed in Blender Python; skipping overlay")
        return
    im = Image.open(image_path).convert("RGB")
    W, H = im.size
    draw = ImageDraw.Draw(im, "RGBA")

    # Estimate world-cm-per-pixel using the camera's FOV + distance + image height
    cam = bpy.data.objects.get("__mv_cam__")
    if cam:
        fov_v = 2 * math.atan(0.5 * cam.data.sensor_height / cam.data.lens)
        dist = (Vector(target_world_pos) - Vector(cam_world_pos)).length
        world_height_at_target = 2.0 * dist * math.tan(fov_v / 2.0)
        # 1 cm = px per cm
        px_per_cm = (H / world_height_at_target) * 0.01 if world_height_at_target > 0 else 0
    else:
        px_per_cm = 0

    # 1cm-grid overlay (subtle ink lines)
    if px_per_cm and px_per_cm > 6:   # only draw if grid won't be a smear
        # Every 1cm thin line, every 5cm bolder
        for i in range(0, max(W, H), int(px_per_cm)):
            x = int(round(i)); y = int(round(i))
            bold = (i / px_per_cm) % 5 == 0
            col = (10, 10, 15, 32 if bold else 16)
            draw.line([(x, 0), (x, H)], fill=col, width=1)
            draw.line([(0, y), (W, y)], fill=col, width=1)

    # 1cm scale ruler at bottom-left
    if px_per_cm:
        rw = int(px_per_cm)
        x0 = 12; y0 = H - 32; x1 = x0 + rw * 3  # 3cm ruler
        draw.rectangle([x0, y0, x1, y0 + 6], fill=(10, 10, 15, 220))
        for k in range(4):
            tx = x0 + k * rw
            draw.line([(tx, y0 - 4), (tx, y0)], fill=(10, 10, 15, 220), width=1)
        draw.text((x0, y0 - 20), "3 cm", fill=(10, 10, 15, 220))

    # Caption pill — view, phase, clip
    caption = f"{view_name.upper()}  ·  PHASE {int(phase01*100):02d}%  ·  {clip_name}"
    draw.rectangle([12, 12, 12 + 6 * len(caption) + 18, 36], fill=(10, 10, 15, 180))
    draw.text((20, 16), caption, fill=(245, 244, 239, 240))

    im.save(image_path)


def _build_contact_sheet(out_dir, clip_name):
    """Stitch the 4 phases × 6 views into one 4×6 PNG for the vision pass."""
    try:
        from PIL import Image
    except ImportError:
        return None
    rows = []
    for ph in PHASES:
        cells = []
        for view in VIEWS:
            p = Path(out_dir) / f"phase_{int(ph*100):02d}" / f"{view}.png"
            if p.exists():
                cells.append(Image.open(p).convert("RGB"))
        if cells:
            row = Image.new("RGB", (sum(c.width for c in cells), cells[0].height), (245, 244, 239))
            x = 0
            for c in cells:
                row.paste(c, (x, 0)); x += c.width
            rows.append(row)
    if not rows: return None
    sheet = Image.new("RGB", (max(r.width for r in rows), sum(r.height for r in rows)), (245, 244, 239))
    y = 0
    for r in rows:
        sheet.paste(r, (0, y)); y += r.height
    sheet_path = Path(out_dir) / "grid.png"
    sheet.save(sheet_path)
    return str(sheet_path)


# ---------------------------------------------------------------------------
# Per-clip validation
# ---------------------------------------------------------------------------

def validate_clip(clip_name, kinematic_group, out_dir, name_prefix=None, axis_world=None):
    """Sample the clip at 4 phases, snapshot positions, render mugshot grid,
    cluster cohorts, cross-reference against kinematic_group. Returns the
    findings dict."""
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    _ensure_render_world(); _ensure_render_lights()
    bb_mn, bb_mx, bb_size, bb_diag = _asset_bbox()
    center = (bb_mn + bb_mx) * 0.5
    distance = max(bb_diag * 1.6, 0.10)
    meshes = _all_meshes(name_prefix=name_prefix)

    # Step through phases
    phase_snaps = []
    for ph in PHASES:
        ok = _set_animation_phase(clip_name, ph)
        snap = _snapshot_positions(meshes)
        phase_snaps.append(snap)

        # Render all 6 views at this phase
        phase_dir = out_dir / f"phase_{int(ph*100):02d}"
        phase_dir.mkdir(exist_ok=True)
        for view in VIEWS:
            _setup_camera_view(view, center, distance, axis_world=axis_world)
            cam = bpy.data.objects["__mv_cam__"]
            cam_pos = tuple(cam.location)
            img_path = phase_dir / f"{view}.png"
            _render_mugshot(img_path)
            _composite_overlay(img_path,
                               view_name=view, phase01=ph, clip_name=clip_name,
                               cam_world_pos=cam_pos,
                               target_world_pos=tuple(center),
                               scene_bbox_size=tuple(bb_size))

    # Reset to phase 0 so we leave the scene in rest
    _set_animation_phase(clip_name, 0.0)

    # Compute displacements + cohorts
    disp = _displacement_vectors(phase_snaps)
    cohorts = _cluster_cohorts(disp)

    # Pick the "stationary" cohort (the largest one with ~0 displacement)
    stationary_set = set()
    for c in cohorts:
        if c["is_stationary"]:
            stationary_set.update(c["members"])
    # Pick the "moved" cohorts (the rest)
    moved_set = set(disp.keys()) - stationary_set

    # Compare against intended group
    intended_members = set(kinematic_group.get("members", []))
    stationary_but_should_have_moved = sorted(intended_members & stationary_set)
    moved_but_not_in_group = sorted(moved_set - intended_members)
    motion_consistency_score = (
        len(intended_members - stationary_set) / max(1, len(intended_members))
    )

    # Build contact sheet
    sheet_path = _build_contact_sheet(out_dir, clip_name)

    # Write per-clip JSONs
    (out_dir / "_measurements.json").write_text(json.dumps({
        "clip": clip_name,
        "phases": PHASES,
        "snapshots_per_phase": [
            {n: list(p) for n, p in snap.items()} for snap in phase_snaps
        ],
        "displacement_vectors": {n: [list(v) for v in seq] for n, seq in disp.items()},
    }, indent=2), encoding="utf-8")
    (out_dir / "_cohorts.json").write_text(json.dumps({
        "clip": clip_name,
        "cohort_count": len(cohorts),
        "cohorts": cohorts,
    }, indent=2), encoding="utf-8")

    findings = {
        "clip": clip_name,
        "intended_kinematic_group": kinematic_group.get("group_id"),
        "intended_members_count": len(intended_members),
        "actually_stationary_count": len(stationary_set),
        "actually_moved_count": len(moved_set),
        "motion_consistency_score": round(motion_consistency_score, 3),
        "stationary_but_should_have_moved": stationary_but_should_have_moved,
        "moved_but_not_in_group": moved_but_not_in_group,
        "verdict": (
            "PASS" if motion_consistency_score >= 0.95 and not moved_but_not_in_group
            else "FAIL" if motion_consistency_score < 0.5
            else "WARN"
        ),
        "contact_sheet": sheet_path,
        "renders_root": str(out_dir),
    }
    (out_dir / "_validation.json").write_text(json.dumps(findings, indent=2), encoding="utf-8")
    return findings


# ---------------------------------------------------------------------------
# Top-level entry
# ---------------------------------------------------------------------------

def validate_asset(asset_id, asset_glb, registry_path, anim_library_path,
                   out_root, name_prefix=None, vision_pass=False):
    """Validate every clip in the asset's animation library. Returns the
    aggregated summary; per-clip details are on disk."""
    registry = json.loads(Path(registry_path).read_text(encoding="utf-8"))
    anim_lib = json.loads(Path(anim_library_path).read_text(encoding="utf-8"))
    groups = {g["group_id"]: g for g in registry.get("kinematicGroups", [])}

    out_root = Path(out_root) / asset_id
    out_root.mkdir(parents=True, exist_ok=True)

    summary = {
        "asset_id": asset_id,
        "asset_glb": asset_glb,
        "validated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "clips": [],
    }

    for clip_name, clip_meta in anim_lib.get("animations", {}).items():
        # Best-effort group lookup: pick the group whose members overlap most
        # with the clip's moving_parts list.
        moving = set(clip_meta.get("moving_parts", []))
        # Resolve aliased names to real mesh ids via registry.aliases
        aliases = registry.get("aliases", {})
        moving_resolved = set()
        for m in moving:
            moving_resolved.add(aliases.get(m, m))
        best_group = None; best_overlap = 0
        for g in groups.values():
            overlap = len(set(g.get("members", [])) & moving_resolved)
            if overlap > best_overlap:
                best_overlap = overlap; best_group = g
        group_for_clip = best_group or {"group_id": "(none)", "members": list(moving_resolved)}

        clip_out = out_root / clip_name
        # Pull the asset's rotation axis from the registry if present
        axis_world = registry.get("rotation_axis_world") or registry.get("director_hints", {}).get("rotation_axis_world")
        findings = validate_clip(
            clip_name, group_for_clip, clip_out,
            name_prefix=name_prefix, axis_world=axis_world,
        )
        summary["clips"].append(findings)

    (out_root / "_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary
