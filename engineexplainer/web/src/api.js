// api.js — thin client that fetches contracts from either the intelligence
// backend (when present) or local example files (for offline dev).

// The intelligence backend is a Python service (intelligence/orchestrator.py).
// Until that exists, we fall back to the bundled example contracts.

// Intelligence backend lives on a separate port (CORS-enabled). Override in dev
// by setting `window.ENGINEEXPLAINER_API` before main.js loads.
const INTELLIGENCE_ENDPOINT = (typeof window !== "undefined" && window.ENGINEEXPLAINER_API)
  || "http://127.0.0.1:5175/api/ask";

/** POST a prompt to the intelligence service and return the resulting contract.
 * `asset_id` tells the orchestrator which asset is currently loaded so the
 * generated contract references the right meshes + animations. Defaults to
 * the value on window.engineexplainer.currentAssetId (set by the switcher). */
export async function askForContract(prompt, opts = {}) {
  const asset_id =
    opts.asset_id ||
    (typeof window !== "undefined" && window.engineexplainer?.currentAssetId) ||
    "engine";
  try {
    const res = await fetch(INTELLIGENCE_ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt, asset_id }),
    });
    if (res.ok) return await res.json();
    console.warn(`[api] backend returned ${res.status}; falling back to example contracts`);
  } catch (err) {
    console.info("[api] no intelligence backend reachable; using example contracts");
  }
  return await pickClosestExample(prompt);
}

/** Load a contract JSON from a known path (used during development). */
export async function loadLocalContract(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`Failed to load ${path}: ${res.status}`);
  return await res.json();
}

// -----------------------------------------------------------------------------
// Local example dispatch
// -----------------------------------------------------------------------------

const EXAMPLES = [
  { path: "../contracts/examples/how-does-a-piston-work.json", keywords: ["piston", "stroke", "combustion", "how does", "power"] },
  // Add more examples here as they're authored.
];

async function pickClosestExample(prompt) {
  const p = prompt.toLowerCase();
  let best = { score: -1, entry: EXAMPLES[0] };
  for (const e of EXAMPLES) {
    const score = e.keywords.reduce((s, k) => s + (p.includes(k) ? 1 : 0), 0);
    if (score > best.score) best = { score, entry: e };
  }
  return await loadLocalContract(best.entry.path);
}
