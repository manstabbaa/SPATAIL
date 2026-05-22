// `npm run spatail` — SPATAIL CLI.
//
// Loads one or more demo cards from /demos, runs the SPATAIL pipeline
// (ingest -> understand -> represent -> place -> contract), and writes a
// SpatialExperienceContract.json per card into /scene_contracts.
//
// Also writes /scene_contracts/_spatail_index.json so the viewer can list
// every available experience without scanning the directory itself.

import { promises as fs } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { ingestCard, probeAssetGroups } from "./spatail/content_ingestion.js";
import { planExperience } from "./spatail/experience_planner.js";
import { SPATAIL_SCHEMA_VERSION } from "./spatail/experience_contract.js";
import { normalizeReferencedAssets } from "./spatail/asset_normalizer.js";
import { loadAuthoredAnimations } from "./spatail/authored_animations.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = path.resolve(__dirname, "..");
const DEMOS_DIR = path.join(PROJECT_ROOT, "demos");
const CONTRACTS_DIR = path.join(PROJECT_ROOT, "scene_contracts");
const RAW_DIR = path.join(PROJECT_ROOT, "assets_raw");

async function main() {
  const requested = process.argv.slice(2);

  await fs.mkdir(CONTRACTS_DIR, { recursive: true });

  // List demo cards. Anything in /demos that ends in -card.json is fair game.
  let cardFiles;
  if (requested.length > 0) {
    cardFiles = requested.map((p) =>
      path.isAbsolute(p) ? p : path.resolve(process.cwd(), p),
    );
  } else {
    const entries = await fs.readdir(DEMOS_DIR).catch(() => []);
    cardFiles = entries
      .filter((f) => f.endsWith("-card.json"))
      .map((f) => path.join(DEMOS_DIR, f));
  }

  if (cardFiles.length === 0) {
    console.error(
      "[spatail] no card files. Drop one into /demos as <name>-card.json " +
      "or pass paths as CLI args.",
    );
    process.exit(1);
  }

  console.log(`[spatail] probing /assets_raw for available 3D groups…`);
  const probedAssetGroups = await probeAssetGroups(RAW_DIR);
  console.log(`[spatail]   found ${probedAssetGroups.length} asset group(s).`);

  // Ingest cards up front so we know which assetGroupRefs need normalizing.
  const ingested = [];
  for (const cardPath of cardFiles) {
    const card = await ingestCard(cardPath);
    ingested.push({ cardPath, card });
  }

  console.log(`[spatail] normalizing referenced asset groups via Blender…`);
  const normalizedAssets = await normalizeReferencedAssets(
    ingested.map((x) => x.card),
    probedAssetGroups,
  );

  // v0.4 — load the default mock RoomContract so every demo plans against
  // a real room. iOS replaces this with the captured ARKit room at runtime.
  let roomContract = null;
  try {
    const roomPath = path.join(CONTRACTS_DIR, "rooms", "_default_room.json");
    roomContract = JSON.parse(await fs.readFile(roomPath, "utf-8"));
    console.log(`[spatail] room: ${roomContract.label || roomContract.roomId} ` +
      `(${roomContract.surfaces?.length || 0} surfaces)`);
  } catch (err) {
    console.warn(`[spatail] no default room — placements stay generic (${err.message})`);
  }

  console.log(`[spatail] scanning /assets_processed/animations for authored bundles…`);
  const authoredAnimations = await loadAuthoredAnimations();
  if (authoredAnimations.length) {
    for (const a of authoredAnimations) {
      console.log(`[spatail/animations] authored bundle: ${a.assetId} ` +
        `(${a.animations.animations?.length || 0} anim, ` +
        `${a.hints?.sequences?.length || 0} sequence(s))`);
    }
  }

  const index = [];

  for (const { cardPath, card } of ingested) {
    console.log(`[spatail] processing ${path.relative(PROJECT_ROOT, cardPath)}`);
    console.log(`[spatail]   ingested ${card.sources.length} source(s).`);

    const contract = planExperience(card, {
      probedAssetGroups,
      normalizedAssets,
      authoredAnimations,
      roomContract,
    });
    console.log(
      `[spatail]   planned ${contract.spatialElements.length} element(s) ` +
      `[${summariseModes(contract.spatialElements)}]`,
    );

    const outName = contractFileNameFor(card, cardPath);
    const outPath = path.join(CONTRACTS_DIR, outName);
    await fs.writeFile(outPath, JSON.stringify(contract, null, 2), "utf-8");
    console.log(`[spatail]   wrote ${path.relative(PROJECT_ROOT, outPath)}`);

    index.push({
      experienceId: contract.experienceId,
      title: contract.title,
      detectedDomain: contract.detectedDomain?.name || "unknown",
      contractPath: `scene_contracts/${outName}`,
      sourcePrompt: contract.sourcePrompt,
      elementCount: contract.spatialElements.length,
      modeCounts: countByMode(contract.spatialElements),
      // Picker default — the viewer lands on this experience when the
      // user hits the root URL without ?id=. Carried from card.default.
      isDefault: card.isDefault === true,
    });
  }

  const indexPath = path.join(CONTRACTS_DIR, "_spatail_index.json");
  await fs.writeFile(indexPath, JSON.stringify({
    schemaVersion: SPATAIL_SCHEMA_VERSION,
    generatedAt: new Date().toISOString(),
    experiences: index,
  }, null, 2), "utf-8");
  console.log(`[spatail] wrote ${path.relative(PROJECT_ROOT, indexPath)}`);
  console.log(`[spatail] done.`);
}

function contractFileNameFor(card, cardPath) {
  // mustang-service-card.json -> mustang-service-spatial-contract.json
  const base = path
    .basename(cardPath, ".json")
    .replace(/-card$/, "")
    .replace(/[^a-zA-Z0-9_-]+/g, "-");
  return `${base || card.id}-spatial-contract.json`;
}

function summariseModes(elements) {
  const c = countByMode(elements);
  return Object.entries(c).map(([m, n]) => `${n}× ${m}`).join(", ");
}

function countByMode(elements) {
  const out = {};
  for (const e of elements) {
    out[e.representationMode] = (out[e.representationMode] || 0) + 1;
  }
  return out;
}

main().catch((err) => {
  console.error("[spatail] fatal:", err);
  process.exit(1);
});
