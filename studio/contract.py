"""contract.py - emit the StudioSceneContract: the artist -> developer -> runtime
handoff. Pure Python (no bpy): reads the Blender metadata sidecar + the brief +
xr_design, and writes one JSON the studio viewer (and tomorrow the XR runtime)
consumes. Every placement carries its reasoning, so the contract is also the
Developer's design note.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

STUDIO_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(STUDIO_DIR))
import xr_design as xr  # noqa: E402


def _focal_yup(d: float):
    # centred focal point: Blender (0, d, baseline) -> y-up (0, baseline, -d)
    return [0.0, round(xr.baseline_height(d), 4), round(-d, 4)]


def build_contract(brief: dict, scene_spec: dict, metadata: dict) -> dict:
    eye = metadata["user"]["eye_height_m"]
    stage_d = metadata["staging"]["distance_m"]
    guides = {
        "eye_height_m": eye,
        "cone_deg": xr.OCPA_CONE_DEG,
        "gaze_down_deg": xr.GAZE_DOWN_DEG,
        "near_clip_m": xr.NEAR_CLIP_M,
        "focal_plane_m": xr.FOCAL_PLANE_M,
        "read_band_m": [xr.READ_NEAR_M, xr.READ_FAR_M],
        "far_max_m": xr.FAR_MAX_M,
        "focal_point_yup": _focal_yup(xr.FOCAL_PLANE_M),
        "stage_distance_m": stage_d,
        "baseline_z_m": round(xr.baseline_height(stage_d), 4),
    }

    beats = [{
        "id": b["id"], "law": b["law"], "subtitle": b["subtitle"],
        "title": b["title"], "narration": b["narration"],
        "focusTarget": b["anchor_yup_m"], "labelAnchor": b["label_anchor_yup_m"],
        "distanceM": b["distance_m"], "inComfortCone": b["in_comfort_cone"],
    } for b in metadata["beats"]]

    interactions = [
        {"id": "play_pause", "type": "play_pause", "label": "Play / Pause",
         "behavior": "toggle the demo animation", "trigger": "ui"},
        {"id": "prev_beat", "type": "prev_beat", "label": "Prev law",
         "behavior": "focus the previous law", "trigger": "ui"},
        {"id": "next_beat", "type": "next_beat", "label": "Next law",
         "behavior": "focus the next law", "trigger": "ui"},
        {"id": "toggle_guides", "type": "toggle_guides", "label": "Comfort guides",
         "behavior": "show/hide the XR comfort overlay", "trigger": "ui"},
        {"id": "reset_view", "type": "reset_view", "label": "Reset view",
         "behavior": "return to the user's eye viewpoint", "trigger": "ui"},
    ]

    return {
        "schemaVersion": "0.3.0-studio",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "sceneId": metadata["sceneId"],
        "title": metadata["title"],
        "sourceBrief": brief,
        "domain": {"name": brief.get("subject", "general"),
                   "confidence": "high", "source": "brief"},
        "studio": {
            "glb": metadata["glb"], "frame": metadata["frame"],
            "room": metadata["room"], "user": metadata["user"],
            "animation": metadata["animation"], "bbox": metadata["bbox_yup_m"],
        },
        "comfortGuides": guides,
        "staging": metadata["staging"],
        "beats": beats,
        "storySequence": [{
            "id": b["id"], "title": f'{b["law"]} - {b["subtitle"]}',
            "description": b["title"], "suggestedAction": "next_beat",
        } for b in metadata["beats"]],
        "interactions": interactions,
        "assets": [{
            "id": "studio", "fileName": "studio.glb", "role": "primary_object",
            "status": "processed", "processedPath": metadata["glb"],
            "detectedObjectName": metadata["title"],
        }],
        "xrDesignCitations": xr.CITATIONS,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brief", required=True)
    ap.add_argument("--scene", required=True)
    ap.add_argument("--metadata", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    brief = json.loads(Path(a.brief).read_text(encoding="utf-8"))
    scene = json.loads(Path(a.scene).read_text(encoding="utf-8"))
    meta = json.loads(Path(a.metadata).read_text(encoding="utf-8"))
    Path(a.out).write_text(json.dumps(build_contract(brief, scene, meta), indent=2),
                           encoding="utf-8")
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
