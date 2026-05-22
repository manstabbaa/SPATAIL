// `npm run generate`
//
// Loop:
//   1. Scan /assets_raw for supported 3D files
//   2. Group them (one folder = one logical asset group, or loose files
//      stand alone)
//   3. Pick the first non-empty group as the active scene (v0.1 — one
//      scene at a time; multi-scene support is a v0.2 problem)
//   4. Classify the group (domain, primary object, use case)
//   5. For each asset: run Blender headlessly to normalize to .glb +
//      extract metrics. If Blender is missing, pass-through-copy formats
//      the browser can load directly; mark CAD formats as failed.
//   6. Build the SpatialSceneContract and write it to /scene_contracts/

import { promises as fs } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { scanAssetsRaw } from "./scanner.js";
import {
  classifyGroup,
  groupAssets,
  inferRoles,
} from "./classifier.js";
import {
  copyPassThrough,
  findBlender,
  isPassThrough,
  processOne,
} from "./blender_runner.js";
import { buildContract } from "./contract_builder.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = path.resolve(__dirname, "..");
const RAW_DIR = path.join(PROJECT_ROOT, "assets_raw");
const PROCESSED_DIR = path.join(PROJECT_ROOT, "assets_processed");
const CONTRACTS_DIR = path.join(PROJECT_ROOT, "scene_contracts");
const ANALYZE_SCRIPT = path.join(
  PROJECT_ROOT, "blender_tools", "analyze_asset.py",
);

function detectedNameFor(item) {
  const base = path.basename(item.fileName, item.extension);
  return base.replace(/[_\-.]+/g, " ").trim();
}

function assetIdFor(groupKey, item, index) {
  const safeGroup = groupKey.replace(/[^a-zA-Z0-9]+/g, "_").slice(0, 40);
  const safeName = path
    .basename(item.fileName, item.extension)
    .replace(/[^a-zA-Z0-9]+/g, "_")
    .slice(0, 40);
  return `${safeGroup}__${safeName}__${index}`;
}

async function pickActiveGroup(groups) {
  // v0.1: pick the first group with at least one supported file.
  // Multi-scene support arrives when the viewer can pick between scenes.
  for (const g of groups) {
    if (g.items.length > 0) return g;
  }
  return null;
}

async function main() {
  console.log("[generate] scanning", RAW_DIR);
  const { supported, ignored } = await scanAssetsRaw(RAW_DIR);

  if (ignored.length) {
    console.log(
      `[generate] noting ${ignored.length} unsupported file(s) `
      + "(SolidWorks/CATIA/etc.) — they won't appear in the viewer.",
    );
  }
  if (supported.length === 0) {
    console.error(
      "[generate] no supported 3D files found in /assets_raw. "
      + "Drop a .glb, .gltf, .obj, .stl, .fbx, .usd, .step, or .iges file in there.",
    );
    process.exit(1);
  }

  const groups = groupAssets(supported);
  const active = await pickActiveGroup(groups);
  console.log(
    `[generate] found ${supported.length} file(s) in ${groups.length} group(s); `
    + `active group: ${active.groupKey}`,
  );

  const classification = classifyGroup(active);
  const itemsWithRoles = inferRoles(active.items);

  await fs.mkdir(PROCESSED_DIR, { recursive: true });
  await fs.mkdir(CONTRACTS_DIR, { recursive: true });

  const blenderPath = await findBlender();
  if (blenderPath) {
    console.log("[generate] using Blender:", blenderPath);
  } else {
    console.warn(
      "[generate] Blender not found. Set $env:SPATIAL_BLENDER to its path "
      + "to enable STEP/STP/IGES/FBX/USDZ. Falling back to pass-through "
      + "copy for browser-loadable formats.",
    );
  }

  const inventory = [];
  const analyses = [];

  for (let i = 0; i < itemsWithRoles.length; i++) {
    const item = itemsWithRoles[i];
    const id = assetIdFor(active.groupKey, item, i);
    const detectedObjectName = detectedNameFor(item);

    let analysis;
    if (blenderPath) {
      console.log(`[generate]   [${i + 1}/${itemsWithRoles.length}] `
        + `analyze: ${item.relativePath}`);
      analysis = await processOne({
        blenderPath,
        scriptPath: ANALYZE_SCRIPT,
        inputPath: item.absolutePath,
        outputDir: PROCESSED_DIR,
        assetId: id,
      });
    } else if (isPassThrough(item.extension)) {
      console.log(`[generate]   [${i + 1}/${itemsWithRoles.length}] `
        + `copy: ${item.relativePath}`);
      analysis = await copyPassThrough({
        inputPath: item.absolutePath,
        outputDir: PROCESSED_DIR,
        assetId: id,
      });
    } else {
      analysis = {
        assetId: id,
        sourcePath: item.absolutePath,
        status: "failed",
        reason: `format '${item.extension}' requires Blender; install Blender `
              + "or set SPATIAL_BLENDER",
      };
    }
    analyses.push(analysis);

    inventory.push({
      id,
      fileName: item.fileName,
      extension: item.extension,
      relativePath: item.relativePath,
      role: item.role,
      detectedObjectName,
      status: analysis.status === "ok" ? "processed" : analysis.status,
      processedPath: analysis.processedPath
        ? path.relative(PROJECT_ROOT, analysis.processedPath).replace(/\\/g, "/")
        : null,
    });

    if (analysis.status !== "ok") {
      console.warn(
        `[generate]     -> ${analysis.status}: ${analysis.reason || "see analysis.json"}`,
      );
    }
  }

  const contract = buildContract({
    inventory,
    classification,
    analyses,
  });

  const outPath = path.join(CONTRACTS_DIR, "SpatialSceneContract.json");
  await fs.writeFile(outPath, JSON.stringify(contract, null, 2), "utf-8");

  const okCount = analyses.filter((a) => a.status === "ok").length;
  console.log(
    `[generate] wrote ${outPath}  (${okCount}/${analyses.length} assets processed)`,
  );

  if (okCount === 0) {
    console.error(
      "[generate] no assets processed successfully — the viewer will show "
      + "the contract but no model. Check analysis.json files in /assets_processed.",
    );
    process.exit(2);
  }
}

main().catch((err) => {
  console.error("[generate] fatal:", err);
  process.exit(1);
});
