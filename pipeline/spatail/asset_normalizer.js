// SPATAIL asset normalizer.
//
// For every assetGroupRef a card references, find the matching probed
// group under /assets_raw and run the existing headless Blender pipeline
// to produce a single normalized .glb in /assets_processed. The result
// gets attached to the contract's requiredAssets entry so renderers
// (web + iPhone AR) can load the real geometry instead of placeholders.
//
// Up-to-date check: if the cached .glb is newer than every source file
// in its group, the Blender step is skipped. That keeps re-runs cheap.

import { promises as fs, existsSync, statSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { findBlender, processOne, copyPassThrough, isPassThrough }
  from "../blender_runner.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = path.resolve(__dirname, "..", "..");
const ANALYZE_SCRIPT = path.join(
  PROJECT_ROOT, "blender_tools", "analyze_asset.py",
);
const PROCESSED_DIR = path.join(PROJECT_ROOT, "assets_processed");
const SEGMENTED_DIR = path.join(PROCESSED_DIR, "segmented");

const PROCESSED_PREFIX = "spatail__";

/** Scan /assets_processed/segmented for a GLB whose basename best matches
 *  the given groupKey or its source filenames. Scoring:
 *    - +2 for every token the GLB stem shares with a SOURCE filename
 *      (source file is the strongest signal — that's what the user uploaded)
 *    - +1 for every token the GLB stem shares with the GROUP KEY
 *    - generic CAD tokens ("car", "engine", "wheel") count for less than
 *      specific ones — but we don't enumerate; the source-file weighting
 *      handles disambiguation.
 *  Returns absolute path or null. Highest score wins; ties broken by name.
 */
function findSegmentedGLB(groupKey, sourceFileNames = []) {
  if (!existsSync(SEGMENTED_DIR)) return null;
  let entries;
  try { entries = readdirSync(SEGMENTED_DIR); } catch { return null; }
  const glbs = entries.filter((e) => e.toLowerCase().endsWith(".glb"));
  if (glbs.length === 0) return null;

  const tokenize = (s) =>
    String(s).toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
  const groupTokens = new Set(tokenize(groupKey));
  const sourceTokens = new Set();
  for (const f of sourceFileNames) for (const t of tokenize(f)) sourceTokens.add(t);

  let bestScore = 0;
  let bestPath = null;
  for (const name of glbs) {
    const stem = tokenize(path.basename(name, ".glb"));
    let score = 0;
    for (const t of stem) {
      if (sourceTokens.has(t)) score += 2;
      if (groupTokens.has(t))  score += 1;
    }
    if (score > bestScore) {
      bestScore = score;
      bestPath = path.join(SEGMENTED_DIR, name);
    }
  }
  if (bestPath) return bestPath;
  // Only one GLB in the folder? Use it. Two or more with zero matches?
  // Don't guess — return null so the caller falls through to Blender.
  if (glbs.length === 1) return path.join(SEGMENTED_DIR, glbs[0]);
  return null;
}

function safeId(s) {
  return String(s).replace(/[^a-zA-Z0-9]+/g, "_").slice(0, 60);
}

function tokensOverlap(a, b) {
  const ta = new Set(String(a).toLowerCase().split(/[^a-z0-9]+/).filter(Boolean));
  const tb = new Set(String(b).toLowerCase().split(/[^a-z0-9]+/).filter(Boolean));
  for (const t of ta) if (tb.has(t)) return true;
  return false;
}

function uniqueAssetRefs(cards) {
  const refs = new Set();
  for (const card of cards) {
    for (const src of card.sources || []) {
      const ref = src.assetGroupRef;
      if (ref && typeof ref === "string") refs.add(ref);
    }
  }
  return [...refs];
}

function pickPrimaryItem(items) {
  // Prefer an item whose role is primary_object (set by classifier.inferRoles).
  // Fall back to the first item; this keeps the v1 flow simple — multi-part
  // assemblies need their own resolver later.
  return items.find((i) => i.role === "primary_object") || items[0] || null;
}

function isCachedFresh(cachedPath, sourcePaths) {
  if (!existsSync(cachedPath)) return false;
  const cachedTime = statSync(cachedPath).mtimeMs;
  for (const sp of sourcePaths) {
    if (!existsSync(sp)) continue;
    if (statSync(sp).mtimeMs > cachedTime) return false;
  }
  return true;
}

/**
 * Normalize every assetGroupRef referenced by `cards` against the
 * `probedAssetGroups` from /assets_raw. Returns a map:
 *   ref -> { processedPath, sourcePath, importer, status, reason }
 *
 * The returned map keys match the strings cards put in `assetGroupRef`,
 * so the planner can look them up directly.
 */
export async function normalizeReferencedAssets(cards, probedAssetGroups) {
  const refs = uniqueAssetRefs(cards);
  if (refs.length === 0) return new Map();

  await fs.mkdir(PROCESSED_DIR, { recursive: true });
  const blenderPath = await findBlender();
  const out = new Map();

  for (const ref of refs) {
    // Best-score match instead of first-token-overlap — otherwise
    // assetGroupRef "f1-car" resolves to the "car-engine" group because
    // they share the token "car".
    const refTokens = new Set(
      String(ref).toLowerCase().split(/[^a-z0-9]+/).filter(Boolean),
    );
    let group = null, bestScore = 0;
    for (const g of probedAssetGroups) {
      const hayTokens = `${g.groupKey} ${g.sceneName}`.toLowerCase()
        .split(/[^a-z0-9]+/).filter(Boolean);
      let s = 0;
      for (const t of hayTokens) if (refTokens.has(t)) s += 1;
      if (s > bestScore) { bestScore = s; group = g; }
    }
    if (!group) {
      out.set(ref, {
        ref,
        status: "no_group",
        reason: `no folder under /assets_raw matches assetGroupRef "${ref}".`,
      });
      continue;
    }

    const item = pickPrimaryItem(group.items);
    if (!item) {
      out.set(ref, {
        ref, status: "empty_group",
        reason: `group "${group.groupKey}" has no usable items.`,
      });
      continue;
    }

    const absSource = path.join(PROJECT_ROOT, "assets_raw", item.relativePath);
    const assetId = `${PROCESSED_PREFIX}${safeId(group.groupKey)}__${safeId(item.fileName)}`;
    const cachedPath = path.join(PROCESSED_DIR, `${assetId}.glb`);

    // Prefer a pre-segmented GLB if one exists. The segmentation script
    // (segment_obj.py) writes to /assets_processed/segmented/<id>.glb with
    // unit normalisation, axis fix, baseline PBR materials, and the validation
    // renders that feed the labelling step. We use the file whose basename
    // matches the group's tokens so authoring stays decoupled.
    const segmentedPath = findSegmentedGLB(
      group.groupKey,
      group.items.map((i) => i.fileName),
    );
    if (segmentedPath && existsSync(segmentedPath)) {
      // Mirror it into the cache path so the contract URL stays predictable.
      await fs.copyFile(segmentedPath, cachedPath);
      out.set(ref, {
        ref, status: "ok", cached: false,
        sourcePath: segmentedPath,
        processedPath: cachedPath,
        importer: "presegmented",
      });
      console.log(`[spatail/assets]   ${ref}: using pre-segmented GLB ${path.basename(segmentedPath)}`);
      continue;
    }

    if (isCachedFresh(cachedPath, [absSource])) {
      out.set(ref, {
        ref, status: "ok", cached: true,
        sourcePath: absSource,
        processedPath: cachedPath,
        importer: "cached",
      });
      console.log(`[spatail/assets]   ${ref}: cached -> ${path.basename(cachedPath)}`);
      continue;
    }

    if (blenderPath) {
      console.log(`[spatail/assets]   ${ref}: normalizing ${item.relativePath} via Blender…`);
      const analysis = await processOne({
        blenderPath,
        scriptPath: ANALYZE_SCRIPT,
        inputPath: absSource,
        outputDir: PROCESSED_DIR,
        assetId,
      });
      out.set(ref, {
        ref,
        status: analysis.status === "ok" ? "ok" : "failed",
        reason: analysis.reason || null,
        sourcePath: absSource,
        processedPath: analysis.status === "ok" ? analysis.processedPath : null,
        importer: analysis.importer || null,
        metrics: analysis.metrics || null,
      });
      if (analysis.status === "ok") {
        console.log(`[spatail/assets]   ${ref}: ok via ${analysis.importer}`);
      } else {
        console.warn(`[spatail/assets]   ${ref}: ${analysis.reason}`);
      }
      continue;
    }

    // No Blender. Fall through with pass-through copy for browser-native formats.
    if (isPassThrough(item.extension)) {
      const copy = await copyPassThrough({
        inputPath: absSource, outputDir: PROCESSED_DIR, assetId,
      });
      out.set(ref, {
        ref, status: "ok", cached: false,
        sourcePath: absSource, processedPath: copy.processedPath,
        importer: "passthrough_copy",
      });
      console.log(`[spatail/assets]   ${ref}: pass-through copy (no Blender)`);
      continue;
    }

    out.set(ref, {
      ref, status: "failed",
      reason: `Blender not available and "${item.extension}" needs it. ` +
              "Install Blender 4.1+, set $env:SPATIAL_BLENDER, or pre-convert to .glb/.stl/.obj.",
    });
  }

  return out;
}

/**
 * Project-relative processed path, forward-slashed (URL-style) so the
 * web viewer can fetch it directly: `/assets_processed/spatail__…glb`.
 */
export function toProjectRelativeURL(absPath) {
  return "/" + path.relative(PROJECT_ROOT, absPath).replace(/\\/g, "/");
}
