"""motion.py - SPATAIL's modular motion systems.

Pre-built, parameterized, reusable animation primitives that bake REAL physics to
Blender keyframes. Demos compose these instead of hand-rolling keyframes, so:
  * every demo gets correct, loop-closed motion for free;
  * the StudioSceneContract can name the motion module driving each part, which
    the iOS/RealityKit player can later reproduce natively;
  * new topics reuse motion ("a planet orbits" = `oscillate` on two axes, "a
    pendulum swings" = `oscillate` of rotation) instead of starting over.

Every function takes a Blender object + the frame loop (start, cycle) and bakes
keyframes that return to the start pose at `cycle` so the clip loops seamlessly.
All return a small dict describing the module + params for the contract.

Frame: Blender-native metres, +Z up. Interpolation is set by the caller
(build_studio sets LINEAR globally so constant velocity stays constant; modules
that need easing set their own keyframe handles).
"""
from __future__ import annotations

import math
from mathutils import Vector

G = 9.81


def _kloc(o, f, p):
    o.location = Vector(p); o.keyframe_insert("location", frame=f)


def _krot(o, f, r):
    o.rotation_euler = Vector(r); o.keyframe_insert("rotation_euler", frame=f)


def constant_velocity(obj, start, cycle, axis=(1, 0, 0), span_m=0.4, origin=(0, 0, 0)):
    """Inertia: glide at constant speed out and back (frictionless). Loop-closed.
    Velocity is literally constant because keyframes are LINEAR."""
    a = Vector(axis).normalized()
    o = Vector(origin)
    mid = start + (cycle - start) // 2
    _kloc(obj, start, o - a * (span_m / 2))
    _kloc(obj, mid,   o + a * (span_m / 2))
    _kloc(obj, cycle, o - a * (span_m / 2))
    return {"module": "constant_velocity", "axis": list(a), "span_m": span_m}


def accelerate_down_slope(obj, start, cycle, top, down, length_m, radius_m,
                          angle_rad, down_frac=0.82):
    """F = ma: accelerate from rest down a slope under gravity (a = g.sin0),
    distance s = 1/2 a t^2, with rolling rotation, then ease back to loop.
    `top`/`down` are Vectors in the object's parent frame."""
    a = G * math.sin(angle_rad)
    t_bottom = math.sqrt(2 * length_m / a) if a > 1e-6 else 1.0
    f_down = start + int((cycle - start) * down_frac)
    for f in range(start, f_down + 1):
        tau = (f - start) / max(1, (f_down - start)) * t_bottom
        s = min(0.5 * a * tau * tau, length_m)
        p = top + down * s
        _kloc(obj, f, p); _krot(obj, f, (0.0, -s / radius_m, 0.0))
    for k, f in enumerate(range(f_down + 1, cycle + 1)):
        g = k / max(1, (cycle - f_down))
        p = top + down * (length_m * (1 - g))
        _kloc(obj, f, p); _krot(obj, f, (0.0, -(length_m * (1 - g)) / radius_m, 0.0))
    return {"module": "accelerate_down_slope", "a_mps2": round(a, 3),
            "length_m": length_m, "angle_deg": round(math.degrees(angle_rad), 1)}


def equal_opposite_recoil(obj, start, cycle, origin, axis, distance_m,
                          out_frac=0.46, hold_frac=0.12):
    """Action-reaction: shove out along `axis` by `distance_m`, hold, return.
    Caller invokes once per body with each body's own distance (lighter body =
    larger distance) — the equal-and-opposite split is the caller's physics."""
    a = Vector(axis).normalized()
    o = Vector(origin)
    apart = start + int((cycle - start) * out_frac)
    hold = int((cycle - start) * hold_frac)
    _kloc(obj, start, o)
    _kloc(obj, apart, o + a * distance_m)
    _kloc(obj, apart + hold, o + a * distance_m)
    _kloc(obj, cycle, o)
    return {"module": "equal_opposite_recoil", "distance_m": distance_m,
            "axis": list(a)}


def spin(obj, start, cycle, axis="y", turns=1.0, distance_coupled_m=None,
         radius_m=None):
    """Rotate steadily. If distance_coupled_m + radius_m given, spin is rolling-
    coupled (angle = distance / radius) instead of a fixed number of turns."""
    if distance_coupled_m is not None and radius_m:
        ang = distance_coupled_m / radius_m
    else:
        ang = 2 * math.pi * turns
    idx = {"x": 0, "y": 1, "z": 2}[axis]
    r0 = [0, 0, 0]; r1 = [0, 0, 0]; r1[idx] = ang
    _krot(obj, start, r0); _krot(obj, cycle, r1)
    return {"module": "spin", "axis": axis, "radians": round(ang, 3)}


def oscillate(obj, start, cycle, channel="location", axis=(0, 0, 1),
              amplitude=0.1, cycles=1.0, origin=(0, 0, 0)):
    """Sinusoidal back-and-forth on a location or rotation channel (pendulum,
    wave, orbit component). Samples a sine so it's smooth and loop-closed."""
    a = Vector(axis)
    o = Vector(origin)
    steps = max(8, int((cycle - start)))
    for i in range(steps + 1):
        f = start + i
        if f > cycle:
            break
        s = math.sin(2 * math.pi * cycles * (i / steps)) * amplitude
        if channel == "location":
            _kloc(obj, f, o + a * s)
        else:
            r = [0, 0, 0]
            for j in range(3):
                r[j] = a[j] * s
            _krot(obj, f, r)
    return {"module": "oscillate", "channel": channel, "axis": list(a),
            "amplitude": amplitude, "cycles": cycles}


REGISTRY = {
    "constant_velocity": constant_velocity,
    "accelerate_down_slope": accelerate_down_slope,
    "equal_opposite_recoil": equal_opposite_recoil,
    "spin": spin,
    "oscillate": oscillate,
}
