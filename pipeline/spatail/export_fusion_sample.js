// export_fusion_sample.js — writes a real fusion-brain contract to disk as a
// sample artifact for the iOS HUD (bundle it) and the web mirror (drop into
// viewer/scene_contracts/). Run: node pipeline/spatail/export_fusion_sample.js
import { writeFileSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { fuseIdentificationToSurface } from "./surface_fusion.js";
import { planSurfaceExperience } from "./surface_placement.js";

const __dirname = dirname(fileURLToPath(import.meta.url));

const room = {
  surfaces: [{
    id: "surf_table_1", kind: "table",
    polygon: [[-0.6, 0.75, -0.4], [0.6, 0.75, -0.4], [0.6, 0.75, 0.4], [-0.6, 0.75, 0.4]],
    normal: [0, 1, 0], centroid: [0, 0.75, 0], area: 0.96, source: "lidar",
  }],
  boundingBox: { min: [-3, 0, -2], max: [3, 2.4, 2] },
};
const pose = { position: [0, 1.5, 1.7], forward: [0, -0.404, -0.915] };
const identification = { primary: "table", detections: [{ label: "table", confidence: 0.95 }] };
const foam = {
  id: "foam_padding", title: "Foam edge padding",
  prompt: "foam padding for my table edges — I have a newborn",
  reason: "Soft-guard the impact edges + corners of the table for a newborn.",
  unitLengthMeters: 0.5, edgeDepthMeters: 0.04, cornerSizeMeters: 0.08, insetMeters: 0.0,
};

const { contract, summary } = planSurfaceExperience({
  identification, room, pose, concept: foam, fuse: fuseIdentificationToSurface,
});

const outDir = resolve(__dirname, "samples");
mkdirSync(outDir, { recursive: true });
const outPath = resolve(outDir, "fusion_foam_table.contract.json");
writeFileSync(outPath, JSON.stringify(contract, null, 2), "utf8");
console.log(`wrote ${outPath}`);
console.log(`  ${contract.spatialElements.length} elements · ${summary.shoppingLine}`);
