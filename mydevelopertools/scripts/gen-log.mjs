#!/usr/bin/env node
/*
 * gen-log.mjs — pull new git commits into the developer log as DRAFT entries.
 *
 * It only READS git history and only WRITES mydevelopertools/data/entries.js.
 * It never touches the rest of the repo. Commits already referenced by an
 * entry's `commits` array are skipped, so it is safe to run repeatedly.
 *
 * Usage:   node mydevelopertools/scripts/gen-log.mjs [--count 60]
 * Then:    open the log, find the new "draft" cards, and edit them in
 *          data/entries.js into clear, summarized entries (set status to
 *          "shipped"/"in-progress" when done).
 *
 * No dependencies — Node 16+.
 */
import { execSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const __dir = dirname(fileURLToPath(import.meta.url));
const DATA = resolve(__dir, "..", "data", "entries.js");

const countArg = process.argv.indexOf("--count");
const COUNT = countArg !== -1 ? parseInt(process.argv[countArg + 1], 10) || 60 : 60;

function git(args) {
  return execSync(`git ${args}`, { encoding: "utf8" }).trim();
}

function short(h) { return String(h || "").slice(0, 7); }

function guessCategory(subject) {
  const s = subject.toLowerCase();
  if (/^(fix|bug)\b|: fix|hotfix/.test(s)) return "fix";
  if (/^docs\b|readme/.test(s)) return "docs";
  if (/^refactor\b|rewrite|restructure/.test(s)) return "refactor";
  if (/^(build|ci|chore|tooling|infra)\b/.test(s)) return "tooling";
  if (/^(feat|feature)\b|add |implement|pipeline/.test(s)) return "feature";
  return "feature";
}

function guessTags(subject) {
  const tags = new Set();
  const scope = subject.match(/^[a-z]+\(([^)]+)\)/i); // e.g. ios(perception)
  if (scope) scope[1].split(/[,\/ ]+/).forEach((t) => t && tags.add(t.toLowerCase()));
  ["ios", "web", "android", "blender", "arkit", "realitykit", "vision"].forEach((k) => {
    if (subject.toLowerCase().includes(k)) tags.add(k);
  });
  return Array.from(tags);
}

function slug(subject) {
  return subject.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 48);
}

function readEntries() {
  const text = readFileSync(DATA, "utf8");
  const start = text.indexOf("[");
  const end = text.lastIndexOf("]");
  if (start === -1 || end === -1) throw new Error("Could not find the array in entries.js");
  const arr = JSON.parse(text.slice(start, end + 1));
  return { arr, prefix: text.slice(0, start), suffix: text.slice(end + 1) };
}

function main() {
  const root = git("rev-parse --show-toplevel");
  const { arr, prefix, suffix } = readEntries();

  const known = new Set();
  arr.forEach((e) => (e.commits || []).forEach((h) => known.add(short(h))));

  // %H \x1f %h \x1f %ad \x1f %s \x1f %b \x1e
  const FMT = "%H%x1f%h%x1f%ad%x1f%s%x1f%b%x1e";
  const raw = execSync(`git -C "${root}" log -n ${COUNT} --date=short --pretty=format:${FMT}`,
    { encoding: "utf8" });

  const drafts = [];
  raw.split("\x1e").forEach((rec) => {
    const line = rec.replace(/^\s+/, "");
    if (!line) return;
    const [, h, date, subject, body] = line.split("\x1f");
    if (!h || known.has(short(h))) return;
    const details = (body || "")
      .split("\n").map((l) => l.trim())
      .filter((l) => l && !l.startsWith("Co-Authored-By") && !l.startsWith("🤖"));
    drafts.push({
      id: `${date}-${slug(subject)}`,
      date,
      title: subject,
      category: guessCategory(subject),
      status: "draft",
      area: "",
      summary: subject,
      details,
      why: "",
      tags: guessTags(subject),
      files: [],
      commits: [short(h)]
    });
  });

  if (!drafts.length) {
    console.log("No new commits to log. (Everything is already referenced.)");
    return;
  }

  // Newest commit first; prepend drafts ahead of the curated entries.
  const merged = drafts.concat(arr);
  writeFileSync(DATA, prefix + JSON.stringify(merged, null, 2) + suffix, "utf8");
  console.log(`Added ${drafts.length} draft entr${drafts.length === 1 ? "y" : "ies"} to data/entries.js:`);
  drafts.forEach((d) => console.log(`  · [${d.commits[0]}] ${d.title}`));
  console.log("\nOpen index.html, then edit the new drafts into clear summaries.");
}

main();
