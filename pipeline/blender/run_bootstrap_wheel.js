// `npm run spatail:authoring:bootstrap`
//
// One-shot: programmatically materialize assets_authoring/wheel.blend so
// the Blender-first animation pipeline has something to export today.
// Once a human takes over the authoring pass, this script is purely a
// safety net — re-running it overwrites the procedural curves, so don't
// run it after you've started editing by hand.

import { spawn } from "node:child_process";
import { promises as fs, existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { findBlender } from "../blender_runner.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = path.resolve(__dirname, "..", "..");
const SCRIPT_PATH = path.join(__dirname, "bootstrap_wheel_blend.py");
const OUT_BLEND = path.join(PROJECT_ROOT, "assets_authoring", "wheel.blend");
// Prefer the pre-segmented GLB; fall back to the raw OBJ.
const PREFERRED_SOURCE = path.join(
  PROJECT_ROOT, "assets_processed", "segmented", "mercedes_wheel.glb",
);
const FALLBACK_SOURCE = path.join(
  PROJECT_ROOT, "assets_raw", "car-engine", "Steering Wheel.obj",
);

async function main() {
  const blenderPath = await findBlender();
  if (!blenderPath) {
    console.error("[bootstrap] Blender not found. Install 4.x or set SPATIAL_BLENDER.");
    process.exit(2);
  }
  const source = existsSync(PREFERRED_SOURCE) ? PREFERRED_SOURCE : FALLBACK_SOURCE;
  if (!existsSync(source)) {
    console.error("[bootstrap] no source asset; expected", PREFERRED_SOURCE, "or", FALLBACK_SOURCE);
    process.exit(2);
  }
  await fs.mkdir(path.dirname(OUT_BLEND), { recursive: true });

  console.log(`[bootstrap] Blender: ${blenderPath}`);
  console.log(`[bootstrap] source:  ${source}`);
  console.log(`[bootstrap] target:  ${OUT_BLEND}`);

  const args = [
    "--background", "--factory-startup",
    "--python", SCRIPT_PATH,
    "--", OUT_BLEND, source,
  ];
  const child = spawn(blenderPath, args, { stdio: "inherit" });
  child.on("exit", (code) => process.exit(code ?? 0));
  child.on("error", (err) => {
    console.error("[bootstrap] spawn failed:", err);
    process.exit(1);
  });
}

main().catch((err) => {
  console.error("[bootstrap] fatal:", err);
  process.exit(1);
});
