// `npm run spatail:animations -- <some.blend> [assetId]`
//
// Headless Blender wrapper around pipeline/blender/spatail_animation_export.py.
// Reads spatail.config.json and threads its animation block through to the
// exporter so the author's quality knobs survive the bash boundary.

import { promises as fs } from "node:fs";
import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { findBlender } from "../blender_runner.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = path.resolve(__dirname, "..", "..");
const SCRIPT_PATH = path.join(__dirname, "spatail_animation_export.py");
const OUTPUT_DIR = path.join(PROJECT_ROOT, "assets_processed", "animations");
const CONFIG_PATH = path.join(PROJECT_ROOT, "spatail.config.json");

async function readConfig() {
  try {
    return JSON.parse(await fs.readFile(CONFIG_PATH, "utf-8"));
  } catch {
    return {};
  }
}

async function main() {
  const [blendFile, assetIdArg] = process.argv.slice(2);
  if (!blendFile) {
    console.error("usage: npm run spatail:animations -- <path/to/file.blend> [assetId]");
    process.exit(2);
  }
  const blendAbs = path.isAbsolute(blendFile)
    ? blendFile
    : path.resolve(process.cwd(), blendFile);
  const assetId = assetIdArg
    || path.basename(blendAbs, path.extname(blendAbs)).replace(/[^a-zA-Z0-9_-]+/g, "_");

  const blenderPath = await findBlender();
  if (!blenderPath) {
    console.error("[spatail:animations] Blender not found. Install 4.x or set SPATIAL_BLENDER.");
    process.exit(2);
  }

  const config = await readConfig();
  console.log(`[spatail:animations] Blender:    ${blenderPath}`);
  console.log(`[spatail:animations] input:      ${blendAbs}`);
  console.log(`[spatail:animations] output dir: ${OUTPUT_DIR}/${assetId}/`);
  console.log(`[spatail:animations] config:     ${JSON.stringify(config.animation || {})}`);

  const args = [
    "--background", blendAbs,
    "--factory-startup",
    "--python", SCRIPT_PATH,
    "--", OUTPUT_DIR, assetId, JSON.stringify(config),
  ];
  const child = spawn(blenderPath, args, { stdio: "inherit" });
  child.on("exit", (code) => process.exit(code ?? 0));
  child.on("error", (err) => {
    console.error("[spatail:animations] spawn failed:", err);
    process.exit(1);
  });
}

main().catch((err) => {
  console.error("[spatail:animations] fatal:", err);
  process.exit(1);
});
