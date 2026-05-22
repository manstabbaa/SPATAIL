// npm run spatail:segment -- <path/to/asset.obj|.blend|.glb> <assetId>
//
// One-shot CAD → parts analysis. Drives the Blender script headlessly,
// writes per-part renders + parts.json into assets_processed/segmented/<assetId>/.
// The agent labels the parts in a follow-up pass by reading the rendered PNGs.

import { spawn } from "node:child_process";
import path from "node:path";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { findBlender } from "../blender_runner.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SCRIPT = path.join(__dirname, "segment_and_analyze.py");

async function main() {
  const [inputArg, assetId] = process.argv.slice(2);
  if (!inputArg || !assetId) {
    console.error("usage: npm run spatail:segment -- <path/to/asset.obj|.blend|.glb> <assetId>");
    process.exit(2);
  }
  const inputAbs = path.isAbsolute(inputArg) ? inputArg
                  : path.resolve(process.cwd(), inputArg);
  if (!existsSync(inputAbs)) {
    console.error("[segment] no such file:", inputAbs);
    process.exit(2);
  }
  const blender = await findBlender();
  if (!blender) {
    console.error("[segment] Blender not found. Install 4.x or set SPATIAL_BLENDER.");
    process.exit(2);
  }

  console.log(`[segment] blender:  ${blender}`);
  console.log(`[segment] input:    ${inputAbs}`);
  console.log(`[segment] assetId:  ${assetId}`);

  const args = [
    "--background", "--factory-startup",
    "--python", SCRIPT,
    "--", inputAbs, assetId,
  ];
  const child = spawn(blender, args, { stdio: "inherit" });
  child.on("exit", (code) => process.exit(code ?? 0));
  child.on("error", (err) => { console.error("[segment] spawn:", err); process.exit(1); });
}
main().catch((e) => { console.error("[segment] fatal:", e); process.exit(1); });
