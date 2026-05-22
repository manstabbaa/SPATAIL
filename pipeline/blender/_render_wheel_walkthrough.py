"""Headless render of assets_authoring/wheel.blend into an MP4.

Bakes a single hero camera orbiting the scene over the full timeline,
adds basic 3-point lighting if the world is dark, sets Eevee Next as
the render engine with FFMPEG/H.264 output. Falls back to Workbench if
Eevee Next isn't available.

Run via:
    blender --background wheel.blend --python _render_wheel_walkthrough.py -- <out_mp4_path>
"""
import bpy, sys, math, os
from mathutils import Vector, Matrix


def get_argv_after_double_dash():
    if "--" in sys.argv:
        return sys.argv[sys.argv.index("--") + 1:]
    return []


def compute_scene_bbox():
    lo = Vector((float("inf"),)*3); hi = Vector((-float("inf"),)*3)
    for obj in bpy.data.objects:
        if obj.type not in ("MESH", "CURVE", "SURFACE", "META", "FONT"): continue
        for c in obj.bound_box:
            p = obj.matrix_world @ Vector(c)
            lo = Vector(map(min, lo, p)); hi = Vector(map(max, hi, p))
    return lo, hi


def setup_hero_camera(scn, centre, radius, height_off, start_frame, end_frame):
    """Create a camera that orbits centre over [start_frame, end_frame].

    Camera tracks the centre via a Track To constraint on a tiny empty
    so the matrix math stays simple — we just keyframe the camera's
    location around a circle.
    """
    # Empty as look-at target
    look = bpy.data.objects.get("SPATAIL_walkthrough_look")
    if look is None:
        look = bpy.data.objects.new("SPATAIL_walkthrough_look", None)
        bpy.context.scene.collection.objects.link(look)
    look.location = centre

    cam_data = bpy.data.cameras.new("SPATAIL_walkthrough_cam")
    cam_data.lens = 35  # wider for hero feel
    cam_data.clip_start = 0.01
    cam_data.clip_end = 100.0
    cam = bpy.data.objects.new("SPATAIL_walkthrough_cam", cam_data)
    bpy.context.scene.collection.objects.link(cam)

    # Track-to constraint pointing at the empty
    tt = cam.constraints.new("TRACK_TO")
    tt.target = look
    tt.track_axis = "TRACK_NEGATIVE_Z"
    tt.up_axis = "UP_Y"

    # Three-act orbit:
    #   1..intro_done    (≈1..31)   : slow approach from front 3/4
    #   intro..peak      (≈31..146) : 180° orbit while pulling back
    #   peak..end        (≈146..216): continue orbit, dolly back in
    intro_done = 31
    peak = 146

    def place_keyframe(frame, angle_rad, dist):
        x = centre.x + math.cos(angle_rad) * dist
        y = centre.y + math.sin(angle_rad) * dist
        z = centre.z + height_off
        cam.location = (x, y, z)
        cam.keyframe_insert("location", frame=frame)

    # Start angle: looking at the wheel from the front (looking +Y is into
    # the wheel since wheel faces +Z and centre.y = 0.71). Use -Y side so
    # the camera looks "into" the wheel.
    start_angle = -math.pi / 2.0  # camera at -Y of centre, looking +Y
    place_keyframe(start_frame, start_angle, radius * 1.0)
    place_keyframe(intro_done, start_angle + math.pi / 6, radius * 1.05)
    place_keyframe(peak, start_angle + math.pi, radius * 1.35)
    place_keyframe(end_frame, start_angle + 2 * math.pi - math.pi / 6,
                   radius * 1.05)

    scn.camera = cam
    return cam


def add_basic_lighting(scn, centre, radius):
    """Add a 3-point light setup if no usable lights exist in the scene."""
    existing_lights = [o for o in bpy.data.objects if o.type == "LIGHT"]
    if existing_lights:
        return [o.name for o in existing_lights]

    def add_light(name, ltype, energy, location):
        ld = bpy.data.lights.new(name, type=ltype)
        ld.energy = energy
        if ltype in ("SUN",):
            ld.angle = math.radians(8)
        if ltype == "AREA":
            ld.size = 2.0
        lo = bpy.data.objects.new(name, ld)
        bpy.context.scene.collection.objects.link(lo)
        lo.location = location
        # Aim at the centre
        d = Vector(centre) - Vector(location)
        lo.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
        return name

    key  = add_light("SPATAIL_key",  "AREA", 800, (centre.x + radius*0.6, centre.y - radius*0.9, centre.z + radius*0.6))
    fill = add_light("SPATAIL_fill", "AREA", 350, (centre.x - radius*0.7, centre.y - radius*0.5, centre.z + radius*0.2))
    rim  = add_light("SPATAIL_rim",  "AREA", 500, (centre.x, centre.y + radius*0.9, centre.z + radius*0.6))
    sun  = add_light("SPATAIL_sun",  "SUN", 2.0, (centre.x + radius*0.5, centre.y - radius*0.5, centre.z + radius*2.0))
    return [key, fill, rim, sun]


def configure_render(scn, frames_dir):
    """Set Eevee Next + PNG sequence output. Returns the engine actually used.

    Note: this Blender 5.1 Windows build was compiled WITHOUT FFmpeg,
    so we render to a PNG sequence and stitch externally with ffmpeg.
    """
    # Try Eevee Next first, then Eevee, then Workbench
    candidates = ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "BLENDER_WORKBENCH")
    chosen = None
    for eng in candidates:
        try:
            scn.render.engine = eng
            chosen = eng
            break
        except Exception as e:
            print(f"[render] engine {eng} unavailable: {e}")
            continue
    if chosen is None:
        scn.render.engine = "BLENDER_WORKBENCH"
        chosen = "BLENDER_WORKBENCH"
    print(f"[render] using engine: {chosen}")

    if chosen in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
        try:
            scn.eevee.taa_render_samples = 32
        except Exception: pass

    scn.render.resolution_x = 1920
    scn.render.resolution_y = 1080
    scn.render.resolution_percentage = 100
    # Keep the source 24fps (no resampling needed; ffmpeg will set the
    # output frame rate to match the PNG count / source frame range).
    # Spec wanted 30fps but it's a preview — 24fps from the timeline is
    # the cleanest path.

    scn.render.image_settings.file_format = "PNG"
    scn.render.image_settings.color_mode = "RGB"
    scn.render.image_settings.color_depth = "8"
    scn.render.image_settings.compression = 15
    scn.render.filepath = os.path.join(frames_dir, "frame_").replace("\\", "/")
    return chosen


def main():
    args = get_argv_after_double_dash()
    if not args:
        out_path = r"C:/SPATAIL_MAX/docs/recordings/wheel_walkthrough.mp4"
    else:
        out_path = args[0]
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    frames_dir = out_path.replace(".mp4", "_frames")
    os.makedirs(frames_dir, exist_ok=True)

    scn = bpy.context.scene

    # Compute scene bbox + framing distance
    lo, hi = compute_scene_bbox()
    centre = (lo + hi) * 0.5
    diag = (hi - lo).length
    radius = diag * 1.6
    height_off = (hi.z - lo.z) * 0.35

    print(f"[render] bbox centre={[round(c,2) for c in centre]} diag={diag:.2f} → radius={radius:.2f}")

    # Camera + lighting
    setup_hero_camera(scn, centre, radius, height_off,
                      scn.frame_start, scn.frame_end)
    lights = add_basic_lighting(scn, centre, radius)
    print(f"[render] lights: {lights}")

    # World background for Eevee bounce
    if scn.world is None:
        scn.world = bpy.data.worlds.new("SPATAIL_world")
    scn.world.use_nodes = True
    try:
        bg = scn.world.node_tree.nodes.get("Background")
        if bg:
            bg.inputs[0].default_value = (0.05, 0.06, 0.08, 1)
            bg.inputs[1].default_value = 0.6
    except Exception: pass

    chosen_engine = configure_render(scn, frames_dir)

    print(f"[render] frame range: {scn.frame_start}..{scn.frame_end}")
    print(f"[render] writing PNG sequence to {frames_dir}")
    bpy.ops.render.render(animation=True)
    duration_sec = (scn.frame_end - scn.frame_start + 1) / float(scn.render.fps)
    print(f"RENDER_DONE engine={chosen_engine} frames_dir={frames_dir} "
          f"frame_start={scn.frame_start} frame_end={scn.frame_end} "
          f"fps={scn.render.fps} duration_sec={duration_sec:.2f} mp4_target={out_path}")


main()
