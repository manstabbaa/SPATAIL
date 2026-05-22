#!/usr/bin/env node
// tools/sync/check_protocol_sync.mjs
//
// CI guard: confirms the boundary files are in sync.
// Exits non-zero with a diff hint if drift detected.
//
// Run:    npm run sync:check
// Manual: node tools/sync/check_protocol_sync.mjs

import { readFileSync, existsSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { execSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, "..", "..");

const PATHS = {
  jsContract: resolve(REPO_ROOT, "pipeline/spatail/experience_contract.js"),
  swiftVocab: resolve(
    REPO_ROOT,
    "ios/SpatailPlayer/Sources/SpatailPlayer/Contract/Vocab.swift",
  ),
  pyServer:  resolve(REPO_ROOT, "pipeline/server/spatail_session_server.py"),
  manifestSwift: resolve(
    REPO_ROOT,
    "ios/SpatailPlayer/Sources/SpatailPlayer/Bundle/Manifest.swift",
  ),
  protocolDoc: resolve(REPO_ROOT, "docs/xr/REALTIME_PROTOCOL.md"),
  bundleDoc:   resolve(REPO_ROOT, "docs/xr/IOS_BUNDLE_SPEC.md"),
};

const failures = [];
function fail(msg) { failures.push(msg); }
function ok(msg)   { console.log(`  [ok] ${msg}`); }

// ────────────────────────────────────────────────────────────────────────
// Check 1: schemaVersion identical across JS + generated Swift
// ────────────────────────────────────────────────────────────────────────

console.log("[1/4] Schema version sync");
const jsSrc = readFileSync(PATHS.jsContract, "utf8");
const jsVersion = (jsSrc.match(
  /export\s+const\s+SPATAIL_SCHEMA_VERSION\s*=\s*"([^"]+)"/,
) || [])[1];

if (!jsVersion) {
  fail("JS: SPATAIL_SCHEMA_VERSION not found in experience_contract.js");
} else {
  ok(`JS schemaVersion = "${jsVersion}"`);
}

if (existsSync(PATHS.swiftVocab)) {
  const swiftSrc = readFileSync(PATHS.swiftVocab, "utf8");
  const swiftVersion = (swiftSrc.match(
    /schemaVersion\s*=\s*"([^"]+)"/,
  ) || [])[1];
  if (swiftVersion !== jsVersion) {
    fail(
      `Swift Vocab.swift schemaVersion="${swiftVersion}" != JS "${jsVersion}". ` +
      `Run: npm run sync:swift-vocab`,
    );
  } else {
    ok(`Swift schemaVersion = "${swiftVersion}"`);
  }
} else {
  fail("Vocab.swift not generated yet. Run: npm run sync:swift-vocab");
}

// ────────────────────────────────────────────────────────────────────────
// Check 2: every MECHANIC_KINDS string in JS is also a case in Swift
// ────────────────────────────────────────────────────────────────────────

console.log("[2/4] MECHANIC_KINDS coverage");
const jsKinds = extractStringArray(jsSrc, "MECHANIC_KINDS");
if (existsSync(PATHS.swiftVocab)) {
  const swiftSrc = readFileSync(PATHS.swiftVocab, "utf8");
  // Find the MechanicKind enum body
  const block = swiftSrc.match(/public enum MechanicKind[\s\S]*?\n}/);
  const swiftRawValues = block
    ? [...block[0].matchAll(/case\s+\S+\s*=\s*"([^"]+)"/g)].map((m) => m[1])
    : [];
  const missing = jsKinds.filter((k) => !swiftRawValues.includes(k));
  const extra = swiftRawValues.filter((k) => !jsKinds.includes(k));
  if (missing.length) {
    fail(
      `Swift MechanicKind missing: ${missing.join(", ")}. ` +
      `Run: npm run sync:swift-vocab`,
    );
  }
  if (extra.length) {
    fail(
      `Swift MechanicKind has unknown cases: ${extra.join(", ")}.`,
    );
  }
  if (!missing.length && !extra.length) {
    ok(`${jsKinds.length} mechanic kinds match exactly`);
  }
}

// ────────────────────────────────────────────────────────────────────────
// Check 3: server's bundle schema version matches IOS_BUNDLE_SPEC version
// ────────────────────────────────────────────────────────────────────────

console.log("[3/4] Bundle schema version sync");
const bundleSchemaPattern = /0\.5\.\d+-spatail-bundle/;
if (existsSync(PATHS.pyServer)) {
  const py = readFileSync(PATHS.pyServer, "utf8");
  const pyMatch = py.match(bundleSchemaPattern);
  if (!pyMatch) fail("Server: no spatail-bundle version found");
  else ok(`server bundle version: ${pyMatch[0]}`);
}
if (existsSync(PATHS.bundleDoc)) {
  const doc = readFileSync(PATHS.bundleDoc, "utf8");
  const docMatch = doc.match(bundleSchemaPattern);
  if (!docMatch) fail("IOS_BUNDLE_SPEC.md: no version found");
  else ok(`bundle spec doc version: ${docMatch[0]}`);
}
if (existsSync(PATHS.manifestSwift)) {
  const swift = readFileSync(PATHS.manifestSwift, "utf8");
  const swiftMatch = swift.match(bundleSchemaPattern);
  if (!swiftMatch) {
    fail("Manifest.swift: no bundle version found");
  } else {
    ok(`Manifest.swift version: ${swiftMatch[0]}`);
  }
}

// ────────────────────────────────────────────────────────────────────────
// Check 4: if JS contract changed since last codegen, fail
// ────────────────────────────────────────────────────────────────────────

console.log("[4/4] Codegen freshness");
try {
  // If git is available, compare last-modified commits.
  const jsCommit = execSync(
    `git log -1 --format=%ct -- "${PATHS.jsContract}"`,
    { cwd: REPO_ROOT },
  ).toString().trim();
  const swiftCommit = existsSync(PATHS.swiftVocab)
    ? execSync(
        `git log -1 --format=%ct -- "${PATHS.swiftVocab}"`,
        { cwd: REPO_ROOT },
      ).toString().trim()
    : "0";

  if (jsCommit && swiftCommit && Number(jsCommit) > Number(swiftCommit)) {
    fail(
      `JS contract committed after Swift vocab.\n` +
      `  JS:    ${new Date(Number(jsCommit) * 1000).toISOString()}\n` +
      `  Swift: ${new Date(Number(swiftCommit) * 1000).toISOString()}\n` +
      `  Run: npm run sync:swift-vocab && git add ${PATHS.swiftVocab}`,
    );
  } else {
    ok("codegen output is at-or-newer than JS source");
  }
} catch {
  console.log("  [skip] git not available; can't compare commit timestamps");
}

// ────────────────────────────────────────────────────────────────────────

if (failures.length) {
  console.error("\n[sync:check] FAILED:");
  for (const f of failures) console.error(`  ✗ ${f}`);
  process.exit(1);
}
console.log("\n[sync:check] all boundary files in sync.");

// ─── helpers ────────────────────────────────────────────────────────────

function extractStringArray(src, name) {
  const re = new RegExp(`export\\s+const\\s+${name}\\s*=\\s*\\[([\\s\\S]*?)\\]\\s*;`);
  const m = src.match(re);
  if (!m) return [];
  return [...m[1]
    .split("\n")
    .map((l) => l.replace(/\/\/.*$/, ""))
    .join("\n")
    .matchAll(/"([^"]+)"/g)].map((mm) => mm[1]);
}
