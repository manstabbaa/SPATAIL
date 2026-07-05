#!/usr/bin/env python
"""test_box_binding_gate.py — the replan gate's box-grounded binding mirror.

LIVE_BRAIN_SPEC §1.4/§1.6: _binding_for() must score object candidates the
same way the node brain's matchNounToObject()/nounObjectScores() does —
containment after adjective stripping, exact > containment, confidence
tie-break, PLUS the projected-footprint IoU bonus — so the id the gate binds
is the id the plan binds. This runs the same two-bottle fixture as
pipeline/spatail/tests/test_box_grounding.mjs case (2) and expects the same
winners.

Run: python pipeline/server/tests/test_box_binding_gate.py   (exit 0 = pass)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from spatail_vision_engine import (  # noqa: E402
    VLMAdapter,
    VisionEngine,
    _box_iou,
    _camera_view,
    _project_obb_rect,
)

failures = 0


def check(name: str, cond: bool, detail: str = ""):
    global failures
    if cond:
        print(f"  ok  {name}")
    else:
        failures += 1
        print(f"FAIL  {name}{' — ' + detail if detail else ''}")


FRAME = [720, 960]  # portrait 3:4 — the FrameStreamer contract
POSE = {"position": [0, 1.5, 2], "forward": [0, 0, -1], "up": [1, 0, 0]}
BOTTLE_L = {
    "id": "obj-bottle-L", "label": "water bottle", "confidence": 0.95,
    "obb": {"center": [-0.5, 0.95, 0], "extents": [0.08, 0.22, 0.08], "yaw": 0},
}
BOTTLE_R = {
    "id": "obj-bottle-R", "label": "water bottle", "confidence": 0.6,
    "obb": {"center": [0.5, 0.95, 0], "extents": [0.08, 0.22, 0.08], "yaw": 0},
}

engine = VisionEngine(VLMAdapter("http://localhost:0", "test-model"))
engine._pose = POSE
engine._objects = [BOTTLE_L, BOTTLE_R]

print("camera view model (python mirror)")
view = _camera_view(POSE, FRAME, engine.camera_fov_long)
check("view exists", view is not None)
check("image right = pose.up", abs(view["right"][0] - 1) < 1e-9)
check("image down = world -Y", abs(view["down"][1] + 1) < 1e-9)

box_r = _project_obb_rect(view, BOTTLE_R["obb"])
check("right bottle projects to a rect", box_r is not None, repr(box_r))
check("rect self-IoU is 1", abs(_box_iou(box_r, box_r) - 1) < 1e-9)
box_l = _project_obb_rect(view, BOTTLE_L["obb"])
check("disjoint rects IoU 0", _box_iou(box_r, box_l) == 0.0)
check("behind-camera OBB projects to None",
      _project_obb_rect(view, {"center": [0, 1.5, 5],
                               "extents": [0.1, 0.1, 0.1], "yaw": 0}) is None)

print("binding gate (lockstep with matchNounToObject)")
check("no box -> higher-confidence bottle wins (legacy)",
      engine._binding_for("water bottle") == ("object", "obj-bottle-L"))
check("box over the right bottle -> the SEEN bottle wins",
      engine._binding_for("water bottle", box=box_r, frame_size=FRAME)
      == ("object", "obj-bottle-R"))
check("adjectives still stripped before matching",
      engine._binding_for("blue plastic water bottle", box=box_r, frame_size=FRAME)
      == ("object", "obj-bottle-R"))
check("surface fallthrough intact (box present, no object match)",
      engine._binding_for("dining table", box=box_r, frame_size=FRAME)
      == ("surface", "table"))
check("no pose -> box is inert, legacy score decides", (lambda: (
      setattr(engine, "_pose", None),
      engine._binding_for("water bottle", box=box_r, frame_size=FRAME),
      setattr(engine, "_pose", POSE))[1])() == ("object", "obj-bottle-L"))
check("malformed box is inert",
      engine._binding_for("water bottle", box=[1, 2], frame_size=FRAME)
      == ("object", "obj-bottle-L"))

if failures:
    print(f"\n{failures} assertion(s) failed")
    sys.exit(1)
print("\nall box-binding gate tests passed")
