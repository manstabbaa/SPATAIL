"""Headless render any .blend to an MP4. Honors the authored camera + frame range.

Usage:
    blender --background <blend> --python _render_blend_to_mp4.py -- <out_mp4_path>

Behaviour:
  - Renders the scene's frame range (start..end) at the .blend's fps.
  - Camera selection:
      1. Use scene.camera if set.
      2. Else pick the first CAMERA object in the scene.
      3. Else build a temporary orbit camera (3 keyed beats: wide / mid / close).
  - If no lights exist, adds a 3-point setup so Eevee has something to work with.
  - Engine: BLENDER_EEVEE_NEXT → BLENDER_EEVEE → BLENDER_WORKBENCH (first one
    the build accepts).
  - Outputs PNG sequence to <out>_frames/ — caller stitches to MP4.
"""
import bpy, sys, math, os
from mathutils import Vector


def get_argv_after_double_dash():
    if "--" in sys.argv:
        return sys.argv[sys.argv.index("--") + 1:]
    return []


def compute_scene_bbox():
    lo = Vector((float("inf"),)*3); hi = Vector((-float("inf"),)*3)
    for obj in bpy.data.objects:
        if obj.type not in ("MESH", "CURVE", "SURFACE", "META", "FONT"):
            continue
        if not obj.visible_get():
            continue
        for c in obj.bound_box:
            p = obj.matrix_world @ Vector(c)
            lo = Vector(map(min, lo, p)); hi = Vector(map(max, hi, p))
    return lo, hi


def setup_fallback_camera(scn, centre, radius, height_off, start_frame, end_frame):
    """Three-beat orbit fallback if no authored camera exists."""
    look = bpy.data.objects.new("SPATAIL_walkthrough_look", None)
    bpy.context.scene.collection.objects.link(look)
    look.location = centre
    cam_data = bpy.data.cameras.new("SPATAIL_walkthrough_cam")
    cam_data.lens = 35
    cam_data.clip_start = 0.01
    cam_data.clip_end = 500.0
    cam = bpy.data.objects.new("SPATAIL_walkthrough_cam", cam_data)
    bpy.context.scene.collection.objects.link(cam)
    tt = cam.constraints.new("TRACK_TO")
    tt.target = look; tt.track_axis = "TRACK_NEGATIVE_Z"; tt.up_axis = "UP_Y"

    span = max(1, end_frame - start_frame)
    mid_frame = start_frame + span // 2

    def kf(frame, angle, dist):
        cam.location = (centre.x + math.cos(angle) * dist,
                        centre.y + math.sin(angle) * dist,
                        centre.z + height_off)
        cam.keyframe_insert("location", frame=frame)

    start_angle = -math.pi / 2
    kf(start_frame, start_angle, radius * 1.0)            # wide
    kf(mid_frame,   start_angle + math.pi, radius * 1.3)  # mid (opposite side, pulled out)
    kf(end_frame,   start_angle + 2*math.pi - math.pi/6, radius * 1.0)  # close

    scn.camera = cam
    return cam


def add_basic_lighting_if_needed(scn, centre, radius):
    existing = [o for o in bpy.data.objects if o.type == "LIGHT"]
    if existing:
        return {"added": False, "existing": [o.name for o in existing]}

    def add(name, ltype, energy, location):
        ld = bpy.data.lights.new(name, type=ltype)
        ld.energy = energy
        if ltype == "AREA":
            ld.size = max(2.0, radius * 0.2)
        lo = bpy.data.objects.new(name, ld)
        bpy.context.scene.collection.objects.link(lo)
        lo.location = location
        d = Vector(centre) - Vector(location)
        lo.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
        return name

    added = [
        add("SPATAIL_key",  "AREA", 800, (centre.x + radius*0.6, centre.y - radius*0.9, centre.z + radius*0.6)),
        add("SPATAIL_fill", "AREA", 350, (centre.x - radius*0.7, centre.y - radius*0.5, centre.z + radius*0.2)),
        add("SPATAIL_rim",  "AREA", 500, (centre.x, centre.y + radius*0.9, centre.z + radius*0.6)),
        add("SPATAIL_sun",  "SUN", 2.0, (centre.x + radius*0.5, centre.y - radius*0.5, centre.z + radius*2.0)),
    ]
    return {"added": True, "lights": added}


def configure_render(scn, frames_dir, source_fps=None):
    candidates = ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "BLENDER_WORKBENCH")
    chosen = None
    for eng in candidates:
        try:
            scn.render.engine = eng
            chosen = eng
            break
        except Exception as e:
            print(f"[render] engine {eng} unavailable: {e}")
    if chosen is None:
        scn.render.engine = "BLENDER_WORKBENCH"
        chosen = "BLENDER_WORKBENCH"
    print(f"[render] using engine: {chosen}")

    if chosen in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
        try: scn.eevee.taa_render_samples = 32
        except Exception: pass

    scn.render.resolution_x = 1920
    scn.render.resolution_y = 1080
    scn.render.resolution_percentage = 100
    if source_fps:
        scn.render.fps = source_fps

    scn.render.image_settings.file_format = "PNG"
    scn.render.image_settings.color_mode = "RGB"
    scn.render.image_settings.color_depth = "8"
    scn.render.image_settings.compression = 15
    scn.render.filepath = os.path.join(frames_dir, "frame_").replace("\\", "/")
    return chosen


def main():
    args = get_argv_after_double_dash()
    out_path = args[0] if args else r"C:/SPATAIL_MAX/docs/recordings/blend_render.mp4"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    frames_dir = out_path.replace(".mp4", "_frames")
    os.makedirs(frames_dir, exist_ok=True)

    scn = bpy.context.scene
    source_fps = scn.render.fps
    start_f, end_f = scn.frame_start, scn.frame_end

    # Camera selection
    camera_source = "authored_scene_camera"
    if scn.camera is None:
        # Pick first CAMERA object if any
        cams = [o for o in bpy.data.objects if o.type == "CAMERA"]
        if cams:
            scn.camera = cams[0]
            camera_source = f"first_camera_object:{cams[0].name}"
        else:
            # Fallback orbit
            lo, hi = compute_scene_bbox()
            centre = (lo + hi) * 0.5
            diag = (hi - lo).length
            radius = diag * 1.6 if diag > 0 else 5.0
            height_off = (hi.z - lo.z) * 0.35 if (hi.z > lo.z) else 1.0
            setup_fallback_camera(scn, centre, radius, height_off, start_f, end_f)
            camera_source = "fallback_orbit_3beat"
            print("[render] no authored camera — built fallback orbit")

    # Lights (only add if scene has none)
    lo, hi = compute_scene_bbox()
    centre = (lo + hi) * 0.5
    diag = (hi - lo).length if (hi - lo).length > 0 else 5.0
    light_info = add_basic_lighting_if_needed(scn, centre, diag * 0.8)

    # World background — make sure something is set
    if scn.world is None:
        scn.world = bpy.data.worlds.new("SPATAIL_world")
    try:
        scn.world.use_nodes = True
        bg = scn.world.node_tree.nodes.get("Background")
        if bg:
            # Only override if it looks unset (default white)
            current = bg.inputs[0].default_value
            if abs(current[0] - 0.05) > 0.001 and abs(current[0] - current[1]) < 0.001 and current[0] > 0.5:
                bg.inputs[0].default_value = (0.05, 0.06, 0.08, 1)
                bg.inputs[1].default_value = 0.6
    except Exception: pass

    chosen_engine = configure_render(scn, frames_dir, source_fps=source_fps)

    print(f"[render] camera: {scn.camera.name} ({camera_source})")
    print(f"[render] lights: {light_info}")
    print(f"[render] frame range: {start_f}..{end_f} @ {source_fps}fps")
    print(f"[render] writing PNG sequence to {frames_dir}")
    bpy.ops.render.render(animation=True)
    duration_sec = (end_f - start_f + 1) / float(source_fps)
    print(f"RENDER_DONE engine={chosen_engine} frames_dir={frames_dir} "
          f"frame_start={start_f} frame_end={end_f} "
          f"fps={source_fps} duration_sec={duration_sec:.2f} "
          f"camera_source={camera_source} mp4_target={out_path}")


main()
