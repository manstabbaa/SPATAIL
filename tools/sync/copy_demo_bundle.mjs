#!/usr/bin/env node
// tools/sync/copy_demo_bundle.mjs
//
// Copies bundles/<DEMO_BUNDLE>.spatail into the iOS SwiftPM library's
// Resources/ folder so it ships inside the app binary. Lets the player
// load the demo on first launch without any AirDrop / Files dance.
//
// The canonical artefact lives in bundles/ (produced by
// pipeline/blender/spatail_export_xr.py). This script keeps the iOS-side
// copy in lockstep. Re-run after the demo bundle is re-exported.
//
//     npm run sync:demo-bundle
//
// To swap the demo bundle, edit DEMO_BUNDLE_NAME below.

import { copyFileSync, existsSync, mkdirSync, statSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, "..", "..");

const DEMO_BUNDLE_NAME = "f1_wheel_buttons.spatail";

const SRC = resolve(REPO_ROOT, "bundles", DEMO_BUNDLE_NAME);
const DEST_DIR = resolve(
  REPO_ROOT,
  "ios/SpatailPlayer/Sources/SpatailPlayer/Resources",
);
const DEST = resolve(DEST_DIR, DEMO_BUNDLE_NAME);

if (!existsSync(SRC)) {
  console.error(`[copy_demo_bundle] source not found: ${SRC}`);
  console.error(
    "Run pipeline/blender/spatail_export_xr.py first to produce a bundle.",
  );
  process.exit(1);
}

mkdirSync(DEST_DIR, { recursive: true });
copyFileSync(SRC, DEST);

const sizeMB = (statSync(DEST).size / 1024 / 1024).toFixed(2);
console.log(`[copy_demo_bundle] ${SRC}`);
console.log(`                → ${DEST}`);
console.log(`                  (${sizeMB} MB)`);
