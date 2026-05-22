// Spawns Blender headlessly to import a source asset, write a normalized
// .glb to /assets_processed, and emit a sidecar JSON with metrics.
//
// Discovery order for the Blender executable:
//   1. SPATIAL_BLENDER env var
//   2. `blender` on PATH
//   3. Well-known Windows install paths (Blender 4.x, 5.x)
//
// If none are found, the pipeline runs in "no-Blender" mode: natively
// browser-loadable formats (.glb, .gltf, .obj, .stl) are copied straight
// through; CAD formats (STEP/STP/IGES, USDZ via Blender, FBX) are marked
// `failed` with a clear reason.

import { promises as fs, existsSync } from "node:fs";
import { spawn } from "node:child_process";
import path from "node:path";
import os from "node:os";

const WINDOWS_GUESSES = [
  "C:/Program Files/Blender Foundation/Blender 5.1/blender.exe",
  "C:/Program Files/Blender Foundation/Blender 5.0/blender.exe",
  "C:/Program Files/Blender Foundation/Blender 4.4/blender.exe",
  "C:/Program Files/Blender Foundation/Blender 4.3/blender.exe",
  "C:/Program Files/Blender Foundation/Blender 4.2/blender.exe",
  "C:/Program Files/Blender Foundation/Blender 4.1/blender.exe",
];

let cachedBlenderPath = undefined; // undefined = not probed yet; null = not found.

export async function findBlender() {
  if (cachedBlenderPath !== undefined) return cachedBlenderPath;

  if (process.env.SPATIAL_BLENDER) {
    if (existsSync(process.env.SPATIAL_BLENDER)) {
      cachedBlenderPath = process.env.SPATIAL_BLENDER;
      return cachedBlenderPath;
    }
  }

  // Probe `blender` on PATH.
  const onPath = await new Promise((resolve) => {
    const cmd = os.platform() === "win32" ? "where" : "which";
    const p = spawn(cmd, ["blender"], { shell: false });
    let out = "";
    p.stdout.on("data", (d) => (out += d));
    p.on("close", (code) => {
      if (code === 0) {
        const first = out.split(/\r?\n/).find((l) => l.trim());
        resolve(first ? first.trim() : null);
      } else resolve(null);
    });
    p.on("error", () => resolve(null));
  });
  if (onPath) {
    cachedBlenderPath = onPath;
    return cachedBlenderPath;
  }

  if (os.platform() === "win32") {
    for (const guess of WINDOWS_GUESSES) {
      if (existsSync(guess)) {
        cachedBlenderPath = guess;
        return cachedBlenderPath;
      }
    }
  }

  cachedBlenderPath = null;
  return null;
}

function runBlender(blenderPath, scriptPath, scriptArgs) {
  return new Promise((resolve) => {
    const args = [
      "--background",
      "--factory-startup",
      "--python", scriptPath,
      "--",
      ...scriptArgs,
    ];
    const child = spawn(blenderPath, args, { shell: false });

    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (d) => (stdout += d));
    child.stderr.on("data", (d) => (stderr += d));
    child.on("close", (code) => resolve({ code, stdout, stderr }));
    child.on("error", (err) =>
      resolve({ code: -1, stdout, stderr: String(err) }),
    );
  });
}

export async function processOne({
  blenderPath,
  scriptPath,
  inputPath,
  outputDir,
  assetId,
}) {
  await fs.mkdir(outputDir, { recursive: true });
  const { code, stdout, stderr } = await runBlender(blenderPath, scriptPath, [
    inputPath, outputDir, assetId,
  ]);

  const analysisFile = path.join(outputDir, `${assetId}.analysis.json`);
  let analysis = null;
  try {
    analysis = JSON.parse(await fs.readFile(analysisFile, "utf-8"));
  } catch {
    analysis = {
      assetId,
      sourcePath: inputPath,
      status: "failed",
      reason: "Blender exited without writing analysis.json",
      blenderExitCode: code,
      blenderStderrTail: stderr.split(/\r?\n/).slice(-20).join("\n"),
    };
  }
  analysis.blenderExitCode = code;
  return analysis;
}

// Used when Blender is not available, for formats the browser can load directly.
const PASS_THROUGH = new Set([".glb", ".gltf", ".obj", ".stl"]);

export function isPassThrough(ext) {
  return PASS_THROUGH.has(ext.toLowerCase());
}

export async function copyPassThrough({
  inputPath,
  outputDir,
  assetId,
}) {
  await fs.mkdir(outputDir, { recursive: true });
  const ext = path.extname(inputPath).toLowerCase();
  const dest = path.join(outputDir, `${assetId}${ext}`);
  await fs.copyFile(inputPath, dest);
  return {
    assetId,
    sourcePath: inputPath,
    extension: ext,
    importer: "passthrough_copy",
    status: "ok",
    processedPath: dest,
    metrics: null,
    note: "Blender not available — file copied as-is for browser loading.",
  };
}
