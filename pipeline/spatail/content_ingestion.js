// ContentIngestionLayer
//
// Accepts the heterogeneous inputs SPATAIL is supposed to handle:
//   - a content/card JSON ({ prompt, sources: [...] })
//   - uploaded files / folders
//   - existing asset groups produced by the legacy CAD pipeline
//
// Normalizes everything into a single { prompt, sources[] } shape that
// the rest of the SPATAIL pipeline consumes. This is the seam where
// future inputs (PDFs, Confluence pages, Linear tickets, etc.) will be
// added — they all reduce to the same source list.

import { promises as fs } from "node:fs";
import path from "node:path";

import { scanAssetsRaw } from "../scanner.js";
import { classifyGroup, groupAssets, inferRoles } from "../classifier.js";

// Supported source kinds. Closed list — the understanding layer switches
// on these, so new kinds need a corresponding handler there.
export const SOURCE_KINDS = [
  "fact",            // key/value pair, eg { kind: "fact", key: "mileage", value: "45,672 mi" }
  "summary",         // titled prose chunk
  "numeric_summary", // KPI block: { title, kpis: [{label,value,delta?}] }
  "list",            // titled list of strings
  "steps",           // ordered procedure
  "timeline",        // events: [{label, when?, detail?}]
  "decisions",       // selectable next-actions: [{label, detail?}]
  "object3d",        // physical object reference (target / component / system)
  "diagnostic",      // a finding anchored to an object3d
  "process",         // a system/process model (often 3D)
  "guide",           // alignment line between two referenced elements
  "airflow",         // streamline / wind-tunnel visualization around a hero
];

export async function ingestCard(cardPath) {
  const raw = await fs.readFile(cardPath, "utf-8");
  const card = JSON.parse(raw);
  return normalizeCard(card, { cardPath });
}

export function ingestCardObject(card, { cardPath = null } = {}) {
  return normalizeCard(card, { cardPath });
}

function normalizeCard(card, { cardPath }) {
  if (!card || typeof card !== "object") {
    throw new Error("ingest: card must be a JSON object");
  }
  if (!card.prompt) {
    throw new Error("ingest: card.prompt is required");
  }
  if (!Array.isArray(card.sources)) {
    throw new Error("ingest: card.sources must be an array");
  }

  const sources = card.sources.map((s, i) => normalizeSource(s, i));

  return {
    cardPath: cardPath || null,
    id: card.id || deriveId(card.prompt),
    title: card.title || titleFromPrompt(card.prompt),
    prompt: card.prompt,
    environmentHint: card.environment || null,
    domainHint: card.domain || null,
    // Picker-default flag — surfaced to the index so the viewer can land
    // on a specific experience when several are present. Not part of the
    // contract schema; this is metadata for the experience picker only.
    isDefault: card.default === true,
    sources,
    fileSources: card.fileSources || [],
    // v0.5 — orchestrator overrides. When present, these bypass the
    // rule-based analyze + decompose stages so the demo cards carry
    // curated stage outputs while we are explicitly NOT shipping an
    // LLM behind the orchestrator yet.
    explanation: card.explanation || null,
    mechanics: card.mechanics || null,
    presentation: card.presentation || null,
  };
}

function normalizeSource(s, index) {
  if (!s || typeof s !== "object") {
    throw new Error(`ingest: source #${index} must be an object`);
  }
  if (!s.kind || !SOURCE_KINDS.includes(s.kind)) {
    throw new Error(
      `ingest: source #${index} has unknown kind '${s.kind}'. ` +
      `Allowed: ${SOURCE_KINDS.join(", ")}`,
    );
  }
  return { _index: index, ...s };
}

function titleFromPrompt(prompt) {
  return prompt
    .replace(/[.?!]+$/g, "")
    .replace(/^(help me|please|can you)\s+/i, "")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function deriveId(prompt) {
  return prompt
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 48) || "spatial-experience";
}

// Inspect /assets_raw for groups that look like they match an object3d
// reference. Reuses the legacy classifier so we don't duplicate the
// keyword logic. Returns a map: assetGroupRef -> { groupKey, domain, items[] }.
export async function probeAssetGroups(rawDir) {
  try {
    const { supported } = await scanAssetsRaw(rawDir);
    if (supported.length === 0) return [];
    const groups = groupAssets(supported);
    return groups.map((g) => {
      const classification = classifyGroup(g);
      const items = inferRoles(g.items);
      return {
        groupKey: g.groupKey,
        sceneName: classification.sceneName,
        detectedDomain: classification.detectedDomain,
        items: items.map((i) => ({
          fileName: i.fileName,
          relativePath: i.relativePath,
          extension: i.extension,
          role: i.role,
        })),
      };
    });
  } catch {
    return [];
  }
}

// Resolve a sourceContent.assetGroupRef against the probed groups.
// Loose match: substring or token overlap on groupKey/sceneName.
export function findAssetGroup(ref, probedGroups) {
  if (!ref || !probedGroups?.length) return null;
  const refTokens = tokenize(ref);
  let best = null;
  let bestScore = 0;
  for (const g of probedGroups) {
    const haystack = tokenize(`${g.groupKey} ${g.sceneName} ${g.detectedDomain}`);
    let score = 0;
    for (const t of refTokens) if (haystack.includes(t)) score += 1;
    if (score > bestScore) { bestScore = score; best = g; }
  }
  return bestScore > 0 ? best : null;
}

function tokenize(s) {
  return String(s)
    .toLowerCase()
    .replace(/[_\-.()/\\]+/g, " ")
    .split(/\s+/)
    .filter(Boolean);
}
