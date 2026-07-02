// EngineExplainer — main entry.
// Wires the viewer (3D), the contract player (orchestrator), the prompt bar,
// and the asset switcher together. Keep this file thin — actual work lives
// in viewer.js, contract_player.js, and api.js.

const __cb = new URLSearchParams(location.search).get("cb") || "";
const __q = __cb ? `?cb=${__cb}` : "";
const { Viewer } = await import(`./viewer.js${__q}`);
const { ContractPlayer } = await import(`./contract_player.js${__q}`);
const { askForContract, loadLocalContract } = await import(`./api.js${__q}`);

// --- DOM refs ---
const canvas         = document.getElementById("three-canvas");
const overlay        = document.getElementById("overlay-root");
const subtitle       = document.getElementById("subtitle");
const transport      = document.getElementById("transport");
const transportFill  = document.getElementById("transport-fill");
const transportLabel = document.getElementById("transport-label");
const transportPrev  = document.getElementById("transport-prev");
const transportNext  = document.getElementById("transport-next");
const promptForm     = document.getElementById("prompt-form");
const promptInput    = document.getElementById("prompt-input");
const promptSubmit   = document.getElementById("prompt-submit");
const switcher       = document.getElementById("asset-switcher");
const statusPill     = document.getElementById("status-pill");
const statusLabel    = document.getElementById("status-label");

// --- Asset registry ---
// Each asset declares: id, label, GLB url, prompt placeholder, camera-preset
// scale hint (the viewer's CAMERA_PRESETS are tuned for the V8; small assets
// like the 60mm fan need different distances).
const ASSETS = {
  fan: {
    id: "fan",
    label: "Fan",
    glbUrl: "../engine/fan.glb",
    placeholder: "Ask anything about this fan…",
    // Fan is ~60mm wide, ~10mm deep. Pull the camera in tight.
    cameraOverride: {
      hero_threequarter: { from: [0.10, 0.06, 0.10], to: [0.0, 0.0, 0.0], fov: 32 },
      hero_front:        { from: [0.00, 0.03, 0.14], to: [0.0, 0.0, 0.0], fov: 32 },
      topdown:           { from: [0.00, 0.14, 0.001], to: [0.0, 0.0, 0.0], fov: 32 },
      section_side:      { from: [0.14, 0.03, 0.00], to: [0.0, 0.0, 0.0], fov: 32 },
      cylinder_close:    { from: [0.05, 0.04, 0.07], to: [0.0, 0.0, 0.0], fov: 30 },
    },
  },
  engine: {
    id: "engine",
    label: "Engine",
    glbUrl: "../engine/v8_engine.glb",
    placeholder: "Ask anything about this engine…",
    cameraOverride: null,   // use default presets (engine bbox ~75cm)
  },
  // Generated from a flat-pack manual (no curated model existed). ~0.9m tall
  // in the GLB (Y-up, meters), so it needs its own camera distances.
  shelving: {
    id: "shelving",
    label: "Shelving",
    glbUrl: "../engine/shelving_unit.glb",
    regionsUrl: "../engine/shelving_unit_regions.json",
    placeholder: "Ask anything about this shelving unit…",
    cameraOverride: {
      hero_threequarter: { from: [1.0, 0.85, 1.25], to: [0.0, 0.45, 0.0], fov: 38 },
      hero_front:        { from: [0.0, 0.55, 1.55], to: [0.0, 0.45, 0.0], fov: 38 },
      topdown:           { from: [0.0, 1.7, 0.02],  to: [0.0, 0.45, 0.0], fov: 38 },
      section_side:      { from: [1.6, 0.55, 0.0],  to: [0.0, 0.45, 0.0], fov: 38 },
      cylinder_close:    { from: [0.6, 0.55, 0.85], to: [0.0, 0.45, 0.0], fov: 34 },
    },
  },
};

// Asset currently loaded (initialised below from URL or default).
// Default is FAN — the simplest asset, used as the introductory demo. The
// engine is available as the second tab while the system grows toward a
// universal explainer.
let currentAssetId = (new URLSearchParams(location.search).get("asset") || "fan").toLowerCase();
if (!ASSETS[currentAssetId]) currentAssetId = "fan";

// --- boot the 3D viewer ---
const viewer = new Viewer(canvas);
const player = new ContractPlayer({
  viewer,
  overlay,
  subtitle,
  onProgress: (beatIndex, beatCount, pct, beat) => {
    transport.classList.remove("hidden");
    transportFill.style.width = `${pct * 100}%`;
    transportLabel.textContent = `${beatIndex + 1} / ${beatCount}  ${beat?.id ?? ""}`;
    setStatus("playing", `BEAT ${beatIndex + 1}/${beatCount}`);
  },
  onFinish: () => {
    setTimeout(() => subtitle.classList.add("hidden"), 1500);
    setStatus("ready", "READY");
  },
});
window.engineexplainer = { viewer, player, ASSETS, currentAssetId };

// --- Status pill helpers ---
function setStatus(mode, label) {
  statusPill.classList.remove("is-thinking", "is-playing");
  if (mode === "thinking") statusPill.classList.add("is-thinking");
  if (mode === "playing")  statusPill.classList.add("is-playing");
  statusLabel.textContent = label;
}
setStatus("ready", "LOADING");

// --- Asset loading ---
async function loadAsset(assetId) {
  const asset = ASSETS[assetId];
  if (!asset) return;
  currentAssetId = assetId;
  window.engineexplainer.currentAssetId = assetId;

  // Update switcher UI
  for (const btn of switcher.querySelectorAll(".seg")) {
    const active = btn.dataset.asset === assetId;
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-selected", active ? "true" : "false");
  }
  promptInput.placeholder = asset.placeholder;

  // Reset any in-flight playback
  player._abortToken += 1;
  player._clearOverlay?.();
  subtitle.classList.add("hidden");
  transport.classList.add("hidden");

  // Hot-swap the GLB
  canvas.classList.add("is-loading");
  setStatus("thinking", "LOADING…");
  try {
    await viewer.loadAsset(asset.glbUrl, { cameraOverride: asset.cameraOverride });
    // Optional sub-mesh regions sidecar (regions.json). Overlay meshes baked
    // into the GLB are addressed by id via highlight_region.
    if (asset.regionsUrl) {
      try {
        const rresp = await fetch(asset.regionsUrl);
        if (rresp.ok) viewer.setRegions(await rresp.json());
      } catch (e) { console.warn("[main] regions load failed", e); }
    }
    canvas.classList.remove("is-loading");
    setStatus("ready", "READY");
    // Update the URL so the asset is shareable / refresh-stable
    const url = new URL(location.href);
    url.searchParams.set("asset", assetId);
    history.replaceState(null, "", url.toString());
  } catch (err) {
    console.warn("[main] asset load failed; placeholder", err);
    viewer.installPlaceholder?.();
    canvas.classList.remove("is-loading");
    setStatus("ready", "ASSET MISSING");
  }
}

// --- Wire the asset switcher ---
switcher.addEventListener("click", async (e) => {
  const btn = e.target.closest(".seg");
  if (!btn) return;
  const assetId = btn.dataset.asset;
  if (!assetId || assetId === currentAssetId) return;
  // Reset prompt + clear last contract (matches the user's chosen swap behavior)
  promptInput.value = "";
  await loadAsset(assetId);
});

// --- Transport buttons ---
transportPrev.addEventListener("click", () => player.seekBeat(-1, { relative: true }));
transportNext.addEventListener("click", () => player.seekBeat(+1, { relative: true }));

// --- Prompt flow ---
promptForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = promptInput.value.trim();
  if (!q) return;
  promptSubmit.disabled = true;
  setStatus("thinking", "THINKING");
  try {
    const contract = await askForContract(q);
    // Bake-bridge integration: if the orchestrator baked new clips into the
    // asset's GLB (contract.meta.glb_version was bumped), we need to reload
    // the GLB before playing so the new clip names actually resolve in the
    // viewer's mixer. The asset is the same — just the file content changed.
    const newGlbVer = contract.meta?.glb_version;
    if (newGlbVer && newGlbVer !== window.__lastGlbVer) {
      const baked = contract.meta?.baked_clips || [];
      setStatus("thinking", `BAKED ${baked.length} CLIP${baked.length === 1 ? "" : "S"}`);
      const asset = ASSETS[currentAssetId];
      // Append a cache-buster query to force a fresh fetch
      const url = asset.glbUrl + (asset.glbUrl.includes("?") ? "&" : "?") + "v=" + newGlbVer;
      await viewer.loadAsset(url, { cameraOverride: asset.cameraOverride });
      window.__lastGlbVer = newGlbVer;
    }
    setStatus("playing", "PLAYING");
    await player.play(contract);
  } catch (err) {
    console.error("[engineexplainer] contract fetch failed", err);
    subtitle.textContent = "Something went wrong reaching the intelligence layer.";
    subtitle.classList.remove("hidden");
    setStatus("ready", "ERROR");
  } finally {
    promptSubmit.disabled = false;
  }
});

// =====================================================================
// usermanualXR — drop a manual → matched model → step walkthrough
// =====================================================================
const manualOpen   = document.getElementById("manual-open");
const manualDrawer = document.getElementById("manual-drawer");
const manualClose  = document.getElementById("manual-close");
const manualText   = document.getElementById("manual-text");
const manualSample = document.getElementById("manual-sample");
const manualBuild  = document.getElementById("manual-build");
const manualStatus = document.getElementById("manual-status");
const manualUpload = document.getElementById("manual-upload");
const manualFile   = document.getElementById("manual-file");
const stepRail     = document.getElementById("step-rail");

const PDFJS_VERSION = "4.4.168";

// Pull plain text out of an uploaded manual file. .txt/.md read directly;
// .pdf is parsed client-side with pdf.js (lazy-loaded, no build step) so the
// stdlib intelligence server never has to handle binary uploads.
async function extractManualText(file) {
  const isPdf = file.type === "application/pdf" || /\.pdf$/i.test(file.name);
  if (!isPdf) return (await file.text()).trim();
  const pdfjs = await import("pdfjs");
  pdfjs.GlobalWorkerOptions.workerSrc =
    `https://unpkg.com/pdfjs-dist@${PDFJS_VERSION}/build/pdf.worker.min.mjs`;
  const pdf = await pdfjs.getDocument({ data: await file.arrayBuffer() }).promise;
  const pages = [];
  for (let p = 1; p <= pdf.numPages; p++) {
    const page = await pdf.getPage(p);
    const content = await page.getTextContent();
    pages.push(content.items.map((it) => it.str).join(" "));
  }
  return pages.join("\n\n").trim();
}

async function loadManualFile(file) {
  if (!file) return;
  manualStatus.textContent = `Reading ${file.name}…`;
  try {
    const text = await extractManualText(file);
    if (!text) {
      manualStatus.textContent =
        `No selectable text found in ${file.name}. If it's a scanned PDF, paste the text instead.`;
      return;
    }
    manualText.value = text;
    manualStatus.textContent =
      `Loaded ${file.name} · ${text.length.toLocaleString()} chars. Click "Build walkthrough".`;
  } catch (err) {
    console.error("[usermanualXR] file read failed", err);
    manualStatus.textContent =
      `Couldn't read ${file.name}. For PDFs with no text layer, paste the text instead.`;
  }
}

const FAN_SAMPLE_MANUAL = `AXICOOL 60 — AXIAL COOLING FAN  ·  Model AC-60X · 60x60x10mm · 12V DC

KNOW YOUR FAN
The square FRAME holds the fan in place. The ROTOR is the spinning hub with
seven curved BLADES that move the air.

STEP 1 — IDENTIFY THE PARTS
The frame protects the moving parts; the rotor and its seven blades move the air.

STEP 2 — MOUNT THE FRAME
Align the four corner holes and insert an M3 screw into each corner. Do not overtighten.

STEP 3 — CONNECT POWER
Plug the 2-pin lead into a 12V DC header. The motor inside the hub drives the rotor.

STEP 4 — POWER ON AND SPIN UP
Apply power. The hub turns, carrying all seven blades as a single rigid assembly,
and reaches full speed within one second.

STEP 5 — VERIFY AIRFLOW
A steady stream of air flows through the frame. The faster the blades spin, the
more air is moved per second.

STEP 6 — MAINTENANCE
Power down and let the rotor stop completely, then gently clear dust from the blades.`;

function openManual()  { manualDrawer.classList.remove("hidden"); manualText.focus(); }
function closeManual() { manualDrawer.classList.add("hidden"); }
// Manual → XR is its own mini-app: the FAB routes there rather than opening
// the legacy in-page drawer (which only matched curated models). The mini-app
// SEGMENTS + BUILDS the product part-by-part. (openManual stays for the sample/
// close buttons that remain in the DOM, but the FAB no longer uses it.)
manualOpen?.addEventListener("click", () => {
  const cb = new URLSearchParams(location.search).get("cb");
  location.href = "manual.html" + (cb ? `?cb=${cb}` : "");
});
manualClose?.addEventListener("click", closeManual);
manualDrawer?.addEventListener("click", (e) => { if (e.target === manualDrawer) closeManual(); });
manualSample?.addEventListener("click", () => { manualText.value = FAN_SAMPLE_MANUAL; });

manualUpload?.addEventListener("click", () => manualFile?.click());
manualFile?.addEventListener("change", (e) => {
  loadManualFile(e.target.files?.[0]);
  e.target.value = "";  // let the same file be re-selected
});

// Drag-and-drop a manual file onto the drawer card.
const manualCard = manualDrawer?.querySelector(".manual-card");
function setDrag(on) { manualCard?.classList.toggle("dragover", on); }
manualCard?.addEventListener("dragover", (e) => { e.preventDefault(); setDrag(true); });
manualCard?.addEventListener("dragleave", (e) => {
  if (e.target === manualCard || !manualCard.contains(e.relatedTarget)) setDrag(false);
});
manualCard?.addEventListener("drop", (e) => {
  e.preventDefault(); setDrag(false);
  loadManualFile(e.dataTransfer?.files?.[0]);
});

function renderStepRail(stepsIndex, contract) {
  if (!stepsIndex || !stepsIndex.length) { stepRail.classList.add("hidden"); return; }
  stepRail.innerHTML = `<div class="rail-head">Walkthrough · ${stepsIndex.length} steps</div>` +
    stepsIndex.map((s, i) =>
      `<div class="step" data-beat="${i}"><span class="n">${String(s.n ?? i + 1).padStart(2, "0")}</span>` +
      `<span class="t">${(s.title || "Step").replace(/</g, "&lt;")}</span></div>`).join("");
  stepRail.classList.remove("hidden");
  // Click a step → scrub the player to that beat
  stepRail.querySelectorAll(".step").forEach((el) => {
    el.addEventListener("click", () => {
      const idx = parseInt(el.dataset.beat, 10);
      player.scrubToBeat?.(idx, contract);
      highlightStep(idx);
    });
  });
}
function highlightStep(idx) {
  stepRail.querySelectorAll(".step").forEach((el, i) =>
    el.classList.toggle("active", i === idx));
}

manualBuild?.addEventListener("click", async () => {
  const text = manualText.value.trim();
  if (!text) { manualStatus.textContent = "Paste manual text first."; return; }
  manualBuild.disabled = true;
  manualStatus.textContent = "Reading manual · classifying product…";
  setStatus("thinking", "INGESTING");
  try {
    const resp = await fetch("http://localhost:5175/api/manual", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ manual_text: text }),
    });
    const result = await resp.json();
    if (!resp.ok || !result.ok) {
      manualStatus.textContent = result.error || result.match?.reason || "Could not build a walkthrough.";
      setStatus("ready", "NO MATCH");
      manualBuild.disabled = false;
      return;
    }
    const { asset_id, contract, match } = result;
    manualStatus.textContent = `Matched → ${match.kind}. Loading model…`;
    // Swap the asset model if the matched asset differs from what's loaded
    if (asset_id !== currentAssetId) {
      await loadAsset(asset_id);
    } else if (contract.meta?.glb_version && contract.meta.glb_version !== window.__lastGlbVer) {
      const asset = ASSETS[asset_id];
      const url = asset.glbUrl + (asset.glbUrl.includes("?") ? "&" : "?") + "v=" + contract.meta.glb_version;
      await viewer.loadAsset(url, { cameraOverride: asset.cameraOverride });
      window.__lastGlbVer = contract.meta.glb_version;
    }
    closeManual();
    renderStepRail(contract.steps_index, contract);
    window.__contract = contract;
    setStatus("playing", "WALKTHROUGH");
    await player.play(contract);
    setStatus("ready", "READY");
  } catch (err) {
    console.error("[usermanualXR] build failed", err);
    manualStatus.textContent = "Something went wrong reaching the intelligence layer.";
    setStatus("ready", "ERROR");
  } finally {
    manualBuild.disabled = false;
  }
});

// --- Boot ---
loadAsset(currentAssetId);
