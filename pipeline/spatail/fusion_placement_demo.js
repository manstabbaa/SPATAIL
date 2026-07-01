// fusion_placement_demo.js — proves the SPATAIL fusion brain end-to-end,
// on the exact example from the brief:
//
//   "I have a newborn and want foam padding on the edges of my table.
//    I'm looking at the table — put the padding on its edges and corners,
//    and tell me how much I need."
//
// Runs entirely on the PC (no phone, no VLM call): we hand the brain a
// synthetic room scan + a VLM identification + the camera pose, and print the
// contract it produces. Run:  node pipeline/spatail/fusion_placement_demo.js
//
// It also runs the no-table branch (→ spawn a stand-in) and asserts the
// derived geometry so this doubles as a regression check.

import { fuseIdentificationToSurface } from "./surface_fusion.js";
import { planSurfaceExperience } from "./surface_placement.js";

// ── synthetic room scan: a 1.2 m × 0.8 m table + a floor + a back wall ─────
function tableSurface() {
  return {
    id: "surf_table_1",
    kind: "table",
    // world-space boundary, y = 0.75 m (table height), CCW about +Y
    polygon: [
      [-0.6, 0.75, -0.4],
      [0.6, 0.75, -0.4],
      [0.6, 0.75, 0.4],
      [-0.6, 0.75, 0.4],
    ],
    normal: [0, 1, 0],
    centroid: [0, 0.75, 0],
    area: 1.2 * 0.8,
    source: "lidar",
  };
}

const room = {
  surfaces: [
    tableSurface(),
    { id: "surf_floor", kind: "floor", normal: [0, 1, 0],
      polygon: [[-3, 0, -2], [3, 0, -2], [3, 0, 2], [-3, 0, 2]],
      centroid: [0, 0, 0], area: 24, source: "lidar" },
    { id: "surf_wall", kind: "wall", normal: [0, 0, 1],
      polygon: [[-3, 0, -2], [3, 0, -2], [3, 2.4, -2], [-3, 2.4, -2]],
      centroid: [0, 1.2, -2], area: 14.4, source: "lidar" },
  ],
  boundingBox: { min: [-3, 0, -2], max: [3, 2.4, 2] },
};

// ── VLM says the user is looking at a table ────────────────────────────────
const identification = {
  primary: "table",
  detections: [{ label: "table", confidence: 0.95 }],
};

// ── camera pose: standing, looking down at the table centre ────────────────
function look(from, at) {
  const f = [at[0] - from[0], at[1] - from[1], at[2] - from[2]];
  const l = Math.hypot(f[0], f[1], f[2]);
  return { position: from, forward: [f[0] / l, f[1] / l, f[2] / l] };
}
const pose = look([0, 1.5, 1.7], [0, 0.75, 0]);

// ── the concept the user wants placed ──────────────────────────────────────
const foam = {
  id: "foam_padding",
  title: "Foam edge padding",
  prompt: "foam padding for my table edges — I have a newborn",
  reason: "Soft-guard the impact edges + corners of the table for a newborn.",
  unitLengthMeters: 0.5,
  edgeDepthMeters: 0.04,
  cornerSizeMeters: 0.08,
  insetMeters: 0.0,
};

// ── run 1: looking at the real table ───────────────────────────────────────
console.log("══ CASE A — real table in view ═══════════════════════════════");
const a = planSurfaceExperience({
  identification, room, pose, concept: foam,
  fuse: fuseIdentificationToSurface,
});
console.log("mode        :", a.mode);
console.log("fused label :", `"${a.labeled.label}" → ${a.labeled.surfaceId} (${a.labeled.kind})`);
console.log("match reason:", a.labeled.matchReason);
console.log("elements    :", a.contract.spatialElements.length);
for (const el of a.contract.spatialElements) {
  const p = el.placement;
  const where = p.kind === "surface_edge"
    ? `from ${JSON.stringify(p.from)} to ${JSON.stringify(p.to)}  span ${p.spanMeters}m × ${p.count}`
    : `at ${JSON.stringify(p.position)}`;
  console.log(`  • ${p.kind.padEnd(14)} ${el.id.padEnd(24)} ${where}`);
}
console.log("SHOPPING    :", a.summary.shoppingLine);

// ── run 2: no table in the scan → spawn a stand-in ─────────────────────────
console.log("\n══ CASE B — no table detected ════════════════════════════════");
const roomNoTable = { ...room, surfaces: room.surfaces.filter((s) => s.kind !== "table") };
const b = planSurfaceExperience({
  identification, room: roomNoTable, pose, concept: foam,
  fuse: fuseIdentificationToSurface,
});
console.log("mode        :", b.mode);
console.log("elements    :", b.contract.spatialElements.length,
  `(${b.contract.spatialElements[0].placement.kind})`);
console.log("note        :", b.summary.note);

// ── assertions (regression) ────────────────────────────────────────────────
console.log("\n══ ASSERTIONS ════════════════════════════════════════════════");
const edges = a.contract.spatialElements.filter((e) => e.placement.kind === "surface_edge");
const corners = a.contract.spatialElements.filter((e) => e.placement.kind === "surface_corner");
const strips = edges.reduce((n, e) => n + e.placement.count, 0);
const checks = [
  ["fused onto the table", a.labeled.surfaceId === "surf_table_1"],
  ["4 edges", edges.length === 4],
  ["4 corners", corners.length === 4],
  ["perimeter 4.0 m", a.summary.perimeterMeters === 4],
  ["10 foam strips (0.5 m unit)", strips === 10 && a.summary.edgeStripsNeeded === 10],
  ["schemaVersion set", a.contract.schemaVersion === "0.5.0-spatail"],
  ["case B spawned a stand-in", b.mode === "spawned_primitive"],
];
let ok = true;
for (const [name, pass] of checks) {
  console.log(`  ${pass ? "✓" : "✗"} ${name}`);
  if (!pass) ok = false;
}
console.log(ok ? "\nALL PASS ✅" : "\nFAILED ❌");
process.exit(ok ? 0 : 1);
