// pipeline/bundle_ios.js — npm run spatail:ios:bundle
//
// Builds dist/SPATAILMobileAR-<sha>.zip with everything the Mac side
// needs to open Xcode and build to a device:
//   - SPATAILMobileAR/        (Swift sources, project.yml, Info.plist, README)
//   - SPATAILMobileAR/Resources/  ← current spatial contracts + the
//                                    GLBs they reference + Blender-authored
//                                    animation sidecars (only what's actually
//                                    used; no orphan files)
//   - MAC_BUILD.md            (at zip root — first thing to read)
//   - AUDIT_NOTES.md          (copy of SPATAILMobileAR/AUDIT_NOTES.md
//                              promoted to the root for the same reason)
//
// Zip is created via PowerShell's built-in Compress-Archive so the
// script has zero npm deps. CRLF↔LF normalisation: source text files
// (.swift/.json/.md/.plist/.yml) are checked for trailing CRs and
// rewritten to LF inside a working directory before zipping. The
// originals on disk are left alone.
//
// Output filename includes a deterministic short SHA derived from the
// content so duplicate bundles are obvious.

import { promises as fs, existsSync, statSync, readdirSync } from "node:fs";
import path from "node:path";
import os from "node:os";
import crypto from "node:crypto";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = path.resolve(__dirname, "..");

const APP_DIR        = path.join(PROJECT_ROOT, "SPATAILMobileAR");
const CONTRACTS_DIR  = path.join(PROJECT_ROOT, "scene_contracts");
const PROCESSED_DIR  = path.join(PROJECT_ROOT, "assets_processed");
const ANIMATIONS_DIR = path.join(PROCESSED_DIR, "animations");
const DIST_DIR       = path.join(PROJECT_ROOT, "dist");

// Text files we normalise CRLF → LF inside the bundle.
const TEXT_EXTS = new Set([
  ".swift", ".json", ".md", ".plist", ".yml", ".yaml", ".sh", ".txt",
]);

// ---------------------------------------------------------------------------

async function main() {
  await fs.mkdir(DIST_DIR, { recursive: true });

  const stage = await fs.mkdtemp(path.join(os.tmpdir(), "spatail-bundle-"));
  console.log(`[bundle] staging in ${stage}`);

  const bundleAppDir = path.join(stage, "SPATAILMobileAR");
  await copyTree(APP_DIR, bundleAppDir);

  const resourcesDir = path.join(bundleAppDir, "Resources");
  await fs.mkdir(resourcesDir, { recursive: true });

  // Bundled spatial contracts → Resources/, plus the GLBs they reference.
  const used = await collectReferencedAssets();
  console.log(
    `[bundle] including ${used.contracts.length} contract(s) + ` +
    `${used.glbs.length} GLB(s) + ${used.sidecars.length} authored sidecar(s)`,
  );
  for (const c of used.contracts) {
    await copyFile(c, path.join(resourcesDir, path.basename(c)));
  }
  for (const g of used.glbs) {
    await copyFile(g, path.join(resourcesDir, path.basename(g)));
  }
  for (const s of used.sidecars) {
    await copyFile(s, path.join(resourcesDir, path.basename(s)));
  }
  if (existsSync(path.join(CONTRACTS_DIR, "_spatail_index.json"))) {
    await copyFile(
      path.join(CONTRACTS_DIR, "_spatail_index.json"),
      path.join(resourcesDir, "_spatail_index.json"),
    );
  }

  // Promote MAC_BUILD.md + AUDIT_NOTES.md to the zip root.
  await writeMacBuildMd(stage);
  if (existsSync(path.join(APP_DIR, "AUDIT_NOTES.md"))) {
    await copyFile(
      path.join(APP_DIR, "AUDIT_NOTES.md"),
      path.join(stage, "AUDIT_NOTES.md"),
    );
  }

  // CRLF → LF + strip BOM across all text files in the staging dir.
  await normaliseLineEndings(stage);

  // Stable, short content-hash so re-bundling identical sources is obvious.
  const sha = await dirHash(stage);
  const outZip = path.join(DIST_DIR, `SPATAILMobileAR-${sha}.zip`);
  if (existsSync(outZip)) {
    await fs.rm(outZip);  // Compress-Archive refuses to overwrite
  }
  await zipDir(stage, outZip);
  console.log(`[bundle] wrote ${outZip} (${(statSync(outZip).size / 1024 / 1024).toFixed(2)} MB)`);

  // Clean up the staging tree.
  await fs.rm(stage, { recursive: true, force: true });
  console.log(`[bundle] done.`);
}

// ---------------------------------------------------------------------------
// Asset collection
// ---------------------------------------------------------------------------

/** Returns absolute paths to every spatial contract, every GLB the
 *  contracts reference, and every Blender-authored animations.json the
 *  contracts reference (via the animations/ bundle's assetId). */
async function collectReferencedAssets() {
  const contracts = [];
  const glbs = new Set();
  const sidecars = new Set();
  if (!existsSync(CONTRACTS_DIR)) return { contracts, glbs: [...glbs], sidecars: [...sidecars] };

  for (const name of await fs.readdir(CONTRACTS_DIR)) {
    if (!name.endsWith("-spatial-contract.json")) continue;
    const abs = path.join(CONTRACTS_DIR, name);
    contracts.push(abs);
    const json = JSON.parse(await fs.readFile(abs, "utf-8"));
    for (const el of json.spatialElements || []) {
      for (const r of el.requiredAssets || []) {
        const p = r.processedAssetPath;
        if (typeof p !== "string") continue;
        // p is project-relative (`/assets_processed/animations/wheel/wheel.glb`).
        const local = path.join(PROJECT_ROOT, p.replace(/^\//, ""));
        if (existsSync(local)) glbs.add(local);
      }
    }
  }
  // Pick up authored animation sidecars next to each referenced GLB.
  if (existsSync(ANIMATIONS_DIR)) {
    for (const dir of readdirSync(ANIMATIONS_DIR)) {
      const base = path.join(ANIMATIONS_DIR, dir);
      if (!statSync(base).isDirectory()) continue;
      for (const f of readdirSync(base)) {
        if (f.endsWith(".animations.json") || f.endsWith(".sequence_hints.json")) {
          sidecars.add(path.join(base, f));
        }
      }
    }
  }
  return { contracts, glbs: [...glbs], sidecars: [...sidecars] };
}

// ---------------------------------------------------------------------------
// File ops — copyTree skips .DS_Store / .git / node_modules / .blend1 noise.
// ---------------------------------------------------------------------------

const SKIP = new Set([".git", "node_modules", ".DS_Store"]);
const SKIP_EXT = new Set([".blend1", ".blend2"]);

async function copyTree(src, dst) {
  await fs.mkdir(dst, { recursive: true });
  for (const ent of await fs.readdir(src, { withFileTypes: true })) {
    if (SKIP.has(ent.name)) continue;
    if (SKIP_EXT.has(path.extname(ent.name))) continue;
    const s = path.join(src, ent.name);
    const d = path.join(dst, ent.name);
    if (ent.isDirectory()) await copyTree(s, d);
    else await copyFile(s, d);
  }
}

async function copyFile(src, dst) {
  await fs.mkdir(path.dirname(dst), { recursive: true });
  await fs.copyFile(src, dst);
}

// CRLF → LF + BOM strip across text files, recursive. Quiet about
// binary content (lookup by extension).
async function normaliseLineEndings(root) {
  let changed = 0;
  for await (const file of walkFiles(root)) {
    if (!TEXT_EXTS.has(path.extname(file).toLowerCase())) continue;
    const buf = await fs.readFile(file);
    let str = buf.toString("utf-8");
    let mutated = false;
    if (str.charCodeAt(0) === 0xFEFF) { str = str.slice(1); mutated = true; }
    if (str.includes("\r\n")) { str = str.replace(/\r\n/g, "\n"); mutated = true; }
    if (mutated) { await fs.writeFile(file, str, "utf-8"); changed += 1; }
  }
  console.log(`[bundle] normalised line endings on ${changed} text file(s)`);
}

async function* walkFiles(dir) {
  for (const ent of await fs.readdir(dir, { withFileTypes: true })) {
    const p = path.join(dir, ent.name);
    if (ent.isDirectory()) yield* walkFiles(p);
    else yield p;
  }
}

// ---------------------------------------------------------------------------
// Hash + zip
// ---------------------------------------------------------------------------

async function dirHash(root) {
  const files = [];
  for await (const f of walkFiles(root)) files.push(f);
  files.sort();
  const h = crypto.createHash("sha256");
  for (const f of files) {
    h.update(path.relative(root, f).replace(/\\/g, "/"));
    h.update("\0");
    h.update(await fs.readFile(f));
    h.update("\0");
  }
  return h.digest("hex").slice(0, 10);
}

/** Cross-platform zip:
 *   - Windows: PowerShell's Compress-Archive (built in).
 *   - macOS / Linux: `zip -r` (every Mac has it; matches `unzip` parity).
 */
function zipDir(srcDir, outZip) {
  return new Promise((resolve, reject) => {
    let cmd, args;
    if (os.platform() === "win32") {
      cmd = "powershell";
      args = ["-NoProfile", "-Command",
        `Compress-Archive -Path '${srcDir}\\*' -DestinationPath '${outZip}' -Force`];
    } else {
      // -r recurse, -X strip extra attributes, -q quiet — cd into src so
      // the archive's top-level matches the user's expectation.
      cmd = "bash";
      args = ["-c", `cd '${srcDir}' && zip -rqX '${outZip}' .`];
    }
    const child = spawn(cmd, args, { stdio: "inherit" });
    child.on("exit", (code) => code === 0 ? resolve() : reject(new Error(`zip exited ${code}`)));
    child.on("error", reject);
  });
}

// ---------------------------------------------------------------------------
// MAC_BUILD.md — written into the staging root, not the source tree.
// ---------------------------------------------------------------------------

async function writeMacBuildMd(stage) {
  const md = `# SPATAILMobileAR — Mac Build

You're holding the cross-platform transfer bundle. The scaffold was authored
on Windows and was never compiled there. Open on a Mac, follow the steps
below, and report the first thing that breaks.

## Pre-flight

Required:
- macOS with **Xcode 16.3+** (Swift 6.1; the scaffold uses trailing commas
  in argument lists — SE-0439 — which earlier toolchains reject).
- A physical **iPhone**. ARKit does not run in the simulator.
- An Apple Developer account for code signing (any tier; free works).

One-time:

\`\`\`bash
brew install xcodegen
\`\`\`

## Build

\`\`\`bash
unzip SPATAILMobileAR-<sha>.zip
cd SPATAILMobileAR
xcodegen generate
open SPATAILMobileAR.xcodeproj
\`\`\`

In Xcode:

1. Pick the **SPATAILMobileAR** scheme; pick your iPhone as the destination.
2. **Signing & Capabilities** → set your Team. Bundle ID will autoresolve;
   you may need to change it to a unique reverse-DNS string the first time.
3. Cmd-R to build & run. Camera permission will prompt on first launch.

## First-run behaviour

The app opens straight into **Room Scan** (this is intentional — every
contract is meaningless until we know what your room actually looks
like). Hold the phone up and sweep slowly across walls / floor /
furniture; the coverage ring fills as the scanner accumulates surfaces.
At ~75% the "Continue" button unlocks. Tapping it persists a
\`RoomContract\` to \`Documents/rooms/<roomId>.json\` and drops you on the
**Demo Selector**.

Subsequent launches skip the scan and go straight to the selector —
\`AppRoot.swift\` checks \`RoomContractIO.mostRecent()\` first.

## Common pitfalls

- **"trailing comma in argument list"** → you're on Xcode < 16.3. Either
  upgrade Xcode or regex-replace \`,(\\s*\\n\\s*)\\)\` → \`$1)\` in the project.
- **\`PhysicallyBasedMaterial.Opacity\` init error** → search
  \`SPATAIL_NEEDS_MAC_BUILD_VERIFY\` and the AUDIT_NOTES.md at this zip's
  root for a list of the four sites. Replace with bare float literals.
- **Tap to place doesn't fire** → you're in the simulator. Run on a
  device.
- **Mesh scanning never crosses 75%** → you're on a non-LiDAR iPhone.
  The fallback path uses plane detection only and accumulates more
  slowly; sweep walls + floor + a horizontal surface for ~30s. The
  chip top-left says "LiDAR" or "Plane heuristic" so you know which
  tier you're on.
- **"camera access denied"** → Settings → SPATAIL → toggle camera back on.

## Audit

See \`AUDIT_NOTES.md\` (this directory) for the 9 lines that may need a
small adjustment depending on Xcode version. Each is marked inline in
the source with \`// SPATAIL_NEEDS_MAC_BUILD_VERIFY:\` plus the
recommended fix.

## What's bundled

- All Swift sources (Models / Services / Views / Reality / App).
- \`project.yml\` (XcodeGen) + \`Info.plist\` with the camera usage string.
- The currently-active spatial contracts (\`Resources/*-spatial-contract.json\`)
  + \`_spatail_index.json\`.
- The GLB(s) those contracts reference (mostly the Mercedes F1 wheel).
- Blender-authored animation sidecars (\`*.animations.json\` /
  \`*.sequence_hints.json\`) for the same.

## Refreshing the bundle

On the Windows host:

\`\`\`bash
npm run spatail              # regenerate contracts after any planner edit
npm run spatail:ios:bundle   # rebuild dist/SPATAILMobileAR-<sha>.zip
\`\`\`

A new bundle is overwriting-safe — re-unzip on the Mac and you're back
in a known-good state.
`;
  await fs.writeFile(path.join(stage, "MAC_BUILD.md"), md, "utf-8");
}

main().catch((err) => {
  console.error("[bundle] fatal:", err);
  process.exit(1);
});
