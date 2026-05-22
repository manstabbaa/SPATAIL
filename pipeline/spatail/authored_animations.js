// authored_animations.js
//
// Reads the Blender-exported animation artefacts in
// /assets_processed/animations/<assetId>/ and surfaces them to the
// planner so they can splice into a contract.
//
// Match strategy: for each card.assetGroupRef, find an animations
// folder whose name shares tokens with the ref OR with the resolved
// processed asset's filename. We reuse the same loose tokeniser the
// asset normalizer uses so the rules don't diverge.

import { promises as fs, existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = path.resolve(__dirname, "..", "..");
const ANIM_ROOT = path.join(PROJECT_ROOT, "assets_processed", "animations");

function tokens(s) {
  return new Set(String(s).toLowerCase().split(/[^a-z0-9]+/).filter(Boolean));
}
function overlaps(a, b) {
  const ta = tokens(a), tb = tokens(b);
  for (const t of ta) if (tb.has(t)) return true;
  return false;
}

/** Returns an array of { assetId, dir, animations, interactions, hints }. */
export async function loadAuthoredAnimations() {
  if (!existsSync(ANIM_ROOT)) return [];
  let entries;
  try { entries = await fs.readdir(ANIM_ROOT, { withFileTypes: true }); }
  catch { return []; }
  const out = [];
  for (const ent of entries) {
    if (!ent.isDirectory()) continue;
    const dir = path.join(ANIM_ROOT, ent.name);
    const animJson = path.join(dir, `${ent.name}.animations.json`);
    const hintsJson = path.join(dir, `${ent.name}.sequence_hints.json`);
    if (!existsSync(animJson)) continue;
    try {
      const animations = JSON.parse(await fs.readFile(animJson, "utf-8"));
      const hints = existsSync(hintsJson)
        ? JSON.parse(await fs.readFile(hintsJson, "utf-8"))
        : null;
      out.push({
        assetId: ent.name,
        dir,
        animations,
        hints,
        // Project-relative URL the viewer can fetch the authored GLB from.
        glbUrl: animations.glb
          ? `/assets_processed/animations/${ent.name}/${animations.glb}`
          : null,
      });
    } catch (err) {
      console.warn(`[authored_animations] could not load ${animJson}:`, err.message);
    }
  }
  return out;
}

/** Pick the authored bundle whose assetId / meta best matches a card-side
 *  haystack (assetGroupRef + element titles + source names). The bundle's
 *  meta carries the assetGroupRef *if the author set it*; otherwise we
 *  fall back to its assetId, targetElementId, and the explicit assetGroupRef
 *  field. Returns null when nothing matches. */
export function pickAuthoredFor(haystacks, authored) {
  if (authored.length === 0) return null;
  const list = Array.isArray(haystacks) ? haystacks : [haystacks];
  const hayTokens = new Set();
  for (const h of list) for (const t of tokens(h)) hayTokens.add(t);

  // Score each authored bundle by the maximum token-overlap across its
  // candidate identifiers. Highest score wins — single-token matches are
  // ambiguous (e.g. "car" overlaps "f1-car" AND "car-engine"), so a 1-token
  // hit on the wheel bundle no longer pirates the F1 aero card.
  let bestScore = 0;
  let bestBundle = null;
  for (const a of authored) {
    const meta = a.animations?.meta || {};
    const candidates = [
      meta.assetGroupRef,
      meta.assetId,
      meta.targetElementId,
      a.assetId,
    ].filter(Boolean);
    let score = 0;
    for (const c of candidates) {
      const ct = tokens(c);
      // Require all-tokens match for high scores (exact identifier match);
      // single-token matches count for less.
      let exact = 0, partial = 0;
      for (const t of ct) if (hayTokens.has(t)) partial += 1;
      if (ct.length > 0 && partial === ct.length) exact = ct.length * 3;
      score = Math.max(score, exact || partial);
    }
    if (score > bestScore) {
      bestScore = score;
      bestBundle = a;
    }
  }
  // Threshold: require at least an exact 2-token match (score >= 6) OR a
  // 2-token partial overlap. A single weak "car" overlap doesn't qualify.
  if (bestScore >= 2) return bestBundle;
  return null;
}

/**
 * Merge an authored bundle into the {animations,interactions,sequences,
 * defaultSequenceId} the primitive-based planner already built. Authored
 * sequences REPLACE auto-generated ones for the same id; everything else
 * appends.
 *
 * Mutates and returns the layer object.
 */
export function mergeAuthored(layer, authored) {
  if (!authored) return layer;
  const a = authored.animations;

  // Append authored animations. Dedupe by id (authored wins).
  const byId = new Map((layer.animations || []).map((x) => [x.id, x]));
  for (const anim of a.animations || []) byId.set(anim.id, anim);
  layer.animations = [...byId.values()];

  // Append authored interactions, marking source for the inspector.
  for (const i of a.interactions || []) {
    layer.interactions.push({ ...i, _authoredFrom: a.assetId });
  }

  // Replace any matching sequence; otherwise append.
  if (authored.hints?.sequences?.length) {
    const seqById = new Map((layer.sequences || []).map((s) => [s.id, s]));
    for (const seq of authored.hints.sequences) {
      seqById.set(seq.id, { ...seq, fromBlend: true });
    }
    layer.sequences = [...seqById.values()];
    if (authored.hints.defaultSequenceId) {
      layer.defaultSequenceId = authored.hints.defaultSequenceId;
    }
  }
  return layer;
}

/** Patches the hero element's requiredAssets[0].processedAssetPath to
 *  point at the authored GLB (which carries baked transform / morph
 *  tracks the loop + transform_keyframes handlers need). */
export function patchHeroGLB(elements, authored) {
  if (!authored?.glbUrl) return;
  const targetId = authored.animations?.meta?.targetElementId;
  const hero = (targetId && elements.find((e) => e.id === targetId))
    || elements.find((e) => e.contentType === "physical_target");
  if (!hero) return;
  if (!hero.requiredAssets?.length) {
    hero.requiredAssets = [{ id: "authored", preferredSource: "blender" }];
  }
  hero.requiredAssets[0] = {
    ...hero.requiredAssets[0],
    processedAssetPath: authored.glbUrl,
    importer: "spatail_animation_export",
    _replacedByAuthoredBundle: authored.assetId,
  };
}
