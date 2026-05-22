"""
spatail_export_xr.py — Blender → .spatail bundle for the iOS AR player.

Bundle layout (see docs/xr/IOS_BUNDLE_SPEC.md for the contract):

    <out>.spatail/
        manifest.json          ← top-level pointers + version
        experience.json        ← v0.5 SpatialExperienceContract
        scene.usdz             ← geometry + materials + baked animation
        prims_index.json       ← prim-path → contract-element-id
        hero/
            front.jpg, perspective.jpg, thumbnail.jpg
        source/
            prompt.txt

The .spatail file is the folder above zipped with the .spatail extension.
The iOS app registers the UTI and opens it directly.

Authoring rules enforced at export:
  • Y-up, meters (USDZ assumes this; we rotate Blender's Z-up scene).
  • Each logical mesh becomes its own prim at /Scene/<part_id>.
  • Animation baked to USD TimeSamples (no live constraints).

USAGE in Blender:
    exec(open(r"C:/SPATAIL_MAX/pipeline/blender/spatail_export_xr.py").read())
    export_xr_bundle(
        out_path=r"C:/SPATAIL_MAX/bundles/f1_wheel_buttons.spatail",
        experience_json_path=None,   # if None, a minimal placeholder is generated
        title="What do all the buttons on an F1 steering wheel do?",
        prompt="What do all the buttons on an F1 steering wheel do?",
        asset_id="f1_steering_wheel",
        anim_frame_range=None,       # or (start, end) to bake animation
    )
"""
import bpy, json, os, shutil, math, re, zipfile
from datetime import datetime, timezone
from mathutils import Vector


# ────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────

_SAFE = re.compile(r"[^A-Za-z0-9_]")


def _safe_prim_id(name: str) -> str:
    """USD prim names must be valid identifiers. Slugify aggressively."""
    s = _SAFE.sub("_", name)
    if not s or not (s[0].isalpha() or s[0] == "_"):
        s = "p_" + s
    return s


def _world_bbox():
    lo = Vector((float("inf"),) * 3)
    hi = Vector((-float("inf"),) * 3)
    any_v = False
    for o in bpy.data.objects:
        if o.type != "MESH":
            continue
        if o.name.startswith("SPATAIL_") or o.name in ("HeroCam", "Camera"):
            continue
        for c in o.bound_box:
            p = o.matrix_world @ Vector(c)
            lo = Vector(map(min, lo, p))
            hi = Vector(map(max, hi, p))
            any_v = True
    if not any_v:
        return Vector((0, 0, 0)), Vector((0, 0, 0))
    return lo, hi


def _collect_logical_meshes():
    out = []
    for o in bpy.data.objects:
        if o.type != "MESH":
            continue
        if o.name.startswith("SPATAIL_") or o.name in ("HeroCam", "Camera"):
            continue
        if not o.data.vertices:
            continue
        out.append(o)
    return out


def _build_prims_index(meshes):
    """Map each logical mesh to a stable USDZ prim path + contract id."""
    used = set()
    prim_to_eid = {}
    eid_to_prim = {}
    for o in meshes:
        base = _safe_prim_id(o.name)
        eid = base
        suffix = 1
        while eid in used:
            eid = f"{base}_{suffix}"
            suffix += 1
        used.add(eid)
        prim_path = f"/Scene/{eid}"
        prim_to_eid[prim_path] = eid
        eid_to_prim[eid] = prim_path
        # Rename Blender object to match — guarantees the USD export uses this prim name
        try:
            o.name = eid
        except Exception:
            pass
    return prim_to_eid, eid_to_prim


def _prep_scene_for_usdz():
    """Ensure scene units are metric, scale 1 (meters), Y-up handled at export."""
    scn = bpy.context.scene
    scn.unit_settings.system = "METRIC"
    # Note: USD export below applies axis conversion to Y-up.


def _bake_animation_if_needed(frame_range):
    if frame_range is None:
        return None
    start, end = frame_range
    scn = bpy.context.scene
    scn.frame_start = start
    scn.frame_end = end
    # USD export will sample existing transform tracks. Drivers / constraints
    # would need bake_action to flatten; the contract anti-goal says constraints
    # are already baked upstream (reassemble / animate stages).
    return {"start": start, "end": end}


def _export_usdz(out_usdz_path, anim_range, meters_per_unit=0.01):
    """Use Blender's native USD exporter to write a USDZ archive.

    meters_per_unit : Blender unit → meters scale baked at export.
                       0.01 if scene is authored in cm (1 unit = 1 cm).
                       1.0 if scene is authored in meters.
    """
    # Probe supported operator properties for forward-compat across Blender versions.
    rna_props = set(bpy.ops.wm.usd_export.get_rna_type().properties.keys())

    desired = {
        "filepath": out_usdz_path,
        "selected_objects_only": False,
        "export_animation": anim_range is not None,
        "export_uvmaps": True,
        "export_normals": True,
        "export_materials": True,
        "export_meshes": True,
        "generate_preview_surface": True,
        "convert_orientation": True,
        "export_global_forward_selection": "NEGATIVE_Z",
        "export_global_up_selection": "Y",
        "meters_per_unit": meters_per_unit,
        "root_prim_path": "/Scene",
        "relative_paths": True,
        "overwrite_textures": True,
    }
    kwargs = {k: v for k, v in desired.items() if k in rna_props}
    kwargs["filepath"] = out_usdz_path  # always required

    win = bpy.context.window_manager.windows[0]
    screen = win.screen
    area = next((a for a in screen.areas if a.type == "VIEW_3D"), screen.areas[0])
    region = next((r for r in area.regions if r.type == "WINDOW"), area.regions[0])
    with bpy.context.temp_override(window=win, screen=screen,
                                    area=area, region=region):
        bpy.ops.wm.usd_export(**kwargs)


def _render_hero_frames(stage_dir):
    """OpenGL-render four orientations for the picker cover + fallback."""
    hero_dir = os.path.join(stage_dir, "hero")
    os.makedirs(hero_dir, exist_ok=True)
    scn = bpy.context.scene
    old_engine = scn.render.engine
    old_x = scn.render.resolution_x
    old_y = scn.render.resolution_y
    old_filepath = scn.render.filepath
    try:
        scn.render.engine = "BLENDER_EEVEE_NEXT" if hasattr(
            bpy.context, "scene") and "BLENDER_EEVEE_NEXT" in [
                e.identifier for e in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items
            ] else "BLENDER_EEVEE"
    except Exception:
        try:
            scn.render.engine = "BLENDER_EEVEE"
        except Exception:
            pass
    try:
        # Try to use existing HeroCam if present, else create a fresh camera at
        # a flattering perspective.
        cam = bpy.data.objects.get("HeroCam") or bpy.data.objects.get("Camera")
        if cam is None:
            cam_data = bpy.data.cameras.new("XRExportCam")
            cam = bpy.data.objects.new("XRExportCam", cam_data)
            bpy.context.scene.collection.objects.link(cam)
        scn.camera = cam

        lo, hi = _world_bbox()
        centre = (lo + hi) * 0.5
        size = (hi - lo).length
        dist = max(size * 1.4, 0.5)

        def _aim(camobj, eye, target):
            camobj.location = eye
            direction = (target - eye).normalized()
            # Default camera looks down -Z; build a track-to-style matrix
            from mathutils import Matrix
            up = Vector((0, 0, 1))
            right = direction.cross(up).normalized()
            if right.length < 1e-6:
                up = Vector((0, 1, 0))
                right = direction.cross(up).normalized()
            up = right.cross(direction).normalized()
            mat = Matrix(((right.x, up.x, -direction.x, eye.x),
                          (right.y, up.y, -direction.y, eye.y),
                          (right.z, up.z, -direction.z, eye.z),
                          (0, 0, 0, 1)))
            camobj.matrix_world = mat

        views = {
            "front":       centre + Vector(( 0,        -dist,    0)),
            "perspective": centre + Vector(( dist*0.7, -dist*0.7, dist*0.5)),
        }
        rendered = {}
        win = bpy.context.window_manager.windows[0]
        screen = win.screen
        area = next((a for a in screen.areas if a.type == "VIEW_3D"), screen.areas[0])
        region = next((r for r in area.regions if r.type == "WINDOW"), area.regions[0])
        for label, eye in views.items():
            _aim(cam, eye, centre)
            scn.render.resolution_x = 1280
            scn.render.resolution_y = 720
            path = os.path.join(hero_dir, f"{label}.jpg")
            scn.render.filepath = path
            scn.render.image_settings.file_format = "JPEG"
            with bpy.context.temp_override(window=win, screen=screen,
                                            area=area, region=region):
                bpy.ops.render.opengl(write_still=True)
            rendered[label] = path
        # Thumbnail: smaller version of perspective
        try:
            scn.render.resolution_x = 512
            scn.render.resolution_y = 512
            scn.render.filepath = os.path.join(hero_dir, "thumbnail.jpg")
            with bpy.context.temp_override(window=win, screen=screen,
                                            area=area, region=region):
                bpy.ops.render.opengl(write_still=True)
            rendered["thumbnail"] = scn.render.filepath
        except Exception:
            pass
        return rendered
    finally:
        scn.render.engine = old_engine
        scn.render.resolution_x = old_x
        scn.render.resolution_y = old_y
        scn.render.filepath = old_filepath


def _write_placeholder_experience_json(stage_dir, asset_id, title, prompt,
                                          eid_to_prim):
    """Minimal v0.5 contract so the iOS app can render something even without
    an orchestrator run. Each part becomes a `three_d_model` element."""
    spatial_elements = []
    for eid, prim_path in list(eid_to_prim.items())[:200]:  # cap for size
        spatial_elements.append({
            "id": eid,
            "title": eid,
            "contentType": "physical_target",
            "representationMode": "three_d_model",
            "placement": {"kind": "in_front_of_user"},
            "anchorStrategy": "world_anchor",
            "scaleMode": "real_scale",
            "priority": 50,
            "fidelity": "authored",
            "whyThisRepresentation": "exported from authored Blender pass",
            "whyThisPlacement": "default in front of user; orchestrator may override",
            "fallbackGeometry": "panel",
            "interactions": [],
            "attentionBehavior": "ambient",
        })
    contract = {
        "schemaVersion": "0.5.0-spatail",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "experienceId": asset_id,
        "title": title,
        "sourcePrompt": prompt,
        "sourceInputs": [],
        "sourceFiles": [],
        "detectedDomain": "unknown",
        "environmentAssumptions": {},
        "spatialElements": spatial_elements,
        "relationships": [],
        "interactionPlan": {"interactions": []},
        "attentionPlan": [],
        "assetRequirements": [],
        "animations": [],
        "interactions": [],
        "sequences": [],
        "defaultSequenceId": None,
        "roomContract": None,
        "explanation": {"written": prompt, "intentSummary": ""},
        "mechanics": [],
        "presentation": {"layout": "stage_in_front", "ordering": []},
        "reasoningSummary": "Placeholder contract emitted by spatail_export_xr",
        "vocabularies": None,  # iOS app has the closed enums compiled in
    }
    path = os.path.join(stage_dir, "experience.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(contract, f, indent=2)
    return path


def _write_manifest(stage_dir, asset_id, title, prompt,
                     bbox_meters, supports_realscale, supports_tabletop):
    manifest = {
        "schemaVersion": "0.5.0-spatail-bundle",
        "experienceId": asset_id,
        "title": title,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "source": {
            "asset": asset_id,
            "prompt": "source/prompt.txt",
        },
        "files": {
            "experience": "experience.json",
            "scene": "scene.usdz",
            "primsIndex": "prims_index.json",
            "thumbnail": "hero/thumbnail.jpg",
        },
        "scene": {
            "unitScale": 1.0,
            "upAxis": "Y",
            "boundingBoxMeters": list(bbox_meters),
            "defaultViewerDistanceMeters": max(0.4, max(bbox_meters) * 2.0),
            "supportsRealScale": supports_realscale,
            "supportsTabletop": supports_tabletop,
        },
        "narrationLanguages": ["en"],
    }
    path = os.path.join(stage_dir, "manifest.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    return path


def _zip_bundle(stage_dir, out_path):
    """Zip <stage_dir>/* as <out_path>. .spatail is just a zip with extension."""
    if os.path.exists(out_path):
        os.remove(out_path)
    base = stage_dir
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(base):
            for fn in files:
                full = os.path.join(root, fn)
                rel = os.path.relpath(full, base)
                zf.write(full, rel)
    return out_path


# ────────────────────────────────────────────────────────────────────────
# Public entry point
# ────────────────────────────────────────────────────────────────────────

def export_xr_bundle(out_path, experience_json_path=None,
                      title=None, prompt=None, asset_id=None,
                      anim_frame_range=None,
                      assume_blender_units_are_cm=True):
    """Export the current scene as a .spatail bundle for the iOS player.

    out_path : full path ending in .spatail
    experience_json_path : if provided, copied into the bundle as-is.
                            Otherwise a placeholder contract is generated.
    title, prompt, asset_id : metadata for manifest + placeholder contract.
    anim_frame_range : (start, end) to bake; None for static export.
    assume_blender_units_are_cm : if True, scene is scaled to meters at export
                                    (USDZ standard). Scale factor 0.01.
    """
    if not out_path.lower().endswith(".spatail"):
        out_path += ".spatail"
    asset_id = asset_id or os.path.splitext(os.path.basename(out_path))[0]
    title = title or asset_id
    prompt = prompt or ""

    stage_dir = out_path + ".stage"
    if os.path.exists(stage_dir):
        shutil.rmtree(stage_dir)
    os.makedirs(stage_dir, exist_ok=True)
    os.makedirs(os.path.join(stage_dir, "source"), exist_ok=True)

    # 1. Prep scene
    _prep_scene_for_usdz()

    # 2. Collect meshes + build prims index
    meshes = _collect_logical_meshes()
    prim_to_eid, eid_to_prim = _build_prims_index(meshes)
    n_meshes = len(meshes)

    # 3. Bounding box BEFORE rescale (cm) → convert to meters
    lo_cm, hi_cm = _world_bbox()
    bbox_m = [(hi_cm.x - lo_cm.x) * (0.01 if assume_blender_units_are_cm else 1.0),
              (hi_cm.y - lo_cm.y) * (0.01 if assume_blender_units_are_cm else 1.0),
              (hi_cm.z - lo_cm.z) * (0.01 if assume_blender_units_are_cm else 1.0)]

    # 4. Bake animation if requested
    anim_meta = _bake_animation_if_needed(anim_frame_range)

    # 5. Export USDZ — meters_per_unit handles cm→m at export so the scene
    #     does not need to be rescaled in place.
    usdz_path = os.path.join(stage_dir, "scene.usdz")
    mpu = 0.01 if assume_blender_units_are_cm else 1.0
    _export_usdz(usdz_path, anim_meta, meters_per_unit=mpu)

    # 6. Hero frames (rendered against the current viewport state).
    try:
        _render_hero_frames(stage_dir)
    except Exception as e:
        print(f"[export_xr] hero render warning: {e}")

    # 9. prims_index.json
    prims_path = os.path.join(stage_dir, "prims_index.json")
    with open(prims_path, "w", encoding="utf-8") as f:
        json.dump({"primToElement": prim_to_eid,
                    "elementToPrim": eid_to_prim}, f, indent=2)

    # 10. experience.json — copy if provided, else placeholder
    if experience_json_path and os.path.exists(experience_json_path):
        shutil.copy2(experience_json_path,
                      os.path.join(stage_dir, "experience.json"))
    else:
        _write_placeholder_experience_json(stage_dir, asset_id, title, prompt,
                                            eid_to_prim)

    # 11. manifest.json
    _write_manifest(stage_dir, asset_id, title, prompt, bbox_m,
                     supports_realscale=True, supports_tabletop=True)

    # 12. source/prompt.txt
    with open(os.path.join(stage_dir, "source", "prompt.txt"),
              "w", encoding="utf-8") as f:
        f.write(prompt or "")

    # 13. Zip
    _zip_bundle(stage_dir, out_path)
    bundle_size = os.path.getsize(out_path)

    # 14. Clean up stage dir
    shutil.rmtree(stage_dir, ignore_errors=True)

    print(f"[export_xr] {n_meshes} meshes → {out_path} ({bundle_size/1024/1024:.2f} MB)")
    return {
        "bundle_path": out_path,
        "bundle_size_mb": round(bundle_size / 1024 / 1024, 2),
        "n_meshes": n_meshes,
        "bbox_m": bbox_m,
        "asset_id": asset_id,
    }


print("[spatail_export_xr] module loaded.")
