// Manual → XR — standalone mini-app entry.
//
// This is the dedicated home for the generative manual→XR path: drop a manual,
// an agent SEGMENTS it into parts + steps, the product is BUILT part-by-part in
// headless Blender, registered as a fresh asset, and played as an interactive
// assembly walkthrough. Unlike the explainer (index.html), this never matches a
// curated model — it loads the GENERATED GLB (contract.meta.asset) and drives
// the runtime tween assembly (contract.scene.assembly).
//
// Reuses viewer.js (3D) + contract_player.js (beat orchestrator) unchanged.

const __cb = new URLSearchParams(location.search).get("cb") || "";
const __q = __cb ? `?cb=${__cb}` : "";
const { Viewer }         = await import(`./viewer.js${__q}`);
const { ContractPlayer } = await import(`./contract_player.js${__q}`);

const API = "http://localhost:5175/api/manual";
const PDFJS_VERSION = "4.4.168";

// --- DOM refs ---
const canvas         = document.getElementById("three-canvas");
const overlay        = document.getElementById("overlay-root");
const subtitle       = document.getElementById("subtitle");
const transport      = document.getElementById("transport");
const transportFill  = document.getElementById("transport-fill");
const transportLabel = document.getElementById("transport-label");
const transportPrev  = document.getElementById("transport-prev");
const transportNext  = document.getElementById("transport-next");
const stepRail       = document.getElementById("step-rail");
const statusPill     = document.getElementById("status-pill");
const statusLabel    = document.getElementById("status-label");

const intake     = document.getElementById("mx-intake");
const intakeCard  = document.getElementById("mx-card");
const manualText  = document.getElementById("manual-text");
const manualFile  = document.getElementById("manual-file");
const manualUpload = document.getElementById("manual-upload");
const manualSample = document.getElementById("manual-sample");
const manualBuild  = document.getElementById("manual-build");
const logEl        = document.getElementById("mx-log");
const newBtn       = document.getElementById("mx-new");

// --- Walkthrough state ---
let activeContract = null;
let beatIndex = 0;
let beatCount = 0;

// --- 3D viewer + beat player ---
const viewer = new Viewer(canvas);
const player = new ContractPlayer({
  viewer, overlay, subtitle,
  onProgress: (idx, count, pct, beat) => {
    beatIndex = idx; beatCount = count;
    transport.classList.remove("hidden");
    transportFill.style.width = `${pct * 100}%`;
    transportLabel.textContent = `${idx + 1} / ${count}`;
    highlightStep(idx);
    setStatus("playing", `STEP ${idx + 1}/${count}`);
  },
  onFinish: () => {
    setTimeout(() => subtitle.classList.add("hidden"), 1500);
    setStatus("ready", "DONE");
  },
});
window.manualXR = { viewer, player, get contract() { return activeContract; } };

// --- Status pill ---
function setStatus(mode, label) {
  statusPill.classList.remove("is-thinking", "is-playing");
  if (mode === "thinking") statusPill.classList.add("is-thinking");
  if (mode === "playing")  statusPill.classList.add("is-playing");
  statusLabel.textContent = label;
}
setStatus("ready", "READY");

// --- Intake log ---
function log(msg, cls = "") {
  const line = document.createElement("div");
  if (cls) line.className = cls;
  line.innerHTML = msg;
  logEl.appendChild(line);
  logEl.scrollTop = logEl.scrollHeight;
}
function logBusy(msg) { log(`<span class="mx-spin"></span>${msg}`, "step"); }
function clearLog() { logEl.innerHTML = ""; }

// --- Manual file → text (txt/md direct, pdf via pdf.js, client-side) ---
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
  clearLog();
  logBusy(`Reading <b>${file.name}</b>…`);
  try {
    const text = await extractManualText(file);
    if (!text) {
      clearLog();
      log(`No selectable text in <b>${file.name}</b>. If it's a scanned PDF, paste the text instead.`, "err");
      return;
    }
    manualText.value = text;
    clearLog();
    log(`Loaded <b>${file.name}</b> · ${text.length.toLocaleString()} chars. Click <b>Build walkthrough</b>.`, "ok");
  } catch (err) {
    console.error("[manualXR] file read failed", err);
    clearLog();
    log(`Couldn't read <b>${file.name}</b>. For PDFs with no text layer, paste the text instead.`, "err");
  }
}

// --- Build: segment → build part-by-part → register → stage → play ---
async function buildWalkthrough() {
  const text = manualText.value.trim();
  if (!text) { clearLog(); log("Paste manual text or drop a file first.", "err"); return; }

  manualBuild.disabled = true;
  setStatus("thinking", "SEGMENTING");
  clearLog();
  logBusy("Segmenting the manual into parts &amp; steps…");
  logBusy("Building the product <b>part-by-part</b> in Blender…");

  try {
    const resp = await fetch(API, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ manual_text: text, mode: "generate" }),
    });
    const result = await resp.json();
    if (!resp.ok || !result.ok) {
      clearLog();
      log(result.error || result.detail || "Could not build a walkthrough.", "err");
      if (result.detail) log(String(result.detail), "err");
      setStatus("ready", "FAILED");
      manualBuild.disabled = false;
      return;
    }

    const contract = result.contract;
    const br = result.build_result || {};
    activeContract = contract;

    clearLog();
    log(`Segmented → <b>${result.match?.kind || contract.meta?.asset_id}</b>`, "ok");
    log(`Built <b>${br.n_parts ?? "?"}</b> parts in ${br.elapsed_s ?? "?"}s → ` +
        `<b>${contract.meta?.asset_id}</b> (${contract.beats?.length ?? 0} steps)`, "ok");
    logBusy("Loading the generated model…");

    // Load the GENERATED GLB directly (never a curated asset). Cache-bust by
    // version because the file is regenerated under the same name each build.
    const glb = contract.meta.asset;
    const url = glb + (glb.includes("?") ? "&" : "?") + "v=" + Date.now();
    await viewer.loadAsset(url, { cameraOverride: contract.meta.camera_presets || {} });

    renderStepRail(contract);
    revealScene();
    setStatus("playing", "WALKTHROUGH");
    await player.play(contract);
  } catch (err) {
    console.error("[manualXR] build failed", err);
    clearLog();
    log("Couldn't reach the intelligence layer (is the 5175 server running?).", "err");
    setStatus("ready", "ERROR");
  } finally {
    manualBuild.disabled = false;
  }
}

// --- Step rail ---
function renderStepRail(contract) {
  const idx = contract.steps_index || [];
  if (!idx.length) { stepRail.classList.add("hidden"); return; }
  stepRail.innerHTML =
    `<div class="rail-head">Walkthrough · ${idx.length} steps</div>` +
    idx.map((s, i) =>
      `<div class="step" data-beat="${i}">` +
      `<span class="n">${String(s.n ?? i + 1).padStart(2, "0")}</span>` +
      `<span class="t">${(s.title || "Step").replace(/</g, "&lt;")}</span></div>`).join("");
  stepRail.classList.remove("hidden");
  stepRail.querySelectorAll(".step").forEach((el) => {
    el.addEventListener("click", () => {
      const i = parseInt(el.dataset.beat, 10);
      player.goToBeat?.(i);
      highlightStep(i);
    });
  });
}
function highlightStep(idx) {
  stepRail.querySelectorAll(".step").forEach((el, i) =>
    el.classList.toggle("active", i === idx));
}

// --- View transitions ---
function revealScene() {
  intake.classList.add("hidden");
  newBtn.classList.remove("hidden");
}
function showIntake() {
  player.stop?.();
  intake.classList.remove("hidden");
  newBtn.classList.add("hidden");
  stepRail.classList.add("hidden");
  transport.classList.add("hidden");
  subtitle.classList.add("hidden");
  setStatus("ready", "READY");
}

// --- Transport prev / next ---
transportPrev?.addEventListener("click", () => {
  if (!activeContract) return;
  player.goToBeat?.(beatIndex - 1);   // clamps internally; animates the move
});
transportNext?.addEventListener("click", () => {
  if (!activeContract) return;
  player.goToBeat?.(beatIndex + 1);   // clamps internally; animates the move
});

// --- Intake wiring ---
manualBuild?.addEventListener("click", buildWalkthrough);
manualUpload?.addEventListener("click", () => manualFile?.click());
manualFile?.addEventListener("change", (e) => {
  loadManualFile(e.target.files?.[0]);
  e.target.value = "";
});
manualSample?.addEventListener("click", () => { manualText.value = KALLAX_SAMPLE; clearLog(); });
newBtn?.addEventListener("click", showIntake);

// Drag-and-drop onto the intake card.
function setDrag(on) { intakeCard?.classList.toggle("dragover", on); }
intakeCard?.addEventListener("dragover", (e) => { e.preventDefault(); setDrag(true); });
intakeCard?.addEventListener("dragleave", (e) => {
  if (e.target === intakeCard || !intakeCard.contains(e.relatedTarget)) setDrag(false);
});
intakeCard?.addEventListener("drop", (e) => {
  e.preventDefault(); setDrag(false);
  loadManualFile(e.dataTransfer?.files?.[0]);
});

// Sample manual — contains "KALLAX", which the segment agent's fixture matches
// (fast, deterministic, no LLM/API key needed). Any manual mentioning a known
// product hits its fixture; everything else falls back to the LLM segmenter.
const KALLAX_SAMPLE = `IKEA KALLAX 1×4 — OPEN SHELVING UNIT
Article 002.758.14 · 42 × 147 cm · birch effect

KNOW YOUR PARTS
Two tall SIDE PANELS stand the unit up. A BOTTOM panel and a TOP panel cap the
ends. Three fixed SHELVES divide the interior into four open compartments. The
unit is held together with cam-lock fittings and wooden dowels — there is no
back panel.

STEP 1 — LAY OUT THE PANELS
Lay both side panels, the top, the bottom and the three shelves on a soft surface.

STEP 2 — INSERT THE DOWELS
Press wooden dowels into the bottom panel, then stand the left side panel onto it.

STEP 3 — ADD THE SHELVES
Seat the three fixed shelves into the cam-lock holes of the standing side panel.

STEP 4 — FIT THE SECOND SIDE
Lower the right side panel onto the protruding dowels and shelf ends.

STEP 5 — LOCK THE CAM-LOCKS
Turn every cam-lock a half-turn clockwise to draw the joints tight.

STEP 6 — ATTACH TOP AND BOTTOM
Fit the top panel across the two sides and lock it down.

STEP 7 — INSERT COVER CAPS
Press the white cover caps over the exposed cam-lock heads.

STEP 8 — SECURE TO THE WALL
Fix the supplied wall anchor to prevent the unit from tipping.`;
