#!/usr/bin/env node
// test_object_binding.mjs — objects-first binding (LIVE_BRAIN_SPEC §1.2).
//
// Runs fuse/decide over a synthetic room WITH tracked objects:
//   (a) noun matches an object  → identity binds to the OBJECT
//   (b) noun matches no object  → the legacy surface binding still fires
// plus end-to-end plan_from_room.js --stdin runs covering the objects
// pass-through, the backward-compatible fused shape, and part targeting.
//
// Run: node pipeline/spatail/tests/test_object_binding.mjs   (exit 0 = pass)

import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import {
  fuseIdentificationToSurface,
  matchNounToObject,
} from "../surface_fusion.js";
import { planSurfaceExperience } from "../surface_placement.js";

let failures = 0;
function check(name, cond, detail) {
  if (cond) {
    console.log(`  ok  ${name}`);
  } else {
    failures += 1;
    console.error(`FAIL  ${name}${detail ? ` — ${detail}` : ""}`);
  }
}

// ── synthetic room: a table + floor, with one tracked object on the table ──
const TABLE = {
  id: "table_1",
  kind: "table",
  polygon: [
    [-0.6, 0.74, -0.4], [0.6, 0.74, -0.4], [0.6, 0.74, 0.4], [-0.6, 0.74, 0.4],
  ],
  normal: [0, 1, 0],
  area: 0.96,
};
const FLOOR = {
  id: "floor_1",
  kind: "floor",
  polygon: [[-3, 0, -3], [3, 0, -3], [3, 0, 3], [-3, 0, 3]],
  normal: [0, 1, 0],
  area: 36,
};
const BOTTLE = {
  id: "obj-bottle-1",
  label: "water bottle",
  confidence: 0.91,
  obb: { center: [0.2, 0.85, 0.0], extents: [0.08, 0.22, 0.08], yaw: 0.1 },
  supportSurfaceId: "table_1",
  lastSeenAt: 1783036800.123,
};
const roomWithObjects = { surfaces: [TABLE, FLOOR], objects: [BOTTLE] };
const POSE = { position: [0, 1.4, 1.5], forward: [0, -0.403, -0.915] };
const CONCEPT = {
  id: "bottle_cap_marker",
  title: "Cap highlight",
  prompt: "highlight the cap of my water bottle",
  reason: "Show which cap to loosen.",
};

// ── (a) noun fuzzy-matches the object → OBJECT binding ─────────────────────
console.log("case (a): noun matches an object");
{
  const fused = fuseIdentificationToSurface({
    identification: {
      primary: "blue water bottle",
      detections: [{ label: "blue water bottle", confidence: 0.88 }],
    },
    room: roomWithObjects,
    pose: POSE,
  });
  check("binds to the object", fused?.objectId === "obj-bottle-1",
    JSON.stringify(fused));
  check("kind is 'object'", fused?.kind === "object");
  check("carries supportSurfaceId", fused?.supportSurfaceId === "table_1");
  check("surfaceId = supportSurfaceId (back-compat)",
    fused?.surfaceId === "table_1");
  check("label is the object's label", fused?.label === "water bottle");
  check("matchReason names the rule",
    fused?.matchReason === "noun matched object water bottle (IoU-fused on device)",
    fused?.matchReason);
  check("confidence is the device's label confidence",
    fused?.confidence === 0.91);
  check("geometry resolved from the support surface",
    fused?.geometry?.edges?.length === 4 && fused?.surface?.id === "table_1");

  // decide still runs the as-is content pipeline over the binding
  const { contract, summary, mode } = planSurfaceExperience({
    identification: { primary: "blue water bottle" },
    room: roomWithObjects,
    pose: POSE,
    concept: CONCEPT,
    fuse: fuseIdentificationToSurface,
  });
  check("decide mode is anchored_to_surface", mode === "anchored_to_surface");
  check("contract has spatial elements",
    (contract?.spatialElements?.length ?? 0) > 0);
  check("summary keeps kind 'object'", summary?.kind === "object");
}

// containment the other way + adjective stripping on the object side
check("bare noun matches longer object label",
  matchNounToObject("bottle", [BOTTLE])?.id === "obj-bottle-1");
check("adjectives stripped from the object label too",
  matchNounToObject("water bottle",
    [{ ...BOTTLE, label: "plastic water bottle" }])?.id === "obj-bottle-1");
check("unlabeled objects never match",
  matchNounToObject("water bottle", [{ ...BOTTLE, label: null }]) === null);

// object with NO support surface → OBB top-face geometry, surfaceId null
{
  const fused = fuseIdentificationToSurface({
    identification: { primary: "water bottle" },
    room: { surfaces: [FLOOR], objects: [{ ...BOTTLE, supportSurfaceId: null }] },
    pose: POSE,
  });
  check("no-support binding keeps surfaceId null", fused?.surfaceId === null);
  check("no-support binding synthesizes OBB-top geometry",
    fused?.geometry?.edges?.length === 4 &&
      Math.abs(fused.geometry.centroid[1] - 0.96) < 1e-6,
    JSON.stringify(fused?.geometry?.centroid));
}

// ── (b) noun matches no object → legacy surface binding unchanged ──────────
console.log("case (b): noun matches no object");
{
  const fused = fuseIdentificationToSurface({
    identification: {
      primary: "table",
      detections: [{ label: "table", confidence: 0.9 }],
    },
    room: roomWithObjects,
    pose: POSE,
  });
  check("binds to the table surface", fused?.surfaceId === "table_1",
    JSON.stringify(fused));
  check("kind is the surface kind", fused?.kind === "table");
  check("no objectId on a surface binding", fused?.objectId === undefined);
  check("legacy matchReason (ray hit) preserved",
    /camera ray pierced table_1/.test(fused?.matchReason || ""),
    fused?.matchReason);
}

// ── end-to-end: plan_from_room.js --stdin with room.objects ────────────────
console.log("end-to-end: plan_from_room.js --stdin");
const PLAN_CLI = fileURLToPath(new URL("../plan_from_room.js", import.meta.url));
function runPlan(blob) {
  const out = execFileSync(process.execPath, [PLAN_CLI, "--stdin"], {
    input: JSON.stringify(blob),
    encoding: "utf8",
  });
  return JSON.parse(out);
}
{
  const plan = runPlan({
    room: roomWithObjects,
    pose: POSE,
    identification: {
      primary: "blue water bottle",
      detections: [{ label: "blue water bottle", confidence: 0.88 }],
      parts: [{ label: "cap", box: [0.4, 0.1, 0.2, 0.1], confidence: 0.8 }],
    },
    concept: CONCEPT,
  });
  check("plan fused.objectId present", plan.fused?.objectId === "obj-bottle-1");
  check("plan fused keeps surfaceId/kind/matchReason",
    plan.fused?.surfaceId === "table_1" && plan.fused?.kind === "object" &&
      typeof plan.fused?.matchReason === "string");
  check("plan targets the part",
    plan.target?.objectId === "obj-bottle-1" && plan.target?.part === "cap",
    JSON.stringify(plan.target));
  check("plan carries a contract", Boolean(plan.contract));
}
{
  const plan = runPlan({
    room: roomWithObjects,
    pose: POSE,
    identification: {
      primary: "table",
      detections: [{ label: "table", confidence: 0.9 }],
      parts: [{ label: "cap", box: [0.4, 0.1, 0.2, 0.1], confidence: 0.8 }],
    },
  });
  check("surface plan has no objectId", plan.fused?.objectId === undefined);
  check("surface plan has no target", plan.target === undefined);
  check("surface plan fused is legacy-shaped",
    plan.fused?.surfaceId === "table_1" && plan.fused?.kind === "table");
}

if (failures > 0) {
  console.error(`\n${failures} assertion(s) failed`);
  process.exit(1);
}
console.log("\nall object-binding tests passed");
