---
name: spatail-multiview-render
description: Render a Blender scene from four canonical cameras (perspective, front, right, top) at a given frame and compose them into a single 2×2 contact-sheet PNG plus the four individual tile images. One ortho-style perspective camera plus three orthographic axis cameras share a common target and asset radius so every render is framed identically. Used for fast visual verification during rig/animation iteration when one camera angle can't reveal what's wrong.
when_to_use: When you need to SEE the scene at one or more frames without guessing which angle hides the bug. Especially useful when paired with spatail-segment-colors to identify which specific rod/piston/throw is misbehaving. Run after rig + animate so the scene actually has motion to inspect, or at frame 1 to verify the rest pose. Also useful for batch-rendering across a cycle (e.g. frames 1, 20, 40, 60, 80, 100) to spot issues that only appear at specific crank angles.
---

# Multiview-Render Skill

## What this exists for

A single camera can hide a lot. A rod swinging past the wrong centre at frame 30 is invisible from the front but obvious from the top; pistons drifting out of bore plane don't show in the right ortho but jump out from the perspective view. Iterating with one camera at a time wastes round-trips.

Multiview-render fires four cameras at the same target, composes the results, and gives you one image to look at — saving four screenshots' worth of decisions per iteration.

## What it does

For a given target point and asset radius `size`:

- **`SPATAIL_cam_persp`** — perspective lens 35mm, placed at `(target + d, target − d, target + 0.6d)` where `d = size · 3.0`, pointed at the target. The hero shot.
- **`SPATAIL_cam_front`** — orthographic, looking down −Y at the target, ortho scale `size · 2.5`.
- **`SPATAIL_cam_right`** — orthographic, looking down −X (from +X side).
- **`SPATAIL_cam_top`** — orthographic, looking down −Z (from above).

Each camera renders its tile (default 480×360, 100% scale) as a PNG. The four tiles are composited into a 2×2 grid via Pillow:

    +----------------+----------------+
    | PERSPECTIVE    | FRONT (−Y)     |
    +----------------+----------------+
    | RIGHT (+X)     | TOP (+Z)       |
    +----------------+----------------+

with a black backdrop label per tile for readability. If Pillow is missing the skill falls back to writing a `*.manifest.txt` listing the four tile paths.

`multiview_render_cycle(out_dir, frames=[...], target, size)` runs the same render at each frame in the list, producing one grid PNG per frame for batch inspection of an animated cycle.

## Inputs

- A Blender scene with geometry already placed/animated.
- `target` (Vector) — what to look at. Pass scene centroid for whole-asset shots; pass a specific part's world translation to zoom in on one cylinder.
- `size` — approximate asset radius, used to choose camera distance and ortho scale. For the V10 engine this is ~20 (cm).
- Optional `frame` (or list of frames for the cycle variant), `tile_w`, `tile_h`, `save_tiles_dir`.

## Outputs

- One composite PNG at `out_path` (2×2 grid, labelled).
- Four individual tile PNGs in `<out_dir>/_tiles/f{frame:04d}_{persp|front|right|top}.png` (the task spec calls these "4 individual JPGs" — implementation writes PNG; same role).
- Four reusable camera objects in the scene: `SPATAIL_cam_persp`, `SPATAIL_cam_front`, `SPATAIL_cam_right`, `SPATAIL_cam_top`. Re-runs reuse them.
- The scene's previous active camera is restored after rendering.

## How to invoke

```python
exec(open(r"C:/SPATAIL_MAX/pipeline/blender/spatail_multiview_render.py").read())

# Single frame
multiview_render(
    out_path=r".../grid_f001.png",
    frame=1,
    target=Vector((cx, cy, cz)),
    size=20,
)

# Full cycle
multiview_render_cycle(
    out_dir=r".../verify/",
    frames=[1, 20, 40, 60, 80, 100],
    target=Vector((cx, cy, cz)),
    size=20,
)
```

## Anti-goals

- Do not auto-detect the target. Caller passes `target` and `size` because the right framing depends on what you're inspecting (whole asset vs one cylinder).
- Do not add lights, materials, or environment. Multiview renders whatever shading the scene already has — typically the viewport colours from `spatail-segment-colors` or whatever materials are loaded. For final-quality renders use a dedicated render skill.
- Do not animate the cameras. Cameras are static per render call; for moving cameras add a wrapper.
- Do not write video. Output is PNGs only. Stitch frames externally if you need an MP4/GIF.

## Composition note

Pipeline position: `treat-mesh` → `classify-engine` → `rig-engine` → `calibrate-assembly` → `animate-engine` → `segment-colors` → **`multiview-render`** (verification) → final render / USDZ export.

Multiview is a verification-only skill — it mutates only the camera list in the scene and writes PNGs. Safe to run at any point after geometry is placed; most useful immediately after segment-colors so each part is visually distinguishable in the grid.
