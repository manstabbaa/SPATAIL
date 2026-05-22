"""
spatail_multiview_render.py — render 4 views (perspective, front, right,
top) of the current scene into a 2x2 grid PNG. Lets us inspect the
geometry from every angle at once instead of guessing what one camera
hides.

USAGE in live Blender:
    exec(open(r".../spatail_multiview_render.py").read())
    multiview_render(
        out_path=r".../grid_f001.png",
        frame=1,
        target=Vector((cx, cy, cz)),  # what to look at
        size=20,                      # asset radius to frame
    )

Or batch over a cycle:
    multiview_render_cycle(
        out_dir=r".../",
        frames=[1, 20, 40, 60, 80, 100],
        target=..., size=...,
    )

The grid layout is:
    +--------------+--------------+
    | perspective  |    front     |
    +--------------+--------------+
    |    right     |     top      |
    +--------------+--------------+
"""

import bpy, math, os
from mathutils import Vector


def _make_camera(name, location, look_at, ortho=False, ortho_scale=40.0):
    cam_data = bpy.data.cameras.get(name) or bpy.data.cameras.new(name)
    if ortho:
        cam_data.type = "ORTHO"
        cam_data.ortho_scale = ortho_scale
    else:
        cam_data.type = "PERSP"
        cam_data.lens = 35
    cam_obj = bpy.data.objects.get(name)
    if cam_obj is None:
        cam_obj = bpy.data.objects.new(name, cam_data)
        bpy.context.collection.objects.link(cam_obj)
    else:
        cam_obj.data = cam_data
    cam_obj.location = location
    direction = Vector(look_at) - Vector(location)
    cam_obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    return cam_obj


def _setup_cameras(target, size):
    """Place 4 cameras around `target`. `size` = roughly the radius of
    the asset (used to choose camera distance + ortho scale)."""
    d = size * 3.0  # perspective distance
    ortho = size * 2.5
    cams = {
        "persp": _make_camera("SPATAIL_cam_persp",
                              (target.x + d, target.y - d, target.z + d * 0.6),
                              target, ortho=False),
        "front": _make_camera("SPATAIL_cam_front",
                              (target.x, target.y - d * 1.3, target.z),
                              target, ortho=True, ortho_scale=ortho),
        "right": _make_camera("SPATAIL_cam_right",
                              (target.x + d * 1.3, target.y, target.z),
                              target, ortho=True, ortho_scale=ortho),
        "top":   _make_camera("SPATAIL_cam_top",
                              (target.x, target.y, target.z + d * 1.3),
                              target, ortho=True, ortho_scale=ortho),
    }
    return cams


def _render_one(cam, out_path, w=480, h=360):
    scn = bpy.context.scene
    scn.camera = cam
    scn.render.resolution_x = w
    scn.render.resolution_y = h
    scn.render.resolution_percentage = 100
    scn.render.filepath = out_path
    bpy.ops.render.render(write_still=True)


def _composite_2x2(tile_paths, out_path, labels=None):
    """Combine 4 PNGs into a 2x2 grid via Pillow if available, else use
    Blender's compositor."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        # Fallback: just keep them as separate files + write a manifest
        manifest = out_path.replace(".png", ".manifest.txt")
        with open(manifest, "w") as f:
            for nm, p in zip(("persp","front","right","top"), tile_paths):
                f.write(f"{nm}: {p}\n")
        return manifest
    tiles = [Image.open(p) for p in tile_paths]
    w, h = tiles[0].size
    grid = Image.new("RGB", (w * 2, h * 2), (24, 24, 28))
    grid.paste(tiles[0], (0, 0))      # persp top-left
    grid.paste(tiles[1], (w, 0))      # front top-right
    grid.paste(tiles[2], (0, h))      # right bottom-left
    grid.paste(tiles[3], (w, h))      # top  bottom-right
    if labels:
        draw = ImageDraw.Draw(grid)
        try:
            font = ImageFont.truetype("arial.ttf", 18)
        except Exception:
            font = ImageFont.load_default()
        positions = [(8, 8), (w + 8, 8), (8, h + 8), (w + 8, h + 8)]
        for (x, y), lbl in zip(positions, labels):
            # backdrop for readability
            draw.rectangle([x - 2, y - 2, x + 90, y + 22], fill=(0, 0, 0))
            draw.text((x, y), lbl, fill=(255, 255, 255), font=font)
    grid.save(out_path)
    return out_path


def multiview_render(out_path, frame=None, target=None, size=20,
                     tile_w=480, tile_h=360, save_tiles_dir=None):
    """Render 4 views at the current (or given) frame, return composite path."""
    scn = bpy.context.scene
    if frame is not None: scn.frame_set(frame)
    if target is None:
        # Auto-target: scene centre via active object or origin
        target = Vector((0, 0, 0))
    target = Vector(target)

    saved_cam = scn.camera
    cams = _setup_cameras(target, size)
    bpy.context.view_layer.update()

    tmp_dir = save_tiles_dir or os.path.join(os.path.dirname(out_path), "_tiles")
    os.makedirs(tmp_dir, exist_ok=True)
    tag = f"f{frame:04d}" if frame is not None else "_"
    tile_paths = []
    for nm in ("persp", "front", "right", "top"):
        p = os.path.join(tmp_dir, f"{tag}_{nm}.png").replace("\\", "/")
        _render_one(cams[nm], p, w=tile_w, h=tile_h)
        tile_paths.append(p)

    scn.camera = saved_cam
    return _composite_2x2(tile_paths, out_path,
                          labels=("PERSPECTIVE", "FRONT (−Y)", "RIGHT (+X)", "TOP (+Z)"))


def multiview_render_cycle(out_dir, frames, target=None, size=20,
                           tile_w=480, tile_h=360):
    """Render a multi-view grid for each frame in `frames`."""
    os.makedirs(out_dir, exist_ok=True)
    results = []
    for f in frames:
        out_path = os.path.join(out_dir, f"grid_f{f:03d}.png").replace("\\", "/")
        results.append(multiview_render(out_path, frame=f, target=target,
                                        size=size, tile_w=tile_w, tile_h=tile_h))
    return results


print("[spatail_multiview_render] module loaded.")
