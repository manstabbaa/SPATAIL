// Tiny static file server for the local Spatial viewer.
// Serves the entire project root so the viewer can request:
//   /viewer/...          (HTML, JS, CSS)
//   /scene_contracts/SpatialSceneContract.json
//   /assets_processed/*.glb
//   /assets_raw/...      (fallback for pass-through formats)
//
// Zero dependencies — uses node:http + node:fs.

import http from "node:http";
import { promises as fs, createReadStream } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { planFromPrompt } from "../pipeline/spatail/prompt_planner.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = path.resolve(__dirname, "..");
const PORT = Number(process.env.SPATIAL_PORT || 5173);

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "application/javascript; charset=utf-8",
  ".mjs": "application/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".glb": "model/gltf-binary",
  ".gltf": "model/gltf+json",
  ".obj": "text/plain; charset=utf-8",
  ".stl": "application/sla",
  ".bin": "application/octet-stream",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".svg": "image/svg+xml",
  ".ico": "image/x-icon",
};

function safeJoin(root, urlPath) {
  // Strip query string, decode, normalize. Refuse paths that escape root.
  const cleaned = decodeURIComponent(urlPath.split("?")[0]).replace(/\\/g, "/");
  const joined = path.normalize(path.join(root, cleaned));
  const rel = path.relative(root, joined);
  if (rel.startsWith("..") || path.isAbsolute(rel)) return null;
  return joined;
}

async function send(res, statusCode, headers, body) {
  res.writeHead(statusCode, headers);
  if (body && typeof body.pipe === "function") {
    body.pipe(res);
  } else {
    res.end(body);
  }
}

async function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => resolve(Buffer.concat(chunks).toString("utf-8")));
    req.on("error", reject);
  });
}

async function handleInquire(req, res) {
  // POST /api/inquire { prompt, contractPath } -> { contract, focusElementId, reason }
  let payload;
  try {
    const body = await readBody(req);
    payload = body ? JSON.parse(body) : {};
  } catch {
    return send(res, 400, { "content-type": "application/json" },
      JSON.stringify({ error: "invalid JSON body" }));
  }
  const prompt = (payload.prompt || "").toString();
  const contractPath = (payload.contractPath || "").toString();
  if (!prompt || !contractPath) {
    return send(res, 400, { "content-type": "application/json" },
      JSON.stringify({ error: "prompt and contractPath required" }));
  }
  const full = safeJoin(PROJECT_ROOT, contractPath);
  if (!full) {
    return send(res, 403, { "content-type": "application/json" },
      JSON.stringify({ error: "forbidden contractPath" }));
  }
  let contract;
  try {
    contract = JSON.parse(await fs.readFile(full, "utf-8"));
  } catch (e) {
    return send(res, 404, { "content-type": "application/json" },
      JSON.stringify({ error: `contract not loadable: ${e.message}` }));
  }
  const constraints = Array.isArray(payload.constraints) ? payload.constraints : [];
  const result = planFromPrompt(prompt, { contract, constraints });
  const out = JSON.stringify({
    contract: result.contract,
    focusElementId: result.focusElementId,
    matchedCategories: result.matchedCategories || [],
    matchedCallouts: result.matchedCallouts || [],
    reason: result.reason,
  });
  send(res, 200, {
    "content-type": "application/json",
    "cache-control": "no-cache",
    "access-control-allow-origin": "*",
  }, out);
}

const server = http.createServer(async (req, res) => {
  try {
    let urlPath = req.url || "/";

    // Tiny API surface. Today: /api/inquire. Tomorrow: /api/constraints,
    // /api/rooms, /api/animations/preview. Keep it small and synchronous;
    // a real backend is a future move.
    if (req.method === "POST" && urlPath.startsWith("/api/inquire")) {
      return handleInquire(req, res);
    }

    if (urlPath === "/") urlPath = "/viewer/spatail.html";
    if (urlPath.endsWith("/")) urlPath += "index.html";

    const full = safeJoin(PROJECT_ROOT, urlPath);
    if (!full) {
      return send(res, 403, { "content-type": "text/plain" }, "Forbidden");
    }

    let stat;
    try {
      stat = await fs.stat(full);
    } catch {
      return send(res, 404, { "content-type": "text/plain" },
        `Not found: ${urlPath}`);
    }
    if (stat.isDirectory()) {
      return send(res, 404, { "content-type": "text/plain" },
        "Directory listing disabled");
    }

    const ext = path.extname(full).toLowerCase();
    const headers = {
      "content-type": MIME[ext] || "application/octet-stream",
      "content-length": stat.size,
      "cache-control": "no-cache",
      "access-control-allow-origin": "*",
    };
    send(res, 200, headers, createReadStream(full));
  } catch (e) {
    send(res, 500, { "content-type": "text/plain" }, `Server error: ${e.message}`);
  }
});

server.listen(PORT, () => {
  console.log(`[spatial-viewer] http://localhost:${PORT}/`);
  console.log(`[spatial-viewer] serving from ${PROJECT_ROOT}`);
});
