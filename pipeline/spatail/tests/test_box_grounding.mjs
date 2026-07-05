#!/usr/bin/env node
// test_box_grounding.mjs — the brain CONSUMES the VLM's boxes (LIVE_BRAIN_SPEC §1.6).
//
// Covers the three consumption rungs plus the camera view model they share:
//   (0) camera view model — device-fixed portrait mapping + projection round-trip
//   (1) box-refined gaze  — the ray runs through the detection box center, so a
//                           surface the forward ray MISSES still binds by ray
//   (2) box-grounded object binding — of two same-noun objects, the one whose
//                           projected footprint overlaps the box wins, even
//                           against a higher device label confidence
//   (3) part anchor bias  — parts[].box turns a {objectId, part} target into a
//                           world-space anchor on the bound object
// plus end-to-end plan_from_room.js --stdin: fused.decisionTrace + target.anchor.
//
// Run: node pipeline/spatail/tests/test_box_grounding.mjs   (exit 0 = pass)

import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import {
  fuseIdentificationToSurface,
  matchNounToObject,
  cameraViewFromPose,
  rayThroughImagePoint,
  projectPointToImage,
  projectObbToImageRect,
  boxIoU,
  rayObbIntersect,
  _geom,
} from "../surface_fusion.js";

const { sub, normalize, dot } = _geom;

let failures = 0;
function check(name, cond, detail) {
  if (cond) {
    console.log(`  ok  ${name}`);
  } else {
    failures += 1;
    console.error(`FAIL  ${name}${detail ? ` — ${detail}` : ""}`);
  }
}

const FRAME = [720, 960]; // portrait 3:4 — the FrameStreamer contract

// ── (0) camera view model ───────────────────────────────────────────────────
console.log("case (0): camera view model");
{
  // upright portrait hold looking north: forward -Z; the wire's up (camera +Y,
  // device-fixed) physically points screen-right = east = +X.
  const pose = { position: [0, 1.5, 2], forward: [0, 0, -1], up: [1, 0, 0] };
  const view = cameraViewFromPose(pose, FRAME);
  check("view exists", Boolean(view));
  check("image right = pose.up (device-fixed mapping)",
    Math.abs(view.right[0] - 1) < 1e-9);
  check("image down = forward × up = world -Y",
    Math.abs(view.down[1] + 1) < 1e-9, JSON.stringify(view.down));

  // a point east of the gaze line lands right of image center; below → lower
  const east = projectPointToImage(view, [0.5, 1.5, 0]);
  const below = projectPointToImage(view, [0, 1.0, 0]);
  check("east point projects right of center", east && east[0] > 0.5, JSON.stringify(east));
  check("east point stays on the horizon line", east && Math.abs(east[1] - 0.5) < 1e-9);
  check("lower point projects below center", below && below[1] > 0.5, JSON.stringify(below));
  check("behind-camera point projects to null",
    projectPointToImage(view, [0, 1.5, 5]) === null);

  // round-trip: project a point, cast back through the pixel → same direction
  const p = [0.3, 1.2, -1];
  const uv = projectPointToImage(view, p);
  const dir = rayThroughImagePoint(view, uv[0], uv[1]);
  const want = normalize(sub(p, pose.position));
  check("project → ray round-trip returns the same direction",
    dot(dir, want) > 1 - 1e-9, JSON.stringify({ dir, want }));

  // no-up pose falls back to an upright-hold basis and still round-trips
  const viewNoUp = cameraViewFromPose({ position: pose.position, forward: pose.forward }, FRAME);
  check("no-up pose still builds a view (upright-hold fallback)",
    Boolean(viewNoUp) && Math.abs(viewNoUp.right[0] - 1) < 1e-9);
}

// ── (2) box-grounded object binding: seen beats confident-but-unseen ────────
console.log("case (2): box-grounded object binding");
const POSE_TWO = { position: [0, 1.5, 2], forward: [0, 0, -1], up: [1, 0, 0] };
const BOTTLE_L = {
  id: "obj-bottle-L", label: "water bottle", confidence: 0.95,
  obb: { center: [-0.5, 0.95, 0], extents: [0.08, 0.22, 0.08], yaw: 0 },
  supportSurfaceId: null,
};
const BOTTLE_R = {
  id: "obj-bottle-R", label: "water bottle", confidence: 0.6,
  obb: { center: [0.5, 0.95, 0], extents: [0.08, 0.22, 0.08], yaw: 0 },
  supportSurfaceId: null,
};
{
  const view = cameraViewFromPose(POSE_TWO, FRAME);
  const boxR = projectObbToImageRect(view, BOTTLE_R.obb);
  check("right bottle projects to a rect", Boolean(boxR), JSON.stringify(boxR));
  check("rect self-IoU is 1", Math.abs(boxIoU(boxR, boxR) - 1) < 1e-9);
  check("disjoint rects IoU 0",
    boxIoU(boxR, projectObbToImageRect(view, BOTTLE_L.obb)) === 0);

  check("no box → higher-confidence bottle wins (legacy)",
    matchNounToObject("water bottle", [BOTTLE_L, BOTTLE_R])?.id === "obj-bottle-L");
  check("box over the right bottle → the SEEN bottle wins",
    matchNounToObject("water bottle", [BOTTLE_L, BOTTLE_R],
      { view, box: boxR })?.id === "obj-bottle-R");

  const fused = fuseIdentificationToSurface({
    identification: {
      primary: "water bottle",
      detections: [{ label: "water bottle", confidence: 0.9, box: boxR }],
      frameSize: FRAME,
    },
    room: { surfaces: [], objects: [BOTTLE_L, BOTTLE_R] },
    pose: POSE_TWO,
  });
  check("fusion binds the boxed bottle", fused?.objectId === "obj-bottle-R",
    JSON.stringify(fused?.objectId));
  check("decisionTrace records the IoU decision",
    (fused?.decisionTrace || []).some((l) => /box: object obj-bottle-R projected footprint IoU/.test(l)),
    JSON.stringify(fused?.decisionTrace));
  check("object binding gains a box-ray hitPoint",
    Array.isArray(fused?.hitPoint) &&
      Math.abs(fused.hitPoint[0] - 0.5) < 0.1 && Math.abs(fused.hitPoint[2]) < 0.15,
    JSON.stringify(fused?.hitPoint));
  check("matchReason string unchanged (rule is still the noun match)",
    fused?.matchReason === "noun matched object water bottle (IoU-fused on device)");
}

// ── (1) box-refined gaze: forward ray misses the table, the box finds it ───
console.log("case (1): box-refined gaze on surfaces");
const TABLE = {
  id: "table_1", kind: "table",
  polygon: [[-0.8, 0.74, -0.4], [0, 0.74, -0.4], [0, 0.74, 0.4], [-0.8, 0.74, 0.4]],
  normal: [0, 1, 0], area: 0.64,
};
const FLOOR = {
  id: "floor_1", kind: "floor",
  polygon: [[-3, 0, -3], [3, 0, -3], [3, 0, 3], [-3, 0, 3]],
  normal: [0, 1, 0], area: 36,
};
// camera looks at the floor BEYOND the table (forward ray exits the table
// polygon at z ≈ 0.79 > 0.4) — the near-but-not-on setup.
const POSE_MISS = {
  position: [0, 1.4, 1.5],
  forward: [0, -0.6822, -0.7311],
  up: [1, 0, 0],
};
{
  const noBox = fuseIdentificationToSurface({
    identification: { primary: "table", detections: [{ label: "table", confidence: 0.9 }] },
    room: { surfaces: [TABLE, FLOOR] },
    pose: POSE_MISS,
  });
  check("without a box the forward ray misses → label fallback",
    /no ray hit; bound VLM label/.test(noBox?.matchReason || ""), noBox?.matchReason);

  const withBox = fuseIdentificationToSurface({
    identification: {
      primary: "table",
      detections: [{ label: "table", confidence: 0.9, box: [0.14, 0.14, 0.2, 0.2] }],
      frameSize: FRAME,
    },
    room: { surfaces: [TABLE, FLOOR] },
    pose: POSE_MISS,
  });
  check("with the box the refined ray PIERCES the table",
    /box-refined ray pierced table_1/.test(withBox?.matchReason || ""),
    withBox?.matchReason);
  check("hitPoint lands inside the table polygon",
    Array.isArray(withBox?.hitPoint) &&
      withBox.hitPoint[0] > -0.8 && withBox.hitPoint[0] < 0 &&
      Math.abs(withBox.hitPoint[1] - 0.74) < 1e-6 &&
      Math.abs(withBox.hitPoint[2]) < 0.4,
    JSON.stringify(withBox?.hitPoint));
  check("ray-hit confidence is NOT discounted", withBox?.confidence === 0.9);
  check("decisionTrace records the gaze refinement",
    (withBox?.decisionTrace || []).some((l) => /box: gaze refined through box center/.test(l)),
    JSON.stringify(withBox?.decisionTrace));
}

// ── ray → OBB primitive ─────────────────────────────────────────────────────
{
  const obb = { center: [0, 1, 0], extents: [0.2, 0.4, 0.2], yaw: 0.3 };
  const hit = rayObbIntersect([0, 1, 2], [0, 0, -1], obb);
  check("axis ray hits a yawed OBB", Boolean(hit) && hit.t > 1.7 && hit.t < 2,
    JSON.stringify(hit));
  check("offset ray misses it", rayObbIntersect([2, 1, 2], [0, 0, -1], obb) === null);
  check("origin inside → t 0 at origin",
    rayObbIntersect([0, 1, 0], [0, 0, -1], obb)?.t === 0);
}

// ── (3) + e2e: part anchor via plan_from_room.js --stdin ───────────────────
console.log("case (3): part anchor + end-to-end --stdin");
const PLAN_CLI = fileURLToPath(new URL("../plan_from_room.js", import.meta.url));
function runPlan(blob) {
  const out = execFileSync(process.execPath, [PLAN_CLI, "--stdin"], {
    input: JSON.stringify(blob),
    encoding: "utf8",
  });
  return JSON.parse(out);
}
{
  const BOTTLE = {
    id: "obj-bottle-1", label: "water bottle", confidence: 0.91,
    obb: { center: [0.2, 0.85, 0.0], extents: [0.08, 0.22, 0.08], yaw: 0.1 },
    supportSurfaceId: "table_1", lastSeenAt: 1783036800.123,
  };
  const TABLE_S = {
    id: "table_1", kind: "table",
    polygon: [[-0.6, 0.74, -0.4], [0.6, 0.74, -0.4], [0.6, 0.74, 0.4], [-0.6, 0.74, 0.4]],
    normal: [0, 1, 0], area: 0.96,
  };
  // device-fixed pose looking at the bottle; up ⊥ forward, screen-right-ish
  const f = normalize([0.2, -0.55, -1.5]);
  const pose = { position: [0, 1.4, 1.5], forward: f, up: normalize([-f[2], 0, f[0]]) };
  const view = cameraViewFromPose(pose, FRAME);
  const primaryBox = projectObbToImageRect(view, BOTTLE.obb);
  check("bottle projects to a primary box", Boolean(primaryBox), JSON.stringify(primaryBox));
  // the cap: a slice across the top of the primary box
  const capBox = [
    primaryBox[0] + primaryBox[2] * 0.3, primaryBox[1],
    primaryBox[2] * 0.4, primaryBox[3] * 0.15,
  ];

  const plan = runPlan({
    room: { surfaces: [TABLE_S], objects: [BOTTLE] },
    pose,
    identification: {
      primary: "blue water bottle",
      detections: [{ label: "blue water bottle", confidence: 0.88, box: primaryBox }],
      parts: [{ label: "cap", box: capBox, confidence: 0.8 }],
      frameSize: FRAME,
    },
    concept: {
      id: "bottle_cap_marker", title: "Cap highlight",
      prompt: "highlight the cap of my water bottle",
      reason: "Show which cap to loosen.",
    },
    camera: { fovLongEdgeDegrees: 67 },
  });
  check("plan still binds the object", plan.fused?.objectId === "obj-bottle-1");
  check("plan still targets the part",
    plan.target?.objectId === "obj-bottle-1" && plan.target?.part === "cap");
  check("target carries a box-grounded anchor",
    Array.isArray(plan.target?.anchor?.point) &&
      /^part_box_(ray|relative)$/.test(plan.target?.anchor?.method || ""),
    JSON.stringify(plan.target?.anchor));
  // the cap anchor must land in the cap zone (upper quarter of the bottle,
  // OBB top 0.96), not at the object's center (0.85) — the whole point.
  check("anchor is in the cap zone, not the object center",
    plan.target?.anchor?.point?.[1] > 0.9 && plan.target?.anchor?.point?.[1] <= 0.97,
    JSON.stringify(plan.target?.anchor?.point));
  check("anchor echoes the part box", JSON.stringify(plan.target?.anchor?.partBox) ===
    JSON.stringify(capBox.map((v) => v)), JSON.stringify(plan.target?.anchor?.partBox));
  check("fused.decisionTrace shows the box influenced the plan",
    (plan.fused?.decisionTrace || []).some((l) => l.startsWith("box:")),
    JSON.stringify(plan.fused?.decisionTrace));
  check("fused carries the box-ray hitPoint", Array.isArray(plan.fused?.hitPoint));

  // boxless replay of the same ask stays legacy-shaped: no anchor, no trace
  const legacy = runPlan({
    room: { surfaces: [TABLE_S], objects: [BOTTLE] },
    pose: { position: pose.position, forward: pose.forward },
    identification: {
      primary: "blue water bottle",
      detections: [{ label: "blue water bottle", confidence: 0.88 }],
      parts: [{ label: "cap", confidence: 0.8 }],
    },
    concept: { id: "bottle_cap_marker", title: "Cap highlight",
               prompt: "highlight the cap of my water bottle" },
  });
  check("boxless plan keeps the bare {objectId, part} target",
    legacy.target?.part === "cap" && legacy.target?.anchor === undefined,
    JSON.stringify(legacy.target));
  check("boxless plan has no decisionTrace",
    legacy.fused?.decisionTrace === undefined);

  // the LIVE wire shape: concept is the user's ask STRING (spec §1.2) — the
  // part target and its anchor must fire exactly as with an object concept
  const wire = runPlan({
    room: { surfaces: [TABLE_S], objects: [BOTTLE] },
    pose,
    identification: {
      primary: "blue water bottle",
      detections: [{ label: "blue water bottle", confidence: 0.88, box: primaryBox }],
      parts: [{ label: "cap", box: capBox, confidence: 0.8 }],
      frameSize: FRAME,
    },
    concept: "highlight the cap of my water bottle",
    camera: { fovLongEdgeDegrees: 67 },
  });
  check("string concept (live wire) still targets the part",
    wire.target?.part === "cap", JSON.stringify(wire.target));
  check("string concept still gets the box-grounded anchor",
    wire.target?.anchor?.point?.[1] > 0.9, JSON.stringify(wire.target?.anchor));
}

if (failures > 0) {
  console.error(`\n${failures} assertion(s) failed`);
  process.exit(1);
}
console.log("\nall box-grounding tests passed");
