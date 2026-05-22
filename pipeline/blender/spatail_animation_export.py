"""
spatail_animation_export.py — Blender-first SPATAIL animation exporter.

Reads a `.blend` authored per docs/SPATAIL_BLENDER_AUTHORING.md and emits
three artefacts under <output_dir>/<assetId>/:

    <assetId>.glb                 # GLB with baked transforms + morph + skin
    <assetId>.animations.json     # everything glTF can't carry:
                                  #   - material params over time
                                  #   - light intensity / color over time
                                  #   - camera path samples (location + lookAt)
                                  #   - interaction wiring derived from strip names
    <assetId>.sequence_hints.json # ordered sequence steps the planner drops
                                  # into contract.sequences[]

Run:
    blender --background <file>.blend \
        --python pipeline/blender/spatail_animation_export.py \
        -- <output_dir> <assetId> [<config_json>]

The config_json (if given) is the resolved spatail.config.json contents
passed straight through by the Node wrapper. Defaults match the schema.
"""

import json
import os
import re
import sys

try:
    import bpy        # type: ignore
    from mathutils import Vector  # type: ignore
except ImportError:
    bpy = None
    Vector = None


SPATAIL_META = "SPATAIL_meta"

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_CONFIG = {
    "animation": {
        "frameRate": 30,
        "bakeStepFrames": 1,
        "materialBakeMode": "continuous",  # "keyframe" | "continuous"
        "cameraPathSamples": 60,
    },
}


def merge_config(user):
    """Shallow-merge user config over defaults; only keys we know about."""
    cfg = {"animation": dict(DEFAULT_CONFIG["animation"])}
    if isinstance(user, dict) and isinstance(user.get("animation"), dict):
        for k, v in user["animation"].items():
            if k in cfg["animation"]:
                cfg["animation"][k] = v
    return cfg


# ---------------------------------------------------------------------------
# Name conventions — kept in lockstep with docs/SPATAIL_BLENDER_AUTHORING.md
# ---------------------------------------------------------------------------

SEQ_PREFIX     = "seq."
INTER_PREFIX   = "interaction."
DEFAULT_PREFIX = "default."
ATT_CAM_COLL_PREFIX = "attention.cam."
MARKER_CUE_PREFIX = "cue."

SEQ_RE = re.compile(
    r"^seq\.(?P<seq>[A-Za-z0-9_\-]+)\.(?P<order>\d{2})\.(?P<label>[A-Za-z0-9_\-]+)$"
)


def slug(s):
    return re.sub(r"[^A-Za-z0-9_\-]+", "_", str(s).strip()).strip("_") or "untitled"


# ---------------------------------------------------------------------------
# Strip walking
# ---------------------------------------------------------------------------

def iter_nla_strips():
    """Yields (object, track, strip) for every NLA strip in the file."""
    if bpy is None:
        return
    for obj in bpy.data.objects:
        if not obj.animation_data:
            continue
        for track in obj.animation_data.nla_tracks:
            for strip in track.strips:
                yield obj, track, strip


def classify_strip(obj, track, strip, fps):
    """Returns a dict describing the strip's contract role, or None."""
    name = strip.name
    f0, f1 = strip.frame_start, strip.frame_end
    duration_sec = max((f1 - f0) / fps, 0.0)
    base = {
        "name": name,
        "object": obj.name,
        "track": track.name,
        "frameStart": int(f0),
        "frameEnd": int(f1),
        "duration": round(duration_sec, 4),
        "actionName": strip.action.name if strip.action else None,
    }

    m = SEQ_RE.match(name)
    if m:
        return {
            "role": "sequence_step",
            "sequenceId": m.group("seq"),
            "order": int(m.group("order")),
            "label": m.group("label"),
            **base,
        }
    if name.startswith(INTER_PREFIX):
        return {
            "role": "interaction",
            "interactionId": name[len(INTER_PREFIX):],
            **base,
        }
    if name.startswith(DEFAULT_PREFIX):
        return {
            "role": "default",
            "label": name[len(DEFAULT_PREFIX):],
            **base,
        }
    return None


# ---------------------------------------------------------------------------
# Material / light sampling
# ---------------------------------------------------------------------------

def sample_material_tracks(strip_frame_range, bake_step, mode):
    """For every material in the file, sample animatable parameters across
    the supplied frame range. Returns list of {material, parameter, samples}.
    """
    if bpy is None:
        return []
    f0, f1 = strip_frame_range
    if mode == "keyframe":
        frames = collect_material_keyframes_in_range(f0, f1)
    else:
        frames = list(range(int(f0), int(f1) + 1, max(1, int(bake_step))))
    out = []
    for mat in bpy.data.materials:
        if not mat.use_nodes or not mat.node_tree:
            continue
        for node in mat.node_tree.nodes:
            for sock in getattr(node, "inputs", []) or []:
                # Only animatable, simple-typed sockets we know how to apply.
                if sock.type not in ("VALUE", "RGBA"):
                    continue
                samples = []
                for f in frames:
                    bpy.context.scene.frame_set(int(f))
                    val = sock.default_value
                    if hasattr(val, "__len__"):
                        val = [float(c) for c in val]
                    else:
                        val = float(val)
                    samples.append({"f": int(f), "v": val})
                if values_constant(samples):
                    continue
                out.append({
                    "material": mat.name,
                    "node": node.name,
                    "input": sock.name,
                    "type": sock.type,
                    "samples": samples,
                })
    return out


def collect_material_keyframes_in_range(f0, f1):
    """Frames that actually carry a material keyframe inside [f0,f1].
    Falls back to {f0, f1} if no fcurves are present so the track still
    has endpoints to interpolate."""
    if bpy is None:
        return [f0, f1]
    found = set()
    for mat in bpy.data.materials:
        anim = mat.node_tree.animation_data if (mat.use_nodes and mat.node_tree) else None
        if not anim or not anim.action:
            continue
        for fc in anim.action.fcurves:
            for kp in fc.keyframe_points:
                f = int(kp.co[0])
                if f0 <= f <= f1:
                    found.add(f)
    if not found:
        return [int(f0), int(f1)]
    return sorted(found)


def values_constant(samples):
    if len(samples) < 2:
        return True
    first = samples[0]["v"]
    for s in samples[1:]:
        if s["v"] != first:
            return False
    return True


def sample_light_tracks(strip_frame_range, bake_step):
    if bpy is None:
        return []
    f0, f1 = strip_frame_range
    frames = list(range(int(f0), int(f1) + 1, max(1, int(bake_step))))
    out = []
    for obj in bpy.data.objects:
        if obj.type != "LIGHT":
            continue
        samples_e = []
        samples_c = []
        for f in frames:
            bpy.context.scene.frame_set(int(f))
            samples_e.append({"f": int(f), "v": float(obj.data.energy)})
            samples_c.append({"f": int(f), "v": [float(c) for c in obj.data.color]})
        if not values_constant(samples_e):
            out.append({"light": obj.name, "parameter": "energy", "samples": samples_e})
        if not values_constant(samples_c):
            out.append({"light": obj.name, "parameter": "color", "samples": samples_c})
    return out


# ---------------------------------------------------------------------------
# Camera path collections
# ---------------------------------------------------------------------------

def find_attention_camera(sequence_id):
    coll_name = ATT_CAM_COLL_PREFIX + sequence_id
    coll = bpy.data.collections.get(coll_name) if bpy else None
    if not coll:
        return None, []
    camera = None
    lookats = []
    for obj in coll.all_objects:
        if obj.type == "CAMERA" and camera is None:
            camera = obj
        elif obj.type == "EMPTY" and obj.name.startswith("lookat."):
            lookats.append(obj)
    return camera, lookats


def sample_camera_path(sequence_id, strip_frame_range, samples_per_sec, fps):
    camera, lookats = find_attention_camera(sequence_id)
    if camera is None:
        return None
    f0, f1 = strip_frame_range
    sec = max((f1 - f0) / fps, 0.001)
    n = max(2, int(round(sec * samples_per_sec)))
    out = []
    for i in range(n):
        f = f0 + (f1 - f0) * (i / (n - 1))
        bpy.context.scene.frame_set(int(round(f)))
        loc = camera.matrix_world.translation
        # Look-at: prefer the closest authored lookat. empty by frame_order;
        # if none, fall back to the camera's -Z direction at a fixed reach.
        look = None
        if lookats:
            # Use the lookat that sorts to position i along the path; if
            # there's only one, it's the constant target.
            idx = min(i * len(lookats) // n, len(lookats) - 1)
            look = lookats[idx].matrix_world.translation
        else:
            forward = camera.matrix_world.to_3x3() @ Vector((0, 0, -1))
            look = loc + forward * 1.5
        out.append({
            "t": round((i / (n - 1)) * sec, 4),
            "pos": [float(loc.x), float(loc.y), float(loc.z)],
            "lookAt": [float(look.x), float(look.y), float(look.z)],
        })
    return {"sequenceId": sequence_id, "samples": out}


# ---------------------------------------------------------------------------
# Marker cues
# ---------------------------------------------------------------------------

def collect_markers(fps):
    if bpy is None:
        return []
    out = []
    for m in bpy.context.scene.timeline_markers:
        if not m.name.startswith(MARKER_CUE_PREFIX):
            continue
        bits = m.name[len(MARKER_CUE_PREFIX):].split(".", 1)
        seq_id = bits[0] if bits else None
        event = bits[1] if len(bits) > 1 else "cue"
        out.append({
            "sequenceId": seq_id,
            "event": event,
            "frame": int(m.frame),
            "atSecond": round(float(m.frame) / fps, 4),
        })
    return out


# ---------------------------------------------------------------------------
# SPATAIL_meta
# ---------------------------------------------------------------------------

def read_meta():
    if bpy is None:
        return {}
    obj = bpy.data.objects.get(SPATAIL_META)
    if obj is None:
        return {}
    out = {}
    for k in obj.keys():
        if k.startswith("_"):
            continue
        try:
            out[k] = json.loads(json.dumps(obj[k]))
        except Exception:
            try:
                out[k] = obj[k]
            except Exception:
                pass
    return out


# ---------------------------------------------------------------------------
# Export entry
# ---------------------------------------------------------------------------

def export_glb(output_path):
    if bpy is None:
        return False
    # Push every pinned NLA strip down into baked tracks so the GLB
    # contains the full motion the author saw in the timeline.
    bpy.ops.export_scene.gltf(
        filepath=output_path,
        export_format="GLB",
        export_apply=True,
        export_animations=True,
        export_force_sampling=True,
        export_nla_strips=True,
        export_morph=True,
        export_skins=True,
    )
    return True


def main():
    if bpy is None:
        print("[spatail_animation_export] must run inside Blender (--python)",
              file=sys.stderr)
        sys.exit(2)

    try:
        argv = sys.argv[sys.argv.index("--") + 1:]
    except ValueError:
        argv = []
    if len(argv) < 2:
        print("usage: blender --background <file>.blend --python "
              "spatail_animation_export.py -- <output_dir> <assetId> [<config_json>]",
              file=sys.stderr)
        sys.exit(2)

    output_dir = argv[0]
    asset_id = argv[1]
    cfg_str = argv[2] if len(argv) >= 3 else "{}"
    try:
        cfg = merge_config(json.loads(cfg_str))
    except Exception:
        cfg = merge_config({})
    bake_step = int(cfg["animation"]["bakeStepFrames"])
    mat_mode  = cfg["animation"]["materialBakeMode"]
    cam_sps   = int(cfg["animation"]["cameraPathSamples"])
    fps = bpy.context.scene.render.fps / max(bpy.context.scene.render.fps_base, 1e-6)

    out_root = os.path.join(output_dir, asset_id)
    os.makedirs(out_root, exist_ok=True)
    glb_path  = os.path.join(out_root, "%s.glb" % asset_id)
    anim_path = os.path.join(out_root, "%s.animations.json" % asset_id)
    seq_path  = os.path.join(out_root, "%s.sequence_hints.json" % asset_id)

    meta = read_meta()
    glb_ok = export_glb(glb_path)

    classified = []
    for obj, track, strip in iter_nla_strips():
        c = classify_strip(obj, track, strip, fps)
        if c:
            classified.append(c)

    # Bake per-strip material / light / camera tracks. Slow on purpose —
    # quality wins (user's call).
    animations = []
    seen_camera_paths = set()
    for c in classified:
        rng = (c["frameStart"], c["frameEnd"])
        mat_tracks = sample_material_tracks(rng, bake_step, mat_mode)
        light_tracks = sample_light_tracks(rng, bake_step)

        if mat_tracks or light_tracks:
            animations.append({
                "id": "anim.%s.%s" % (slug(c.get("role", "anon")), slug(c["name"])),
                "primitive": "apply_baked_track",
                "target": meta.get("targetElementId") or asset_id,
                "duration": c["duration"],
                "easing": "linear",
                "fps": fps,
                "params": {
                    "frameStart": c["frameStart"],
                    "frameEnd": c["frameEnd"],
                    "materials": mat_tracks,
                    "lights": light_tracks,
                    "stripName": c["name"],
                },
                "_authoring": {
                    "object": c["object"],
                    "track": c["track"],
                    "action": c["actionName"],
                },
            })

        if c.get("role") == "sequence_step":
            seq_id = c["sequenceId"]
            cam_key = (seq_id, c["frameStart"], c["frameEnd"])
            cam_track = sample_camera_path(seq_id, rng, cam_sps, fps)
            if cam_track and cam_key not in seen_camera_paths:
                seen_camera_paths.add(cam_key)
                animations.append({
                    "id": "anim.camera_path.%s.%02d" % (seq_id, c["order"]),
                    "primitive": "camera_path",
                    "target": meta.get("targetElementId") or asset_id,
                    "duration": c["duration"],
                    "easing": "ease-in-out-cubic",
                    "params": {
                        "sequenceId": seq_id,
                        "stepOrder": c["order"],
                        "samples": cam_track["samples"],
                    },
                })

            # Always emit a transform_keyframes entry for the step's
            # underlying glTF clip. Even when there are no material /
            # camera tracks, the GLB carries the baked transforms — the
            # viewer's transform_keyframes handler plays them via the
            # AnimationMixer it wires up on load. trackName is the strip
            # name; the handler also accepts the action name as a
            # fallback (token-overlap match).
            animations.append({
                "id": "anim.transform.%s" % slug(c["name"]),
                "primitive": "transform_keyframes",
                "target": meta.get("targetElementId") or asset_id,
                "duration": c["duration"],
                "easing": "linear",
                "params": {
                    "trackName": c["name"],
                    "actionName": c.get("actionName"),
                    "loop": False,
                },
                "_authoring": {
                    "stripName": c["name"],
                    "frameStart": c["frameStart"],
                    "frameEnd": c["frameEnd"],
                },
            })

        if c.get("role") == "default":
            # Loop the default strip's bake track at runtime. We still need
            # a baked apply_baked_track entry above OR a glTF clip to loop
            # over; the `loop` primitive wraps either.
            animations.append({
                "id": "anim.loop.%s" % slug(c["name"]),
                "primitive": "loop",
                "target": meta.get("targetElementId") or asset_id,
                "duration": c["duration"],
                "easing": "linear",
                "params": {
                    "wraps": "gltf_track",
                    "trackName": c.get("actionName") or c["name"],
                    "fps": fps,
                },
            })

    # ----- sequence_hints -------------------------------------------------
    # Multiple objects can carry a strip with the same name (eg every
    # rig_slot keyframes its own location during an explode beat). We
    # dedupe by (sequenceId, order, label) and union the play[] arrays.
    sequences = {}
    for c in classified:
        if c.get("role") != "sequence_step":
            continue
        sid = c["sequenceId"]
        # Skip the "__master" auxiliary strips — they're for human-author
        # inspection of the master driver prop; the viewer plays the
        # baked slot tracks.
        if c["name"].endswith("__master"):
            continue
        key = (sid, c["order"], c["label"])
        bucket = sequences.setdefault(sid, {}).setdefault(key, {
            "order": c["order"],
            "label": c["label"].replace("_", " "),
            "duration": c["duration"],
            "frameStart": c["frameStart"],
            "frameEnd": c["frameEnd"],
            "strips": [],
        })
        bucket["strips"].append(c["name"])
        # Longest contributing strip wins for duration / frame range.
        if c["duration"] > bucket["duration"]:
            bucket["duration"] = c["duration"]
            bucket["frameStart"] = c["frameStart"]
            bucket["frameEnd"] = c["frameEnd"]

    seq_blocks = []
    for sid, bucket_map in sequences.items():
        steps = sorted(bucket_map.values(), key=lambda x: x["order"])
        out_steps = []
        for s in steps:
            anim_ids = []
            strip_names = set(s["strips"])
            for a in animations:
                if a.get("primitive") == "apply_baked_track" \
                   and a["params"].get("stripName") in strip_names:
                    anim_ids.append(a["id"])
                elif a.get("primitive") == "camera_path" \
                     and a["params"].get("sequenceId") == sid \
                     and a["params"].get("stepOrder") == s["order"]:
                    anim_ids.append(a["id"])
                elif a.get("primitive") == "transform_keyframes" \
                     and a.get("_authoring", {}).get("stripName") in strip_names:
                    anim_ids.append(a["id"])
            out_steps.append({
                "order":  s["order"],
                "label":  s["label"],
                "duration": s["duration"],
                "frameStart": s["frameStart"],
                "frameEnd": s["frameEnd"],
                "play": anim_ids,
                "afterPrevious": True,
            })
        seq_blocks.append({
            "id": "seq.%s" % sid,
            "title": sid.replace("_", " "),
            "fromBlend": True,
            "steps": out_steps,
        })

    # Interaction strips → triggers
    interactions = []
    for c in classified:
        if c.get("role") != "interaction":
            continue
        # We can't classify the trigger type from the strip alone; the
        # planner is responsible for wiring tap/hover. We mark these as
        # play_animation actions referring to the strip's baked track.
        interactions.append({
            "id": c["interactionId"],
            "trigger": {"type": "tap", "target": None},
            "actions": [{
                "type": "play_animation",
                "ref": "anim.interaction.%s" % slug(c["name"]),
            }],
            "_authoring": {
                "object": c["object"], "strip": c["name"],
                "frameStart": c["frameStart"], "frameEnd": c["frameEnd"],
            },
        })

    markers = collect_markers(fps)

    # ----- write artefacts ------------------------------------------------
    with open(anim_path, "w", encoding="utf-8") as fh:
        json.dump({
            "schemaVersion": "0.3.0-spatail",
            "assetId": asset_id,
            "fps": fps,
            "glb": os.path.basename(glb_path) if glb_ok else None,
            "config": cfg,
            "meta": meta,
            "animations": animations,
            "interactions": interactions,
            "markers": markers,
        }, fh, indent=2)

    with open(seq_path, "w", encoding="utf-8") as fh:
        json.dump({
            "assetId": asset_id,
            "fps": fps,
            "defaultSequenceId": meta.get("defaultSequenceId")
                or ("seq.%s" % next(iter(sequences))) if sequences else None,
            "sequences": seq_blocks,
        }, fh, indent=2)

    print("[spatail_animation_export] wrote:")
    print("  %s" % glb_path)
    print("  %s   (%d anim, %d interactions, %d markers)" %
          (anim_path, len(animations), len(interactions), len(markers)))
    print("  %s   (%d sequence(s))" % (seq_path, len(seq_blocks)))


if __name__ == "__main__":
    main()
