// One-shot recovery: the wheel segmentation run rendered 1521 PNGs but
// was killed before writing parts.json. This script scans the on-disk
// renders and emits a parts.json skeleton the agent can label.
//
// Metrics (bbox / centroid / face count) need a follow-up Blender pass —
// noted as `knownLimitation` in the JSON so we don't pretend they exist.

import fs from "node:fs";
import path from "node:path";

const dir = "C:/SPATAIL_MAX/assets_processed/segmented/mercedes_wheel/parts";
const files = fs.readdirSync(dir);
const parts = new Map();

for (const f of files) {
  const m = f.match(/^(.+)\.(front|iso|top)\.png$/);
  if (!m) continue;
  const [_, id, view] = m;
  if (!parts.has(id)) parts.set(id, { id, originalName: id, renders: {} });
  parts.get(id).renders[view] = path.join(dir, f).replace(/\\/g, "/");
}

const records = [...parts.values()].sort((a, b) =>
  a.id.localeCompare(b.id, undefined, { numeric: true }));
for (const r of records) {
  r.semantic = {
    name: null, role: null, function: null,
    connectedTo: [], motionDOF: null, agentNotes: null,
  };
}

const analysis = {
  assetId: "mercedes_wheel",
  source: "assets_raw/car-engine/Steering Wheel.obj",
  segmentationStrategy: "loose_separate",
  schemaVersion: "0.1.0-parts-analysis",
  partCount: records.length,
  parts: records,
  agentLoop:
    "Each part has 3 renders. Visual inspection fills the semantic block. " +
    "507 islands is too granular for hand-labelling at this scale — next " +
    "pass clusters by spatial proximity + size before labelling.",
  knownLimitation:
    "Run was killed mid-pipeline at the metrics step. Renders survived " +
    "(1521 PNGs); bbox / centroid / face counts need a follow-up Blender pass.",
};

const out = "C:/SPATAIL_MAX/assets_processed/segmented/mercedes_wheel/mercedes_wheel.parts.json";
fs.writeFileSync(out, JSON.stringify(analysis, null, 2));
console.log("wrote", out, "—", records.length, "parts");
console.log("first 5:", records.slice(0, 5).map((r) => r.id));
console.log("last 5: ", records.slice(-5).map((r) => r.id));
