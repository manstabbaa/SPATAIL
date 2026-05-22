// Recursively scans /assets_raw for supported 3D files.
// Skips hidden, lock, and Office-temp files (~$*).

import { promises as fs } from "node:fs";
import path from "node:path";

const SUPPORTED = new Set([
  ".glb", ".gltf",
  ".obj",
  ".fbx",
  ".stl",
  ".usd", ".usda", ".usdc", ".usdz",
  ".step", ".stp",
  ".iges", ".igs",
  ".ply",
]);

// File extensions a CAD source folder is likely to drop alongside the model.
// We track these but don't try to load them — they're noted in the inventory
// so the user can see what was ignored.
const KNOWN_UNSUPPORTED = new Set([
  ".sldprt", ".sldasm", ".prt", ".asm",   // SolidWorks
  ".catpart", ".catproduct",              // CATIA
  ".ipt", ".iam",                         // Inventor
  ".par", ".psm",                         // Solid Edge
  ".3dm",                                 // Rhino
  ".x_t", ".x_b",                         // Parasolid
  ".dwg", ".dxf",                         // AutoCAD
]);

async function walk(root, hits = []) {
  const entries = await fs.readdir(root, { withFileTypes: true });
  for (const entry of entries) {
    const full = path.join(root, entry.name);
    if (entry.name.startsWith(".") || entry.name.startsWith("~$")) continue;
    if (entry.isDirectory()) {
      await walk(full, hits);
    } else if (entry.isFile()) {
      hits.push(full);
    }
  }
  return hits;
}

export async function scanAssetsRaw(rootDir) {
  let files;
  try {
    files = await walk(rootDir);
  } catch (e) {
    if (e.code === "ENOENT") return { supported: [], ignored: [] };
    throw e;
  }

  const supported = [];
  const ignored = [];

  for (const file of files) {
    const ext = path.extname(file).toLowerCase();
    const stat = await fs.stat(file);
    const rel = path.relative(rootDir, file);

    if (SUPPORTED.has(ext)) {
      supported.push({
        absolutePath: file,
        relativePath: rel,
        fileName: path.basename(file),
        extension: ext,
        sizeBytes: stat.size,
      });
    } else if (KNOWN_UNSUPPORTED.has(ext)) {
      ignored.push({
        absolutePath: file,
        relativePath: rel,
        fileName: path.basename(file),
        extension: ext,
        reason: "format requires native CAD app; not supported in v0.1",
      });
    }
    // Silently skip images, docs, etc. — they're not relevant noise.
  }

  // Stable ordering helps reproducibility.
  supported.sort((a, b) => a.relativePath.localeCompare(b.relativePath));
  ignored.sort((a, b) => a.relativePath.localeCompare(b.relativePath));

  return { supported, ignored };
}
