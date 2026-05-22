#!/usr/bin/env node
// tools/sync/gen_swift_vocab.mjs
//
// Generates ios/SpatailPlayer/Sources/SpatailPlayer/Contract/Vocab.swift
// from pipeline/spatail/experience_contract.js.
//
// The JS file is the single source of truth for closed-vocabulary values
// (MECHANIC_KINDS, ANIMATION_PRIMITIVES, INTERACTION_TRIGGERS, etc).
// This script ensures the Swift enums have identical string raw values.
//
// Run from repo root:
//     npm run sync:swift-vocab
//
// or directly:
//     node tools/sync/gen_swift_vocab.mjs

import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, "..", "..");
const JS_PATH = resolve(REPO_ROOT, "pipeline/spatail/experience_contract.js");
const SWIFT_PATH = resolve(
  REPO_ROOT,
  "ios/SpatailPlayer/Sources/SpatailPlayer/Contract/Vocab.swift",
);

// ────────────────────────────────────────────────────────────────────────
// Vocab map: which JS export names become which Swift enum names.
// Adding a new vocab = one row here, then re-run.
// ────────────────────────────────────────────────────────────────────────

const VOCABS = [
  { js: "MECHANIC_KINDS",        swift: "MechanicKind" },
  { js: "ANIMATION_PRIMITIVES",  swift: "AnimationPrimitive" },
  { js: "INTERACTION_TRIGGERS",  swift: "InteractionTrigger" },
  { js: "INTERACTION_ACTIONS",   swift: "InteractionAction" },
  { js: "CONTENT_TYPES",         swift: "ContentType" },
  { js: "REPRESENTATION_MODES",  swift: "RepresentationMode" },
  { js: "PLACEMENTS",            swift: "Placement" },
  { js: "ANCHOR_STRATEGIES",     swift: "AnchorStrategy" },
  { js: "SCALE_MODES",           swift: "ScaleMode" },
  { js: "ATTENTION_BEHAVIORS",   swift: "AttentionBehavior" },
  { js: "FIDELITIES",            swift: "Fidelity" },
  { js: "PRESENTATION_LAYOUTS",  swift: "PresentationLayout" },
];

const SCHEMA_VERSION_CONST = "SPATAIL_SCHEMA_VERSION";

// ────────────────────────────────────────────────────────────────────────
// Parse the JS file
// ────────────────────────────────────────────────────────────────────────

function parseJsExports(src) {
  const out = { schemaVersion: null, vocabs: {} };

  // Schema version: `export const SPATAIL_SCHEMA_VERSION = "x.y.z-...";`
  const sv = src.match(
    new RegExp(
      `export\\s+const\\s+${SCHEMA_VERSION_CONST}\\s*=\\s*"([^"]+)"`,
    ),
  );
  if (sv) out.schemaVersion = sv[1];

  // Vocab arrays. Match the array body (multi-line) up to the closing `];`.
  for (const { js } of VOCABS) {
    const re = new RegExp(
      `export\\s+const\\s+${js}\\s*=\\s*\\[([\\s\\S]*?)\\]\\s*;`,
    );
    const match = src.match(re);
    if (!match) {
      out.vocabs[js] = null;
      continue;
    }
    const body = match[1];
    // Strip // line comments, then pull each "..." token.
    const cleaned = body
      .split("\n")
      .map((line) => line.replace(/\/\/.*$/, ""))
      .join("\n");
    const values = [...cleaned.matchAll(/"([^"]+)"/g)].map((m) => m[1]);
    out.vocabs[js] = values;
  }

  return out;
}

// ────────────────────────────────────────────────────────────────────────
// Swift case-name slugifier
// ────────────────────────────────────────────────────────────────────────

function swiftCaseName(rawValue) {
  // Convert snake_case → camelCase
  const parts = rawValue.split(/[_\-\s]+/).filter(Boolean);
  return parts
    .map((p, i) =>
      i === 0
        ? p.charAt(0).toLowerCase() + p.slice(1)
        : p.charAt(0).toUpperCase() + p.slice(1),
    )
    .join("");
}

// ────────────────────────────────────────────────────────────────────────
// Swift emitter
// ────────────────────────────────────────────────────────────────────────

function emitSwift({ schemaVersion, vocabs }) {
  const lines = [];
  const stamp = new Date().toISOString();

  lines.push(
    "// ⚠️  GENERATED — DO NOT EDIT BY HAND",
    "//",
    "//  Source:    pipeline/spatail/experience_contract.js",
    "//  Generator: tools/sync/gen_swift_vocab.mjs",
    `//  Regenerated: ${stamp}`,
    "//",
    "//  To update: edit the JS file, then run `npm run sync:swift-vocab`.",
    "",
    "import Foundation",
    "",
    "public enum SpatailContract {",
    `    public static let schemaVersion = "${schemaVersion ?? "unknown"}"`,
    "}",
    "",
  );

  for (const { js, swift } of VOCABS) {
    const values = vocabs[js];
    if (!values) {
      lines.push(`// WARNING: ${js} not found in JS source.`);
      lines.push("");
      continue;
    }
    lines.push(
      `/// Mirrors \`${js}\` from experience_contract.js.`,
      `public enum ${swift}: String, Codable, CaseIterable, Sendable {`,
    );
    for (const raw of values) {
      const caseName = swiftCaseName(raw);
      const safe = SWIFT_RESERVED.has(caseName) ? "\`" + caseName + "\`" : caseName;
      lines.push(`    case ${safe} = "${raw}"`);
    }
    lines.push("}", "");
  }

  return lines.join("\n");
}

const SWIFT_RESERVED = new Set([
  "class", "struct", "enum", "protocol", "extension", "func", "var", "let",
  "if", "else", "for", "while", "switch", "case", "default", "return",
  "break", "continue", "true", "false", "nil", "self", "Self", "Type",
  "associatedtype", "where", "in", "is", "as", "throws", "throw", "try",
  "rethrows", "guard", "defer", "do", "catch", "fallthrough", "repeat",
  "static", "private", "public", "internal", "open", "fileprivate", "final",
  "lazy", "weak", "unowned", "operator", "import", "init", "deinit",
  "inout", "subscript", "typealias", "convenience", "override", "required",
]);

// ────────────────────────────────────────────────────────────────────────
// Entry point
// ────────────────────────────────────────────────────────────────────────

function main() {
  const src = readFileSync(JS_PATH, "utf8");
  const parsed = parseJsExports(src);

  // Sanity checks
  const missing = VOCABS.filter(({ js }) => !parsed.vocabs[js]);
  if (missing.length) {
    console.error(
      "[gen_swift_vocab] missing exports in JS:",
      missing.map((m) => m.js).join(", "),
    );
    process.exit(1);
  }
  if (!parsed.schemaVersion) {
    console.error("[gen_swift_vocab] could not find SPATAIL_SCHEMA_VERSION");
    process.exit(1);
  }

  const swift = emitSwift(parsed);

  mkdirSync(dirname(SWIFT_PATH), { recursive: true });
  writeFileSync(SWIFT_PATH, swift, "utf8");

  const totalCases = VOCABS.reduce(
    (n, { js }) => n + (parsed.vocabs[js]?.length || 0),
    0,
  );
  console.log(
    `[gen_swift_vocab] wrote ${SWIFT_PATH}`,
  );
  console.log(
    `  ${VOCABS.length} enums, ${totalCases} cases, schemaVersion="${parsed.schemaVersion}"`,
  );
}

main();
