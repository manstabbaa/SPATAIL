// `npm run spatail:authoring:build-libraries`
//
// One-shot: materialise the PBR materials library + the rigs library
// the authoring import path appends from. Idempotent — re-running
// overwrites the .blend files with deterministic content.

import { promises as fs } from "node:fs";
import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { findBlender } from "../blender_runner.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = path.resolve(__dirname, "..", "..");
const MATERIALS_OUT = path.join(PROJECT_ROOT, "assets_authoring", "materials",
                                "spatail_pbr_library.blend");
const RIGS_OUT_DIR  = path.join(PROJECT_ROOT, "assets_authoring", "rigs");
const MAT_SCRIPT  = path.join(__dirname, "build_material_library.py");
const RIGS_SCRIPT = path.join(__dirname, "build_rigs_library.py");

function runBlender(blenderPath, script, scriptArgs) {
  return new Promise((resolve, reject) => {
    const args = [
      "--background", "--factory-startup",
      "--python", script, "--", ...scriptArgs,
    ];
    const child = spawn(blenderPath, args, { stdio: "inherit" });
    child.on("exit", (code) => code === 0 ? resolve() : reject(new Error(`blender exited ${code}`)));
    child.on("error", reject);
  });
}

async function main() {
  const blenderPath = await findBlender();
  if (!blenderPath) {
    console.error("[build-libraries] Blender not found. Set SPATIAL_BLENDER or install 4.x.");
    process.exit(2);
  }
  await fs.mkdir(path.dirname(MATERIALS_OUT), { recursive: true });
  await fs.mkdir(RIGS_OUT_DIR, { recursive: true });

  console.log("[build-libraries] materials → " + MATERIALS_OUT);
  await runBlender(blenderPath, MAT_SCRIPT, [MATERIALS_OUT]);

  console.log("[build-libraries] rigs → " + RIGS_OUT_DIR + "/*.blend");
  await runBlender(blenderPath, RIGS_SCRIPT, [RIGS_OUT_DIR]);

  console.log("[build-libraries] done.");
}

main().catch((err) => {
  console.error("[build-libraries] fatal:", err);
  process.exit(1);
});
