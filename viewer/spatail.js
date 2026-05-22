// SPATAIL viewer — renders a SpatialExperienceContract as a navigable
// preview room. The room metaphor (walls / table / floor / user
// position) is the v0.1 stand-in for the Vision Pro runtime, and the
// element renderers map 1:1 to the renderers the visionOS player will
// implement next.
//
// One renderer per representationMode is the rule. Adding a new mode
// means adding both a renderer here and one in the visionOS player.

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";

import { AnimationPlayer } from "/viewer/animations/AnimationPlayer.js";
import { InteractionRouter } from "/viewer/animations/InteractionRouter.js";
import { getMechanism, inferMechanismKind } from "/viewer/mechanisms/registry.js";
import { applyFidelity } from "/viewer/fidelity.js";
import { attachDirectManipulation } from "/viewer/direct_manipulation.js";
import { renderAirflowField } from "/viewer/airflow.js";
import { SequenceController } from "/viewer/animations/SequenceController.js";
import { explodeHandler } from "/viewer/animations/handlers/explode.js";
import { assembleHandler } from "/viewer/animations/handlers/assemble.js";
import { highlightPulseHandler } from "/viewer/animations/handlers/highlight_pulse.js";
import { fadeHandler } from "/viewer/animations/handlers/fade.js";
import { setVisibleHandler } from "/viewer/animations/handlers/set_visible.js";
import { attentionCameraHintHandler } from "/viewer/animations/handlers/attention_camera_hint.js";
import { transformKeyframesHandler } from "/viewer/animations/handlers/transform_keyframes.js";
import { cameraPathHandler } from "/viewer/animations/handlers/camera_path.js";
import { applyBakedTrackHandler } from "/viewer/animations/handlers/apply_baked_track.js";
import { crossSectionMechanic } from "/viewer/mechanics/handlers/cross_section.js";
import { flowDiagramMechanic } from "/viewer/mechanics/handlers/flow_diagram.js";
import { loopHandler } from "/viewer/animations/handlers/loop.js";

const gltfLoader = new GLTFLoader();

// --------------------------------------------------------------------------
// State
// --------------------------------------------------------------------------

const state = {
  index: null,
  contract: null,
  scene: null,
  camera: null,
  renderer: null,
  controls: null,
  elementMeshes: new Map(),  // elementId -> THREE.Object3D
  relLines: [],              // [{ from, to, type, line }] for relationship-line updates
  showRelLines: true,
  hudEl: null,
  statusEl: null,
  selectedElementId: null,
  raycaster: new THREE.Raycaster(),
  pointer: new THREE.Vector2(),

  // v0.3 animation layer — instantiated once on boot, swapped per contract.
  animPlayer: null,
  interactionRouter: null,
  sequenceCtl: null,
};

// Ordered placement groups for the sidebar — read top-to-bottom as walls
// (far / back) → table (in front) → floor (down) → on / above the target →
// peripheral panels → hand-reach. Anything in the contract that doesn't
// match goes into an "other" group at the bottom.
const PLACEMENT_GROUP_ORDER = [
  { key: "wall",             label: "Wall",              hint: "shared overview, far surface" },
  { key: "table",            label: "Table",             hint: "inspectable 3D / target zone" },
  { key: "floor",            label: "Floor",             hint: "walkable sequences" },
  { key: "object_anchored",  label: "Object-anchored",   hint: "pinned to a target part" },
  { key: "above_target",     label: "Above target",      hint: "exploded views / diagnostics / guides" },
  { key: "left_of_user",     label: "Left of user",      hint: "persistent reference" },
  { key: "right_of_user",    label: "Right of user",     hint: "active instructions / tools" },
  { key: "in_front_of_user", label: "In front of user",  hint: "head-on" },
  { key: "near_user",        label: "Near user",         hint: "hand-reach decisions" },
  { key: "near_presenter",   label: "Near presenter",    hint: "shared review layouts" },
  { key: "room_center",      label: "Room center",       hint: "centred scenery" },
];

// Color per relationship type. Kept in lockstep with the legend in
// spatail.html.
const REL_COLOR = {
  aligned_above:           0x6ea8ff,
  diagnoses:               0xef6868,
  attached_to:             0xf5b942,
  controls_attention_for:  0xb56cff,
  connects:                0x5ddfd6,
  relates_to:              0x8b90a0,
};

// Logical room dimensions match the placement engine (kept in sync by
// reading roomDimensionsMeters from the contract's environmentAssumptions).
let ROOM = { widthX: 6, depthZ: 4, wallY: 1.6, tableHeight: 0.75 };

// --------------------------------------------------------------------------
// Boot
// --------------------------------------------------------------------------

bootstrap().catch((err) => {
  console.error(err);
  setStatus(`fatal: ${err.message}`, "bad");
});

async function bootstrap() {
  state.hudEl = document.getElementById("hud-line");
  state.statusEl = document.getElementById("status");

  setStatus("loading experience index…");
  const idxRes = await fetch("/scene_contracts/_spatail_index.json", { cache: "no-store" });
  if (!idxRes.ok) {
    throw new Error("no _spatail_index.json — run `npm run spatail` first.");
  }
  state.index = await idxRes.json();
  document.getElementById("schema-version").textContent =
    `schema ${state.index.schemaVersion}`;

  populatePicker(state.index.experiences);
  initThree();
  buildRoom();
  initAnimationSystem();
  startRenderLoop();
  attachPicker();
  attachStageInteraction();
  attachRelToggle();
  attachTransport();
  attachPromptForm();
  attachContentTabs();

  // Per-tab constraint store. The prompt form ships these to /api/inquire
  // so the planner can honour user gestures on the next re-plan.
  state.constraints = [];
  attachDirectManipulation({
    renderer: state.renderer,
    camera: state.camera,
    scene: state.scene,
    controls: state.controls,
    // `elementsGroup` is re-built on every contract load, so we pass a
    // getter rather than a fixed reference.
    getElementsGroup: () => elementsGroup,
    store: state.constraints,
  });

  // v0.4 Step 1 — the very first thing the user sees is the room scan.
  // Desktop simulator: fetch the mock RoomContract and draw its surface
  // outlines so the user can verify the "scan" landed before any prompt
  // is sent. iOS later replaces this with a real ARKit scene-reconstruction
  // pass. Same downstream consumer.
  await loadAndDrawRoom("/scene_contracts/rooms/_default_room.json");

  // Load the picker-default experience (or the first one) by default,
  // unless ?id= is present in the URL.
  const url = new URL(window.location.href);
  const explicit = url.searchParams.get("id");
  const flagged = state.index.experiences.find((e) => e.isDefault === true);
  const initialId = explicit
    || flagged?.experienceId
    || state.index.experiences[0]?.experienceId;
  if (initialId) await loadExperience(initialId);
}

// --------------------------------------------------------------------------
// Mock RoomContract loader
// --------------------------------------------------------------------------
//
// On desktop, the "room scan" is just a JSON we fetch. On iOS it'll be
// the ARKit scene-reconstruction output written to Documents/rooms/.
// Both produce the same shape (see scene_contracts/rooms/_default_room.json).
// The viewer draws each surface as a thin outline so the user has visible
// confirmation that the planner is placing against a real room, not a guess.

let roomOutlines = null;

async function loadAndDrawRoom(url) {
  try {
    setStatus("scanning room…");
    const res = await fetch(url, { cache: "no-store" });
    if (!res.ok) throw new Error(`room fetch ${res.status}`);
    const room = await res.json();
    state.room = room;
    drawRoomOutlines(room);
    setStatus(`room: ${room.label || room.roomId}`);
  } catch (err) {
    console.warn("[viewer] no room available:", err.message);
    setStatus("no room — using inferred placement");
  }
}

function drawRoomOutlines(room) {
  // In void mode the user wants a clean canvas. Skip the room wireframe
  // entirely — the iOS client will draw real surfaces over the camera
  // feed; the web client doesn't need a debug box around the hero.
  if (document.body.classList.contains("void")) return;
  if (roomOutlines) state.scene.remove(roomOutlines);
  roomOutlines = new THREE.Group();
  roomOutlines.name = "RoomOutlines";

  // One thin line-loop per surface polygon, tinted by kind. The user reads
  // these as "this is the floor, this is the back wall, this is the table."
  const surfaceColors = {
    floor:   0x1a1d24,
    wall:    0x6b7280,
    ceiling: 0xc7ccd6,
    table:   0x4e8aff,
    seat:    0xb56cff,
    window:  0x57d09b,
    door:    0xf5b942,
  };

  for (const s of room.surfaces || []) {
    if (!Array.isArray(s.polygon) || s.polygon.length < 3) continue;
    const points = s.polygon.map(([x, y, z]) => new THREE.Vector3(x, y, z));
    points.push(points[0].clone());      // close the loop
    const geo = new THREE.BufferGeometry().setFromPoints(points);
    const color = surfaceColors[s.kind] ?? 0x9ca3af;
    const mat = new THREE.LineBasicMaterial({
      color, transparent: true, opacity: s.kind === "table" ? 0.85 : 0.35,
    });
    const line = new THREE.LineLoop(geo, mat);
    line.userData.surfaceId = s.id;
    line.userData.surfaceKind = s.kind;
    roomOutlines.add(line);
  }
  state.scene.add(roomOutlines);
}

// --------------------------------------------------------------------------
// Prompt form
// --------------------------------------------------------------------------
//
// The single visible UI in void mode. For v0.4 the submit handler is a
// stub: it acknowledges the prompt, sets the status pill, and (in a
// follow-up turn) will POST to a planner endpoint that returns a new
// contract whose elements start at fidelity "ghost" and promote as the
// planner gains confidence. Keeping the wire-up here means the form is
// always functional even before the backend exists.

async function requestFocusOn(el) {
  const entry = state.index?.experiences?.find(
    (e) => e.experienceId === state.contract?.experienceId,
  );
  const contractPath = entry?.contractPath;
  if (!contractPath) return;
  setStatus(`focus → ${el.title}`);
  try {
    const res = await fetch("/api/inquire", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        prompt: el.title, contractPath, constraints: state.constraints || [],
      }),
    });
    if (!res.ok) throw new Error(`inquire ${res.status}`);
    const payload = await res.json();
    state.contract = payload.contract;
    renderSpatialElements(payload.contract);
    renderRelationshipLines(payload.contract);
    state.animPlayer?.loadContract(payload.contract);
    state.interactionRouter?.loadContract(payload.contract);
    if (payload.focusElementId) {
      state.interactionRouter?.fire(`tap.${payload.focusElementId}`);
    }
    setStatus(payload.reason || `focused on ${el.title}`);
  } catch (err) {
    console.error(err);
    setStatus(`focus failed: ${err.message}`, "bad");
  }
}

function attachContentTabs() {
  const buttons = [...document.querySelectorAll(".content-tab")];
  if (buttons.length === 0) return;
  const repaint = (activeId) => {
    for (const b of buttons) {
      b.setAttribute("aria-pressed", b.dataset.experience === activeId ? "true" : "false");
    }
  };
  for (const b of buttons) {
    b.addEventListener("click", async () => {
      const id = b.dataset.experience;
      if (!id) return;
      repaint(id);
      try {
        await loadExperience(id);
      } catch (err) {
        console.error(err);
        setStatus(`load failed: ${err.message}`, "bad");
      }
    });
  }
  // Initial state reflects whichever experience auto-loads on boot.
  const obs = new MutationObserver(() => {
    const eid = state.contract?.experienceId;
    if (eid) repaint(eid);
  });
  obs.observe(document.getElementById("status"), { childList: true, characterData: true, subtree: true });
}

function attachPromptForm() {
  const form = document.getElementById("prompt-form");
  if (!form) return;
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const input = document.getElementById("prompt-input");
    const text = (input?.value || "").trim();
    if (!text) return;

    // Resolve the active contract path from the index so the server knows
    // which contract to re-plan against.
    const entry = state.index?.experiences?.find(
      (e) => e.experienceId === state.contract?.experienceId,
    );
    const contractPath = entry?.contractPath;
    if (!contractPath) {
      setStatus("no active contract to inquire against", "warn");
      return;
    }

    setStatus(`asking… "${text}"`);
    try {
      const res = await fetch("/api/inquire", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          prompt: text, contractPath,
          // Constraints emitted by direct-manipulation gestures the user
          // made since the last re-plan. The server respects them when
          // building the new contract.
          constraints: state.constraints || [],
        }),
      });
      if (!res.ok) throw new Error(`inquire ${res.status}`);
      const payload = await res.json();
      const newContract = payload.contract;
      state.contract = newContract;

      // Re-render with the new fidelity / attention assignments.
      renderSpatialElements(newContract);
      renderRelationshipLines(newContract);
      state.animPlayer?.loadContract(newContract);
      state.interactionRouter?.loadContract(newContract);

      // If the re-plan picked a focus element, fire its tap interaction so
      // the matching mechanism explodes — object-side, no camera move.
      if (payload.focusElementId) {
        state.interactionRouter?.fire(`tap.${payload.focusElementId}`);
      }
      setStatus(payload.reason || "ready");
      input.value = "";
    } catch (err) {
      console.error(err);
      setStatus(`inquire failed: ${err.message}`, "bad");
    }
  });
}

// --------------------------------------------------------------------------
// Sidebar / picker
// --------------------------------------------------------------------------

function populatePicker(experiences) {
  const sel = document.getElementById("experience-picker");
  sel.innerHTML = "";
  for (const e of experiences) {
    const opt = document.createElement("option");
    opt.value = e.experienceId;
    opt.textContent = `${e.title}  ·  ${e.detectedDomain}  ·  ${e.elementCount} elements`;
    sel.appendChild(opt);
  }
}

function attachPicker() {
  document.getElementById("experience-picker").addEventListener("change", (e) => {
    loadExperience(e.target.value).catch((err) => {
      console.error(err);
      setStatus(`load failed: ${err.message}`, "bad");
    });
  });
}

async function loadExperience(experienceId) {
  const entry = state.index.experiences.find((x) => x.experienceId === experienceId);
  if (!entry) throw new Error(`unknown experienceId ${experienceId}`);

  setStatus(`loading ${entry.title}…`);
  document.getElementById("experience-picker").value = experienceId;

  const res = await fetch("/" + entry.contractPath, { cache: "no-store" });
  if (!res.ok) throw new Error(`could not load ${entry.contractPath}`);
  const contract = await res.json();
  state.contract = contract;

  const link = document.getElementById("contract-link");
  link.href = "/" + entry.contractPath;

  // Pull room dimensions from the contract so the viewer matches the
  // placement engine even if the planner changes them later.
  const rd = contract.environmentAssumptions?.roomDimensionsMeters;
  if (rd?.widthX) {
    ROOM = { ...ROOM, ...rd };
    rebuildRoom();
  }

  paintSource(contract);
  paintAttention(contract);
  paintSummary(contract);
  paintElementList(contract);
  paintElementsByPlacement(contract);
  paintInteractionPlan(contract);

  renderSpatialElements(contract);
  renderRelationshipLines(contract);
  applyMechanics(contract);
  paintExplanation(contract);
  resetReasoningPanel();

  // Hand the new contract to the animation stack. Player + Router are
  // pure registries; SequenceController will repaint the transport bar
  // via its onChange callback. If the contract has no defaultSequenceId
  // the transport stays hidden.
  state.animPlayer?.loadContract(contract);
  state.interactionRouter?.loadContract(contract);
  state.sequenceCtl?.loadContract(contract);

  setStatus("ready");
  state.hudEl.textContent =
    `${contract.spatialElements.length} elements · domain "${contract.detectedDomain.name}"`;
}

function paintSource(contract) {
  document.getElementById("prompt-text").textContent = contract.sourcePrompt;
  const dl = document.getElementById("source-meta");
  dl.innerHTML = "";
  const rows = [
    ["title",       contract.title],
    ["domain",      `${contract.detectedDomain.name} (${contract.detectedDomain.confidence})`],
    ["inputs",      `${contract.sourceInputs.length} source(s)`],
    ["environment", contract.environmentAssumptions?.kind || "—"],
  ];
  for (const [k, v] of rows) {
    const dt = document.createElement("dt"); dt.textContent = k;
    const dd = document.createElement("dd"); dd.textContent = v;
    dl.appendChild(dt); dl.appendChild(dd);
  }
}

function paintAttention(contract) {
  const ol = document.getElementById("attention-list");
  ol.innerHTML = "";
  for (const step of contract.attentionPlan || []) {
    const li = document.createElement("li");
    li.dataset.elementId = step.focusElementId;
    li.innerHTML = `<strong>${escapeHtml(step.narration)}</strong><small>${escapeHtml(step.focusElementId)}</small>`;
    li.addEventListener("click", () => selectElement(step.focusElementId));
    ol.appendChild(li);
  }
}

function paintSummary(contract) {
  document.getElementById("summary-text").textContent =
    contract.reasoningSummary || "—";
}

function paintElementList(contract) {
  const ul = document.getElementById("element-list");
  ul.innerHTML = "";
  document.getElementById("element-count").textContent =
    contract.spatialElements.length;

  for (const el of contract.spatialElements) {
    const li = document.createElement("li");
    li.dataset.elementId = el.id;

    const titleRow = document.createElement("div");
    titleRow.className = "elem-title";
    titleRow.textContent = el.title;

    const row2 = document.createElement("div");
    row2.className = "elem-row2";
    row2.appendChild(tag(el.representationMode, "tag mode"));
    row2.appendChild(tag(el.placement?.kind || "—", "tag placement"));
    row2.appendChild(tag(el.attentionBehavior || "—", "tag attention"));

    li.appendChild(titleRow);
    li.appendChild(row2);
    li.addEventListener("click", () => selectElement(el.id));
    ul.appendChild(li);
  }
}

function paintElementsByPlacement(contract) {
  const root = document.getElementById("placement-groups");
  if (!root) return;
  root.innerHTML = "";

  // Bucket elements by placement.kind, preserving the contract's element order.
  const buckets = new Map();
  for (const el of contract.spatialElements) {
    const kind = el.placement?.kind || "other";
    if (!buckets.has(kind)) buckets.set(kind, []);
    buckets.get(kind).push(el);
  }

  // Emit known groups in the documented reading order, then anything left
  // (unknown placements) under "other".
  const seen = new Set();
  for (const g of PLACEMENT_GROUP_ORDER) {
    const items = buckets.get(g.key);
    if (!items?.length) continue;
    root.appendChild(renderPlacementGroup(g.label, g.hint, g.key, items));
    seen.add(g.key);
  }
  for (const [kind, items] of buckets) {
    if (seen.has(kind)) continue;
    root.appendChild(renderPlacementGroup(prettyPlacement(kind), "", kind, items));
  }
}

function renderPlacementGroup(label, hint, key, items) {
  const wrap = document.createElement("div");
  wrap.className = "placement-group";
  wrap.dataset.placementKind = key;

  const header = document.createElement("div");
  header.className = "placement-header";
  const name = document.createElement("div");
  name.className = "name";
  name.innerHTML = `${escapeHtml(label)}${hint ? ` <small>· ${escapeHtml(hint)}</small>` : ""}`;
  const count = document.createElement("span");
  count.className = "count";
  count.textContent = items.length;
  header.appendChild(name);
  header.appendChild(count);
  wrap.appendChild(header);

  const ul = document.createElement("ul");
  for (const el of items) {
    const li = document.createElement("li");
    li.dataset.elementId = el.id;
    const title = document.createElement("span");
    title.textContent = el.title;
    const chip = document.createElement("span");
    chip.className = "mode-chip";
    chip.textContent = el.representationMode;
    li.appendChild(title);
    li.appendChild(chip);
    li.addEventListener("click", () => selectElement(el.id));
    ul.appendChild(li);
  }
  wrap.appendChild(ul);
  return wrap;
}

function prettyPlacement(kind) {
  return String(kind).replace(/_/g, " ");
}

function paintInteractionPlan(contract) {
  const ul = document.getElementById("interaction-plan-list");
  if (!ul) return;
  ul.innerHTML = "";
  const scene = (contract.interactionPlan?.interactions || [])
    .filter((i) => !i.elementId);
  for (const i of scene) {
    const li = document.createElement("li");
    li.innerHTML =
      `<code>${escapeHtml(i.type)}</code> ` +
      `<small>${escapeHtml(i.behavior)}</small>`;
    ul.appendChild(li);
  }
  if (scene.length === 0) {
    const li = document.createElement("li");
    li.className = "hint";
    li.textContent = "no scene-wide interactions defined";
    ul.appendChild(li);
  }
}

function tag(text, cls) {
  const s = document.createElement("span");
  s.className = cls;
  s.textContent = text;
  return s;
}

function selectElement(elementId) {
  state.selectedElementId = elementId;
  const el = state.contract.spatialElements.find((e) => e.id === elementId);
  if (!el) return;

  // Discovery-marker tap path: if the user tapped a callout that's
  // currently hidden (on_demand), enter focus mode for it by synthesising
  // an inquire prompt from the callout's title. The server returns a
  // contract with that callout promoted to active_focus and everything
  // else stripped. Camera does not move.
  if (el.attentionBehavior === "on_demand"
      && el.representationMode === "anchored_callout") {
    requestFocusOn(el);
    return;
  }

  // Sidebar highlight — flat list, grouped list, attention plan.
  for (const li of document.querySelectorAll("#element-list li, .placement-group li")) {
    li.classList.toggle("active", li.dataset.elementId === elementId);
  }
  for (const li of document.querySelectorAll("#attention-list li")) {
    li.classList.toggle("active-step", li.dataset.elementId === elementId);
  }

  // Reasoning panel.
  document.getElementById("reasoning-empty").hidden = true;
  document.getElementById("reasoning-body").hidden = false;
  document.getElementById("reasoning-title").textContent = el.title;
  document.getElementById("rep-mode").textContent      = el.representationMode;
  document.getElementById("rep-placement").textContent = el.placement?.kind || "—";
  document.getElementById("rep-anchor").textContent    = el.anchorStrategy || "—";
  document.getElementById("rep-scale").textContent     = el.scaleMode || "—";
  document.getElementById("rep-attention").textContent = el.attentionBehavior || "—";
  document.getElementById("rep-priority").textContent  = String(el.priority ?? "—");
  document.getElementById("why-rep").textContent       = el.whyThisRepresentation;
  document.getElementById("why-place").textContent     = el.whyThisPlacement;

  const intUl = document.getElementById("interactions-list");
  intUl.innerHTML = "";
  const ints = el.interactions || [];
  if (ints.length === 0) {
    const li = document.createElement("li");
    li.className = "hint";
    li.textContent = "no element-specific interactions";
    intUl.appendChild(li);
  } else {
    for (const i of ints) {
      const li = document.createElement("li");
      li.innerHTML =
        `<code>${escapeHtml(i.type)}</code> ` +
        `<small>${escapeHtml(i.behavior)}</small>`;
      intUl.appendChild(li);
    }
  }

  const rels = (state.contract.relationships || []).filter(
    (r) => r.from === elementId || r.to === elementId,
  );
  const relBlock = document.getElementById("related-block");
  const relList = document.getElementById("related-list");
  relList.innerHTML = "";
  if (rels.length === 0) {
    relBlock.hidden = true;
  } else {
    relBlock.hidden = false;
    const byId = new Map(state.contract.spatialElements.map((e) => [e.id, e]));
    for (const r of rels) {
      const otherId = r.from === elementId ? r.to : r.from;
      const direction = r.from === elementId ? "→" : "←";
      const other = byId.get(otherId);
      const li = document.createElement("li");
      li.innerHTML =
        `${direction} <strong>${escapeHtml(r.type)}</strong> ` +
        `<a href="#" data-eid="${otherId}">${escapeHtml(other?.title || otherId)}</a> ` +
        `<small>— ${escapeHtml(r.note || "")}</small>`;
      li.querySelector("a").addEventListener("click", (ev) => {
        ev.preventDefault();
        selectElement(otherId);
      });
      relList.appendChild(li);
    }
  }

  flashElementInRoom(elementId);

  // Fire the contract's `tap.<elementId>` interaction so the planner-
  // declared object animations (pulse, explode-in, …) play wherever the
  // selection came from — stage tap, sidebar click, or attention-plan
  // step. The camera does not move; every action acts on the element.
  state.interactionRouter?.fire(`tap.${elementId}`);
}

function resetReasoningPanel() {
  state.selectedElementId = null;
  document.getElementById("reasoning-empty").hidden = false;
  document.getElementById("reasoning-body").hidden = true;
  for (const li of document.querySelectorAll(
    "#element-list li, .placement-group li, #attention-list li",
  )) {
    li.classList.remove("active", "active-step");
  }
}

// --------------------------------------------------------------------------
// Three.js setup
// --------------------------------------------------------------------------

function initThree() {
  const wrap = document.getElementById("canvas-wrap");
  const { clientWidth: w, clientHeight: h } = wrap;

  state.scene = new THREE.Scene();
  state.scene.background = null;

  state.camera = new THREE.PerspectiveCamera(35, w / h, 0.01, 100);
  // Closer / lower pose so the hero geometry on the table fills the void
  // at load. The user takes over from here; the contract never moves
  // the camera again.
  state.camera.position.set(1.6, 1.35, 1.9);

  state.renderer = new THREE.WebGLRenderer({
    antialias: true, alpha: true,
    powerPreference: "high-performance",
  });
  state.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  state.renderer.setSize(w, h);
  state.renderer.toneMapping = THREE.ACESFilmicToneMapping;
  state.renderer.toneMappingExposure = 1.05;
  state.renderer.outputColorSpace = THREE.SRGBColorSpace;
  state.renderer.shadowMap.enabled = true;
  state.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  wrap.appendChild(state.renderer.domElement);

  state.controls = new OrbitControls(state.camera, state.renderer.domElement);
  state.controls.enableDamping = true;
  state.controls.dampingFactor = 0.08;
  // Target the table surface where the hero geometry lands. Initial
  // pose only — the user owns the camera from here.
  state.controls.target.set(0, 0.85, 0);

  // HDRI-style environment lighting — RoomEnvironment is a Three.js built-in
  // procedural studio that gives PBR materials something to reflect. Combined
  // with the directional key light, metallic + clear-coat surfaces read like
  // photographs of real objects instead of flat plastic.
  const pmrem = new THREE.PMREMGenerator(state.renderer);
  state.scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
  state.scene.environmentIntensity = 0.55;

  // Subtle hemi for the floor-up fill so shadowed sides aren't pitch black.
  const hemi = new THREE.HemisphereLight(0xc6dfff, 0x252836, 0.18);
  state.scene.add(hemi);

  // Key light: warm-white, casts soft shadows onto the table + floor.
  const key = new THREE.DirectionalLight(0xffeedd, 1.8);
  key.position.set(3.5, 5.5, 3.5);
  key.castShadow = true;
  // 1024 keeps the shadow map cost low enough for the preview compositor
  // and is plenty for a single object on a table.
  key.shadow.mapSize.set(1024, 1024);
  key.shadow.camera.left = -2.5;
  key.shadow.camera.right = 2.5;
  key.shadow.camera.top = 2.5;
  key.shadow.camera.bottom = -2.5;
  key.shadow.camera.near = 0.5;
  key.shadow.camera.far = 18;
  key.shadow.bias = -0.0002;
  key.shadow.normalBias = 0.02;
  state.scene.add(key);

  // Cool fill from the opposite side — keeps shadows readable, hints at sky.
  const fill = new THREE.DirectionalLight(0x88a8ff, 0.35);
  fill.position.set(-4, 3, -2);
  state.scene.add(fill);

  window.addEventListener("resize", onResize);
}

function onResize() {
  const wrap = document.getElementById("canvas-wrap");
  const w = wrap.clientWidth, h = wrap.clientHeight;
  state.camera.aspect = w / h;
  state.camera.updateProjectionMatrix();
  state.renderer.setSize(w, h);
}

function startRenderLoop() {
  const clock = new THREE.Clock();
  const renderOnce = () => {
    const dt = clock.getDelta();
    const t = clock.getElapsedTime();
    state.controls.update();
    // v0.3 — drive active animation tweens.
    state.animPlayer?.tick(t, dt);
    // v0.5 — tick any mechanic visuals that draw on a clock (flow_diagram tokens).
    tickMechanics(t);
    for (const [eid, mesh] of state.elementMeshes) {
      const isSel = eid === state.selectedElementId;
      mesh.userData.highlight?.update?.(t, isSel);
      // Per-element animation tick (airflow streamers etc.).
      mesh.userData.tick?.(t);
    }
    for (const r of state.relLines) {
      const involved = state.selectedElementId &&
        (state.selectedElementId === r.from || state.selectedElementId === r.to);
      r.line.material.opacity = involved ? 1.0 : 0.45;
      r.line.material.color.setHex(r.color);
      r.line.material.needsUpdate = true;
    }
    state.renderer.render(state.scene, state.camera);
  };
  // Escape hatch: when the browser tab is hidden, requestAnimationFrame
  // halts and any in-process screenshot tool times out waiting for a fresh
  // frame. Expose a manual one-shot render so callers can force a frame
  // even from a backgrounded tab.
  window.__spatail__ = {
    state,
    renderOnce,
    captureSnapshot() {
      renderOnce();
      return state.renderer.domElement.toDataURL("image/png");
    },
  };
  const tick = () => {
    renderOnce();
    requestAnimationFrame(tick);
  };
  tick();
}

// --------------------------------------------------------------------------
// Animation system bootstrap (v0.3)
// --------------------------------------------------------------------------

function initAnimationSystem() {
  // Player: handlers map primitive names to tween factories. Adding a new
  // primitive = new handler import at the top + one register() call.
  const player = new AnimationPlayer({
    resolveEntity: (id) => state.elementMeshes.get(id) || null,
  });
  player.register(explodeHandler);
  player.register(assembleHandler);
  player.register(highlightPulseHandler);
  player.register(fadeHandler);
  player.register(setVisibleHandler);
  player.register(attentionCameraHintHandler);
  player.register(transformKeyframesHandler);
  player.register(cameraPathHandler);
  player.register(applyBakedTrackHandler);
  player.register(loopHandler);
  player.setContext({
    scene: state.scene,
    camera: state.camera,
    controls: state.controls,
    getEntity: (id) => state.elementMeshes.get(id) || null,
  });

  const router = new InteractionRouter();
  const sequence = new SequenceController({
    player,
    getEntity: (id) => state.elementMeshes.get(id) || null,
    onChange: paintTransport,
  });
  router.setHosts({ player, sequence });

  state.animPlayer = player;
  state.interactionRouter = router;
  state.sequenceCtl = sequence;
}

function attachTransport() {
  document.getElementById("t-play")?.addEventListener("click", async () => {
    if (!state.sequenceCtl) return;
    if (state.sequenceCtl.isPlaying() && !state.sequenceCtl.isWaiting()) {
      state.sequenceCtl.pause();
    } else {
      await state.sequenceCtl.play();
    }
  });
  document.getElementById("t-next")?.addEventListener("click", () => {
    state.interactionRouter?.fire("tap.next");
  });
  document.getElementById("t-prev")?.addEventListener("click", () => {
    state.interactionRouter?.fire("tap.previous");
  });
  document.getElementById("t-restart")?.addEventListener("click", () => {
    state.sequenceCtl?.restart();
  });
}

function paintTransport(s) {
  const wrap = document.getElementById("transport");
  if (!wrap) return;
  wrap.hidden = !s.hasSequence;
  if (!s.hasSequence) return;
  const playBtn = document.getElementById("t-play");
  if (playBtn) playBtn.textContent = s.running && !s.waiting ? "⏸" : "▶";
  const label = document.getElementById("t-label");
  if (label) {
    const stepLabel = s.step?.label || "—";
    const num = s.stepIndex >= 0 ? `${s.stepIndex + 1}/${s.stepCount}` : `0/${s.stepCount}`;
    label.classList.toggle("is-waiting", s.waiting);
    label.innerHTML = s.waiting
      ? `<span class="t-step-num">${escapeHtml(num)}</span>tap ⏭ to advance — ${escapeHtml(stepLabel)}`
      : `<span class="t-step-num">${escapeHtml(num)}</span>${escapeHtml(stepLabel)}`;
  }
  const fill = document.getElementById("t-progress-fill");
  if (fill) {
    const pct = s.stepCount > 0 ? ((s.stepIndex + 1) / s.stepCount) * 100 : 0;
    fill.style.width = `${Math.max(0, Math.min(100, pct))}%`;
  }
}

// --------------------------------------------------------------------------
// Room geometry — walls / floor / table / user marker
// --------------------------------------------------------------------------

let roomGroup = null;

function buildRoom() {
  rebuildRoom();
}

function rebuildRoom() {
  if (roomGroup) state.scene.remove(roomGroup);
  roomGroup = new THREE.Group();
  roomGroup.name = "Room";

  // VOID MODE — the body.void class strips every sidebar and renders a
  // white background; the dark walls / wood table belong to the older
  // "Contract Studio" mock room. Skip them entirely so the spatial scene
  // floats in the void. Only the (invisible) ground plane stays so
  // shadows have something to land on; otherwise loaded geometry sits
  // in an empty white scene exactly as it would on iOS over a camera feed.
  if (document.body.classList.contains("void")) {
    const shadowPlane = new THREE.Mesh(
      new THREE.PlaneGeometry(ROOM.widthX, ROOM.depthZ),
      new THREE.ShadowMaterial({ opacity: 0.18 }),
    );
    shadowPlane.rotation.x = -Math.PI / 2;
    shadowPlane.position.y = 0;
    shadowPlane.receiveShadow = true;
    roomGroup.add(shadowPlane);
    state.scene.add(roomGroup);
    return;
  }

  const W = ROOM.widthX, D = ROOM.depthZ, H = 3;

  // Floor with grid.
  const floorGeo = new THREE.PlaneGeometry(W, D);
  const floorMat = new THREE.MeshStandardMaterial({
    color: 0x2a3242,
    roughness: 0.95,
    metalness: 0,
  });
  const floor = new THREE.Mesh(floorGeo, floorMat);
  floor.rotation.x = -Math.PI / 2;
  floor.receiveShadow = true;
  roomGroup.add(floor);

  const grid = new THREE.GridHelper(Math.max(W, D), Math.max(W, D), 0x404a5f, 0x1f2532);
  grid.position.y = 0.001;
  grid.material.transparent = true;
  grid.material.opacity = 0.45;
  roomGroup.add(grid);

  // Walls — back / left / right (open front so the camera can see in).
  const wallMat = new THREE.MeshStandardMaterial({
    color: 0x3d465a, roughness: 1.0, metalness: 0, side: THREE.DoubleSide,
  });

  const backWall = new THREE.Mesh(new THREE.PlaneGeometry(W, H), wallMat);
  backWall.position.set(0, H / 2, -D / 2);
  roomGroup.add(backWall);

  const leftWall = new THREE.Mesh(new THREE.PlaneGeometry(D, H), wallMat);
  leftWall.rotation.y = Math.PI / 2;
  leftWall.position.set(-W / 2, H / 2, 0);
  roomGroup.add(leftWall);

  const rightWall = new THREE.Mesh(new THREE.PlaneGeometry(D, H), wallMat);
  rightWall.rotation.y = -Math.PI / 2;
  rightWall.position.set(W / 2, H / 2, 0);
  roomGroup.add(rightWall);

  // Table — anodised-aluminium-look workbench top, casts a clean shadow
  // onto the floor and receives shadows from anything resting on it.
  const tableTop = new THREE.Mesh(
    new THREE.BoxGeometry(1.6, 0.05, 1.0),
    new THREE.MeshStandardMaterial({
      color: 0x3a3a3f, roughness: 0.42, metalness: 0.55,
    }),
  );
  tableTop.position.set(0, ROOM.tableHeight, 0);
  tableTop.castShadow = true;
  tableTop.receiveShadow = true;
  roomGroup.add(tableTop);
  for (const [dx, dz] of [[-0.7, -0.4], [0.7, -0.4], [-0.7, 0.4], [0.7, 0.4]]) {
    const leg = new THREE.Mesh(
      new THREE.BoxGeometry(0.05, ROOM.tableHeight, 0.05),
      new THREE.MeshStandardMaterial({
        color: 0x202125, roughness: 0.6, metalness: 0.7,
      }),
    );
    leg.position.set(dx, ROOM.tableHeight / 2, dz);
    leg.castShadow = true;
    roomGroup.add(leg);
  }

  // User marker — small cone at the implicit user position.
  const userGeo = new THREE.ConeGeometry(0.18, 0.45, 16);
  const userMat = new THREE.MeshStandardMaterial({
    color: 0x6ea8ff, emissive: 0x1f3a6e, emissiveIntensity: 0.35,
    roughness: 0.4, metalness: 0.1,
  });
  const userMesh = new THREE.Mesh(userGeo, userMat);
  userMesh.rotation.x = Math.PI; // tip up
  userMesh.position.set(0, 0.23, 1.7);
  roomGroup.add(userMesh);

  const userRing = new THREE.Mesh(
    new THREE.RingGeometry(0.22, 0.32, 32),
    new THREE.MeshBasicMaterial({ color: 0x6ea8ff, transparent: true, opacity: 0.4, side: THREE.DoubleSide }),
  );
  userRing.rotation.x = -Math.PI / 2;
  userRing.position.set(0, 0.01, 1.7);
  roomGroup.add(userRing);

  state.scene.add(roomGroup);
}

// --------------------------------------------------------------------------
// Element rendering
// --------------------------------------------------------------------------

let elementsGroup = null;

// ---------------------------------------------------------------------------
// v0.5 — explanation mechanics
// ---------------------------------------------------------------------------
//
// Two shipped renderers today: `cross_section` (clips the hero) and
// `flow_diagram` (a flat 2D panel positioned on the stage). The viewer
// reads contract.mechanics[] after renderSpatialElements has filled
// state.elementMeshes, so target lookups by element id work.
//
// Anything not in the registry is logged as "no renderer" and skipped;
// the contract still ships the mechanic data so a sidebar chip can
// show the gap.

const MECHANIC_REGISTRY = new Map();
MECHANIC_REGISTRY.set("cross_section", crossSectionMechanic);
MECHANIC_REGISTRY.set("flow_diagram",  flowDiagramMechanic);

let mechanicsGroup = null;
let mechanicTickables = [];

function applyMechanics(contract) {
  if (mechanicsGroup) state.scene.remove(mechanicsGroup);
  mechanicsGroup = new THREE.Group();
  mechanicsGroup.name = "Mechanics";
  state.scene.add(mechanicsGroup);
  mechanicTickables = [];

  const mechanics = contract.mechanics || [];
  // Stage layout: position flat panels along the +X side of the stage,
  // stepping out so multiple flow_diagrams don't overlap.
  let stageCursor = 0;
  for (const m of mechanics) {
    const handler = MECHANIC_REGISTRY.get(m.kind);
    if (!handler) continue;
    const ctx = {
      renderer: state.renderer,
      scene: state.scene,
      elementsById: new Map(contract.spatialElements.map((e) => [e.id, e])),
      getMeshForElement: (id) => state.elementMeshes.get(id),
    };
    const out = handler.apply({ mechanic: m, ctx });
    if (!out?.visualEntity) continue;
    // Position flat panels on the stage (1.5m in front of user, off to
    // the right). 3D-clip overlays (cross_section ring) keep the
    // handler's authored transform.
    if (out.kind === "flow_diagram") {
      out.visualEntity.position.set(1.4 + stageCursor * 1.7, 1.3, -0.3);
      stageCursor += 1;
    }
    mechanicsGroup.add(out.visualEntity);
    if (typeof out.visualEntity.userData?.tick === "function") {
      mechanicTickables.push(out.visualEntity);
    }
  }
}

function tickMechanics(elapsedSec) {
  for (const obj of mechanicTickables) {
    obj.userData.tick?.(elapsedSec);
  }
}

// Tiny sidebar block that surfaces the orchestrator's written
// explanation + mechanic chips. Hooks an existing #app element if one
// exists, otherwise injects a new panel at the top of #sidebar.
function paintExplanation(contract) {
  const exp = contract.explanation;
  const mechanics = contract.mechanics || [];
  let host = document.getElementById("explanation-panel");
  if (!host) {
    const sidebar = document.getElementById("sidebar");
    if (!sidebar) return;
    host = document.createElement("section");
    host.id = "explanation-panel";
    host.className = "panel";
    sidebar.insertBefore(host, sidebar.firstChild);
  }
  if (!exp && mechanics.length === 0) {
    host.style.display = "none";
    return;
  }
  host.style.display = "";
  const writeRows = exp ? `
    <h2>Explanation</h2>
    <p class="exp-intent" style="margin:4px 0 6px;color:var(--accent);font-size:12px;font-weight:600">
      ${escapeHtml(exp.intentSummary || "")}
    </p>
    <p class="exp-written" style="margin:0 0 8px;color:var(--text-dim);font-size:12px;line-height:1.45">
      ${escapeHtml(exp.written || "")}
    </p>` : "";
  const mechRows = mechanics.length ? `
    <div class="exp-mech-label" style="font-size:11px;color:var(--text-dim);
         text-transform:uppercase;letter-spacing:0.5px;margin:4px 0">
      Mechanics (${mechanics.length})
    </div>
    <ul style="list-style:none;padding:0;margin:0;display:flex;flex-direction:column;gap:6px">
      ${mechanics.map((m) => `
        <li style="font-size:12px;color:var(--text);
                   padding:6px 8px;border:1px solid var(--border);border-radius:6px">
          <div style="display:flex;gap:6px;align-items:baseline">
            <strong style="color:var(--accent)">${escapeHtml(m.kind)}</strong>
            ${m.placeholderRouted ? `<span style="font-size:10px;color:var(--accent-2)">← ${escapeHtml(m.requestedKind)}</span>` : ""}
            ${m.target ? `<span style="font-size:11px;color:var(--text-dim)">on ${escapeHtml(m.target)}</span>` : ""}
          </div>
          <div style="color:var(--text-dim);font-size:11px;margin-top:2px">
            ${escapeHtml(m.why || "")}
          </div>
        </li>`).join("")}
    </ul>` : "";
  host.innerHTML = writeRows + mechRows;
}

function renderSpatialElements(contract) {
  if (elementsGroup) state.scene.remove(elementsGroup);
  elementsGroup = new THREE.Group();
  elementsGroup.name = "SpatialElements";
  state.elementMeshes = new Map();

  for (const el of contract.spatialElements) {
    // Strip mode: `on_demand` means "hide unless the user asks for me".
    // The prompt planner pushes everything except the matched callout
    // (+ hero + contextual airflow / guide lines) to on_demand, so a
    // focused prompt produces a clean scene with leader lines and a
    // localized explode instead of a dump of every panel at once.
    if (el.attentionBehavior === "on_demand") continue;

    // Fidelity gate: ghost / draft don't run the committed renderer at
    // all — they render their own footprint primitives.
    let mesh;
    const f = el.fidelity || "committed";
    if (f === "ghost" || f === "draft") {
      mesh = applyFidelity(null, el);
    } else {
      const built = renderOne(el, contract);
      if (!built) continue;
      mesh = applyFidelity(built, el);
    }
    if (!mesh) continue;

    // Peripheral / ambient elements stay visible as context but fade
    // opacity-wise so the active_focus element reads clearly.
    if (el.attentionBehavior === "peripheral" || el.attentionBehavior === "ambient") {
      applyPeripheralStyling(mesh, 0.32);
    }

    mesh.userData.elementId = el.id;
    mesh.userData.element = el;
    elementsGroup.add(mesh);
    state.elementMeshes.set(el.id, mesh);
  }

  // Focus-mode glue: draw leader lines from the hero's spotlightOnHero
  // points to the active_focus callouts, plus a small glowing dot at
  // each spotlight on the hero. Content-agnostic — works for wheel
  // buttons today, F1 aero parts tomorrow, anything else next.
  drawFocusLeaderLines(contract);

  // Discovery markers: every on_demand callout with a spotlightOnHero
  // gets a small interactive dot at its position on the hero. The hero
  // dominates the scene; the dots show where the affordances live;
  // tapping a dot promotes that callout to active_focus.
  drawDiscoveryMarkers(contract);

  state.scene.add(elementsGroup);
}

function drawDiscoveryMarkers(contract) {
  const hero = contract.spatialElements.find((el) =>
    el.representationMode === "highlighted_target"
    || el.representationMode === "tabletop_model"
    || el.representationMode === "three_d_model"
  );
  if (!hero) return;
  const heroMesh = state.elementMeshes.get(hero.id);
  if (!heroMesh) return;
  const ratio = heroMesh.userData._autoFitRatio || 1.0;

  for (const el of contract.spatialElements) {
    if (el.representationMode !== "anchored_callout") continue;
    if (el.attentionBehavior !== "on_demand") continue;
    const spot = el.sourceContent?.spotlightOnHero;
    if (!Array.isArray(spot) || spot.length < 3) continue;

    // Dot at the spotlight position on the hero (world space). Sized to
    // read at the wheel's auto-fit scale; halo is 2.5× larger to give
    // a clear tap-target.
    const dot = new THREE.Mesh(
      new THREE.SphereGeometry(0.020, 16, 16),
      new THREE.MeshBasicMaterial({
        color: 0x6ea8ff, transparent: true, opacity: 0.95,
        depthTest: false,
      }),
    );
    dot.renderOrder = 20;
    dot.position.set(
      heroMesh.position.x + spot[0] * ratio,
      heroMesh.position.y + spot[1] * ratio,
      heroMesh.position.z + spot[2] * ratio,
    );
    // A pulsing halo signals "tap me" without becoming visual noise.
    const halo = new THREE.Mesh(
      new THREE.SphereGeometry(0.045, 18, 18),
      new THREE.MeshBasicMaterial({
        color: 0x6ea8ff, transparent: true, opacity: 0.25,
        depthWrite: false, depthTest: false,
      }),
    );
    halo.renderOrder = 19;
    halo.position.copy(dot.position);
    halo.userData.tick = (t) => {
      halo.material.opacity = 0.20 + 0.16 * Math.sin(t * 2.4 + spot[0] * 17);
    };
    dot.userData.__discoveryFor = el.id;
    halo.userData.__discoveryFor = el.id;
    // Stamp the elementId so the existing raycaster picks it up and
    // selectElement → router.fire(`tap.${eid}`) runs.
    dot.userData.elementId = el.id;
    dot.userData.element = el;
    halo.userData.elementId = el.id;
    halo.userData.element = el;
    elementsGroup.add(halo);
    elementsGroup.add(dot);
  }
}

// Fade a mesh tree's materials to a reduced opacity. Preserves the
// originals on userData so the next contract load can restore.
function applyPeripheralStyling(root, alpha) {
  root.traverse((obj) => {
    if (!obj.isMesh || !obj.material) return;
    const mats = Array.isArray(obj.material) ? obj.material : [obj.material];
    for (const m of mats) {
      if (!m) continue;
      m.transparent = true;
      m.opacity = alpha;
      m.depthWrite = false;
    }
  });
}

// Find the hero in the contract (highlighted_target / tabletop_model /
// three_d_model). The hero's loaded GLB sits at its placement.position;
// active_focus callouts get a dashed leader line from
// (hero position + spotlightOnHero offset) to (callout position).
function drawFocusLeaderLines(contract) {
  const hero = contract.spatialElements.find((el) =>
    el.representationMode === "highlighted_target"
    || el.representationMode === "tabletop_model"
    || el.representationMode === "three_d_model"
  );
  if (!hero) return;
  const heroMesh = state.elementMeshes.get(hero.id);
  if (!heroMesh) return;
  const heroPos = heroMesh.position;

  for (const el of contract.spatialElements) {
    if (el.attentionBehavior !== "active_focus") continue;
    if (el.representationMode !== "anchored_callout") continue;
    const calloutMesh = state.elementMeshes.get(el.id);
    if (!calloutMesh) continue;
    const spot = el.sourceContent?.spotlightOnHero;
    if (!Array.isArray(spot) || spot.length < 3) continue;

    // The spotlightOnHero is in the hero's LOCAL frame (the GLB's frame
    // before auto-fit). The hero mesh on screen has been auto-scaled by
    // its loadRealAssetInto ratio, captured on userData if available.
    const ratio = heroMesh.userData._autoFitRatio || 1.0;
    const from = new THREE.Vector3(
      heroPos.x + spot[0] * ratio,
      heroPos.y + spot[1] * ratio,
      heroPos.z + spot[2] * ratio,
    );
    const to = calloutMesh.position;

    // Glowing spotlight dot at the spotlight point on the hero.
    const dot = new THREE.Mesh(
      new THREE.SphereGeometry(0.012, 16, 16),
      new THREE.MeshBasicMaterial({ color: 0x6ea8ff, transparent: true, opacity: 0.95 }),
    );
    dot.position.copy(from);
    dot.userData.__focusGlue = true;
    elementsGroup.add(dot);

    // Dashed leader line.
    const geo = new THREE.BufferGeometry().setFromPoints([from, to]);
    const mat = new THREE.LineDashedMaterial({
      color: 0x6ea8ff, dashSize: 0.04, gapSize: 0.025,
      transparent: true, opacity: 0.85,
    });
    const line = new THREE.Line(geo, mat);
    line.computeLineDistances();
    line.userData.__focusGlue = true;
    elementsGroup.add(line);
  }
}

function renderOne(el, contract) {
  switch (el.representationMode) {
    case "two_d_panel":
    case "wall_dashboard":
    case "floating_decision_card":
    case "diagnostic_overlay":
      return renderPanel(el);
    case "anchored_callout":
      return renderCalloutWithMechanism(el);
    case "tabletop_model":
      return renderTabletopModel(el);
    case "three_d_model":
      return renderThreeDModel(el);
    case "highlighted_target":
      return renderHighlightedTarget(el);
    case "exploded_view":
      return renderExplodedView(el);
    case "floor_timeline":
      return renderFloorTimeline(el);
    case "guide_line":
      return renderGuideLine(el);
    case "airflow_field": {
      const group = renderAirflowField(el, {
        resolveHeroMesh: () => {
          const heroEl = contract.spatialElements.find(
            (e) => e.representationMode === "tabletop_model"
                || e.representationMode === "three_d_model"
                || e.representationMode === "highlighted_target",
          );
          return heroEl ? state.elementMeshes.get(heroEl.id) : null;
        },
      });
      const [tx = 0, ty = 0, tz = 0] = el.placement?.position || [];
      group.position.set(tx, ty, tz);
      return group;
    }
    default:
      return renderPanel(el);
  }
}

// ----- Panels ---------------------------------------------------------

function renderPanel(el) {
  const [w = 1.0, h = 0.8] = el.placement?.sizeMeters || [];
  const canvas = makePanelCanvas(el, w, h);
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 4;
  const mat = new THREE.MeshBasicMaterial({ map: tex, transparent: true });
  const geo = new THREE.PlaneGeometry(w, h);
  const mesh = new THREE.Mesh(geo, mat);
  applyPlacement(mesh, el);
  attachHighlight(mesh, mat, { mode: "panel", tex });
  return mesh;
}

/**
 * Anchored-callout renderer with a procedural MECHANISM sub-assembly.
 *
 * The panel sits in-place at the callout's placement; the mechanism
 * (button / rotary / paddle) is built procedurally from the mechanism
 * registry and floats just above the panel. On tap, the contract's
 * `explode` animation fires on this group — the explode handler reads
 * `userData.explodableChildren` (set by the mechanism builder) so the
 * parts spread cleanly along +Y. Camera does not move.
 *
 * Modularity: which mechanism gets built is inferred from the callout's
 * title + body via `inferMechanismKind`. Card authors can force a kind
 * with `sourceContent.mechanismKind`. New mechanism types = new file in
 * /viewer/mechanisms/ + one register() call — nothing in this renderer changes.
 */
function renderCalloutWithMechanism(el) {
  const group = new THREE.Group();
  group.name = `callout.${el.id}`;

  // 1) The flat info-panel — same canvas-textured plane the other panel
  //    modes use. It's the label the user reads while the mechanism is
  //    in its assembled state.
  const [w = 0.35, h = 0.18] = el.placement?.sizeMeters || [];
  const panelCanvas = makePanelCanvas(el, w, h);
  const panelTex = new THREE.CanvasTexture(panelCanvas);
  panelTex.colorSpace = THREE.SRGBColorSpace;
  panelTex.anisotropy = 4;
  const panelMat = new THREE.MeshBasicMaterial({
    map: panelTex, transparent: true,
  });
  const panel = new THREE.Mesh(new THREE.PlaneGeometry(w, h), panelMat);
  panel.name = "callout.panel";
  panel.position.y = h / 2 - 0.005;
  // CRITICAL: the panel is NOT explodable — the explode primitive is for
  // the mechanism parts only, never the label.
  panel.userData.explodable = false;
  group.add(panel);

  // 2) The procedural mechanism. Sits a few cm above the panel so it
  //    reads as the "live cross-section" of what the panel describes.
  const kind = inferMechanismKind(el);
  if (kind) {
    const builder = getMechanism(kind);
    if (builder) {
      const mech = builder({ scale: 2.4 });
      mech.position.y = h + 0.04;
      mech.name = `callout.mechanism.${kind}`;
      group.add(mech);
      // The mechanism's explodable parts are what the explode handler
      // operates on. Re-export them at the group level so the handler
      // doesn't have to walk to find them.
      group.userData.explodableChildren = mech.userData.explodableChildren;
      group.userData.mechanism = kind;
      group.userData.partLabels = mech.userData.partLabels;
    }
  }

  // 3) A small accent ring under the mechanism — visual ground for the
  //    parts so the explode reads against a stable anchor.
  if (kind) {
    const ring = new THREE.Mesh(
      new THREE.RingGeometry(0.045, 0.052, 32),
      new THREE.MeshBasicMaterial({
        color: 0x6ea8ff, transparent: true, opacity: 0.55,
        side: THREE.DoubleSide,
      }),
    );
    ring.name = "callout.anchorRing";
    ring.rotation.x = -Math.PI / 2;
    ring.position.y = h + 0.025;
    ring.userData.explodable = false;
    group.add(ring);
  }

  applyPlacement(group, el);
  attachHighlight(group, panelMat, { mode: "panel", tex: panelTex });
  return group;
}

function makePanelCanvas(el, w, h) {
  // Pixel density: 320 px per metre — readable when zoomed.
  const PX = 320;
  const W = Math.round(w * PX);
  const H = Math.round(h * PX);
  const cv = document.createElement("canvas");
  cv.width = W; cv.height = H;
  const ctx = cv.getContext("2d");

  // Background card.
  const radius = 14;
  ctx.fillStyle = "rgba(20, 24, 32, 0.96)";
  roundRect(ctx, 0, 0, W, H, radius); ctx.fill();
  ctx.strokeStyle = "rgba(110,168,255,0.35)";
  ctx.lineWidth = 2;
  roundRect(ctx, 1, 1, W - 2, H - 2, radius); ctx.stroke();

  // Title bar accent stripe (color depends on representation mode).
  const accent = accentFor(el.representationMode);
  ctx.fillStyle = accent;
  ctx.fillRect(0, 0, 5, H);

  // Header: title + mode badge.
  ctx.fillStyle = "#e6e8ee";
  ctx.font = `600 ${Math.round(H * 0.06)}px ui-sans-serif, system-ui, sans-serif`;
  ctx.textBaseline = "top";
  wrapText(ctx, el.title, 22, 16, W - 40, H * 0.085);

  ctx.fillStyle = "#8b90a0";
  ctx.font = `500 ${Math.round(H * 0.038)}px ui-monospace, Menlo, monospace`;
  ctx.fillText(el.representationMode.toUpperCase(),
    22, 16 + H * 0.085 * 1.05);

  // Content.
  const contentTop = 16 + H * 0.085 * 1.05 + H * 0.06;
  drawPanelContent(ctx, el, 22, contentTop, W - 40, H - contentTop - 14);

  return cv;
}

function drawPanelContent(ctx, el, x, y, w, h) {
  ctx.fillStyle = "#dfe3ee";
  const fontSize = Math.max(14, Math.round(h * 0.07));
  ctx.font = `500 ${fontSize}px ui-sans-serif, system-ui, sans-serif`;
  ctx.textBaseline = "top";

  const sc = el.sourceContent || {};

  // KPI grid (numeric_summary)
  if (Array.isArray(sc.kpis)) {
    const cols = 2;
    const rows = Math.ceil(sc.kpis.length / cols);
    const cellW = w / cols;
    const cellH = Math.min(h / rows, 110);
    for (let i = 0; i < sc.kpis.length; i++) {
      const k = sc.kpis[i];
      const cx = x + (i % cols) * cellW;
      const cy = y + Math.floor(i / cols) * cellH;
      ctx.fillStyle = "#8b90a0";
      ctx.font = `500 ${Math.round(fontSize * 0.85)}px ui-sans-serif, sans-serif`;
      ctx.fillText(k.label, cx, cy);
      ctx.fillStyle = "#e6e8ee";
      ctx.font = `700 ${Math.round(fontSize * 1.35)}px ui-sans-serif, sans-serif`;
      ctx.fillText(k.value, cx, cy + fontSize);
      if (k.delta) {
        ctx.fillStyle = k.trend === "down" ? "#ef6868" : "#f5b942";
        ctx.font = `500 ${Math.round(fontSize * 0.82)}px ui-monospace, monospace`;
        ctx.fillText(k.delta, cx, cy + fontSize * 2.6);
      }
    }
    return;
  }

  // Fact bucket (group of key/value)
  if (Array.isArray(sc.facts)) {
    let yy = y;
    for (const f of sc.facts) {
      ctx.fillStyle = "#8b90a0";
      ctx.font = `500 ${Math.round(fontSize * 0.85)}px ui-sans-serif, sans-serif`;
      ctx.fillText(f.key, x, yy);
      ctx.fillStyle = "#e6e8ee";
      ctx.font = `500 ${fontSize}px ui-sans-serif, sans-serif`;
      yy = wrapText(ctx, f.value, x, yy + fontSize * 0.95, w, fontSize * 1.15);
      yy += fontSize * 0.6;
      if (yy > y + h) break;
    }
    return;
  }

  // List
  if (Array.isArray(sc.items)) {
    let yy = y;
    for (const item of sc.items) {
      ctx.fillStyle = "#6ea8ff";
      ctx.fillText("•", x, yy);
      ctx.fillStyle = "#dfe3ee";
      yy = wrapText(ctx, item, x + fontSize * 1.0, yy, w - fontSize, fontSize * 1.2);
      yy += fontSize * 0.4;
      if (yy > y + h) break;
    }
    return;
  }

  // Steps
  if (Array.isArray(sc.steps)) {
    let yy = y;
    for (let i = 0; i < sc.steps.length; i++) {
      ctx.fillStyle = "#b56cff";
      ctx.font = `700 ${fontSize}px ui-monospace, monospace`;
      ctx.fillText(`${i + 1}.`, x, yy);
      ctx.fillStyle = "#dfe3ee";
      ctx.font = `500 ${fontSize}px ui-sans-serif, sans-serif`;
      yy = wrapText(ctx, sc.steps[i], x + fontSize * 1.6, yy, w - fontSize * 1.6, fontSize * 1.2);
      yy += fontSize * 0.4;
      if (yy > y + h) break;
    }
    return;
  }

  // Decisions options (rendered as floating decision panels)
  if (Array.isArray(sc.options)) {
    let yy = y;
    for (const o of sc.options) {
      ctx.fillStyle = "#6ea8ff";
      ctx.font = `600 ${fontSize}px ui-sans-serif, sans-serif`;
      yy = wrapText(ctx, "› " + (o.label || ""), x, yy, w, fontSize * 1.2);
      if (o.detail) {
        ctx.fillStyle = "#8b90a0";
        ctx.font = `400 ${Math.round(fontSize * 0.85)}px ui-sans-serif, sans-serif`;
        yy = wrapText(ctx, o.detail, x + 8, yy + 4, w - 8, fontSize);
      }
      yy += fontSize * 0.5;
      if (yy > y + h) break;
    }
    return;
  }

  // Diagnostic finding / labeled callout
  if (sc.finding) {
    wrapText(ctx, sc.finding, x, y, w, fontSize * 1.2);
    return;
  }

  // Plain summary / body
  if (sc.body) {
    wrapText(ctx, sc.body, x, y, w, fontSize * 1.2);
    return;
  }

  // Fallback: dump title.
  ctx.fillStyle = "#8b90a0";
  ctx.fillText(el.title, x, y);
}

function accentFor(mode) {
  switch (mode) {
    case "wall_dashboard":         return "#6ea8ff";
    case "two_d_panel":            return "#6ea8ff";
    case "floating_decision_card": return "#b56cff";
    case "anchored_callout":       return "#f5b942";
    case "diagnostic_overlay":     return "#ef6868";
    default:                       return "#6ea8ff";
  }
}

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

function wrapText(ctx, text, x, y, maxWidth, lineHeight) {
  const words = String(text || "").split(/\s+/);
  let line = "";
  let yy = y;
  for (const word of words) {
    const test = line ? line + " " + word : word;
    if (ctx.measureText(test).width > maxWidth && line) {
      ctx.fillText(line, x, yy);
      yy += lineHeight;
      line = word;
    } else {
      line = test;
    }
  }
  if (line) { ctx.fillText(line, x, yy); yy += lineHeight; }
  return yy;
}

// ----- Tabletop / highlighted / exploded ------------------------------

function renderTabletopModel(el) {
  const group = new THREE.Group();
  const [w = 0.8, h = 0.4, d = 0.8] = el.placement?.sizeMeters || [];

  // Build the placeholder first so the scene has something to show
  // immediately. If a normalized GLB is available, swap it in async
  // and remove the placeholder when it lands.
  const placeholder = new THREE.Group();
  const body = new THREE.Mesh(
    new THREE.BoxGeometry(w, h, d),
    new THREE.MeshStandardMaterial({
      color: 0x4a5570, roughness: 0.5, metalness: 0.25,
      emissive: 0x1a2238, emissiveIntensity: 0.25,
    }),
  );
  body.position.y = h / 2;
  placeholder.add(body);

  const components = el.sourceContent?.components || [];
  if (components.length) {
    const stripeH = h * 0.18;
    components.slice(0, 5).forEach((c, i) => {
      const stripe = new THREE.Mesh(
        new THREE.BoxGeometry(w * 1.02, stripeH, d * 1.02),
        new THREE.MeshStandardMaterial({
          color: stripeColor(i),
          roughness: 0.6, metalness: 0.1,
          emissive: stripeColor(i), emissiveIntensity: 0.15,
        }),
      );
      stripe.position.y = stripeH / 2 + i * stripeH * 1.05;
      placeholder.add(stripe);
    });
  }
  group.add(placeholder);

  group.add(makeLabel(el.title, w * 1.2, 0.12, {
    yOffset: h + 0.18,
    color: "#e6e8ee", bg: "rgba(74,85,112,0.85)",
  }));

  applyPlacement(group, el);
  attachHighlight(group, body.material, { mode: "object" });

  loadRealAssetInto(group, el, { fitToBox: [w, h, d], placeholder });
  return group;
}

function stripeColor(i) {
  const palette = [0x6ea8ff, 0xb56cff, 0xf5b942, 0x57d09b, 0xef6868];
  return palette[i % palette.length];
}

function renderHighlightedTarget(el) {
  // SPATAIL "highlighted_target" — the serviced physical part. When a
  // normalized GLB is available (requiredAssets[0].processedAssetPath),
  // we render the real geometry with a subtle accent halo. Otherwise we
  // fall back to the bright blue translucent box used as a placeholder
  // for assets we haven't ingested yet (e.g. the Mustang air filter).
  const group = new THREE.Group();
  const [w = 0.6, h = 0.3, d = 0.4] = el.placement?.sizeMeters || [];
  const hasRealAsset = !!el.requiredAssets?.[0]?.processedAssetPath;

  // The placeholder box ONLY shows when there's no real GLB. With a
  // real asset coming, the placeholder would ghost behind the wheel and
  // muddle the silhouette — so we skip it entirely and let the loaded
  // GLB render alone.
  const placeholderMat = new THREE.MeshStandardMaterial({
    color: 0x6ea8ff,
    emissive: 0x2a5dd0,
    emissiveIntensity: 0.85,
    roughness: 0.25, metalness: 0.4,
    transparent: true, opacity: 0.78,
  });
  let placeholder = null;
  if (!hasRealAsset) {
    placeholder = new THREE.Group();
    const body = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), placeholderMat);
    body.position.y = h / 2;
    placeholder.add(body);
    const edges = new THREE.LineSegments(
      new THREE.EdgesGeometry(body.geometry),
      new THREE.LineBasicMaterial({ color: 0x9cc5ff }),
    );
    edges.position.copy(body.position);
    placeholder.add(edges);
    group.add(placeholder);
  }

  // Halo only when there's NO real asset — with the wheel GLB loaded
  // the silhouette IS the highlight, and a 1.3x box around it just
  // adds noise.
  let halo = null;
  if (!hasRealAsset) {
    halo = new THREE.Mesh(
      new THREE.BoxGeometry(w * 1.3, h * 1.3, d * 1.3),
      new THREE.MeshBasicMaterial({
        color: 0x6ea8ff, transparent: true, opacity: 0.22,
      }),
    );
    halo.position.y = h / 2;
    group.add(halo);
  }

  // Note: the hero deliberately renders WITHOUT a name label. The
  // geometry IS the name. A 1m-wide nameplate hovering over the wheel
  // dominates the canvas and defeats the purpose of the void aesthetic.
  // The status pill names the current experience; that's enough.

  applyPlacement(group, el);
  attachHighlight(group, placeholderMat, { mode: "object", halo });

  // Larger fit-box for hero — the wheel should fill the table, not sit
  // as a thumbnail in the middle of it. fitToBox is what loadRealAssetInto
  // scales the loaded model to fit within.
  loadRealAssetInto(group, el, { fitToBox: [w * 1.6, h * 2.0, d * 1.6], placeholder });
  return group;
}

function renderThreeDModel(el) {
  const group = new THREE.Group();
  const [w = 0.5, h = 0.5, d = 0.5] = el.placement?.sizeMeters || [];
  const mat = new THREE.MeshStandardMaterial({
    color: 0x8a96b0, roughness: 0.4, metalness: 0.25,
  });
  const placeholder = new THREE.Group();
  const body = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat);
  body.position.y = h / 2;
  placeholder.add(body);
  group.add(placeholder);
  group.add(makeLabel(el.title, w * 1.4, 0.10, {
    yOffset: h + 0.12, color: "#e6e8ee", bg: "rgba(74,85,112,0.85)",
  }));
  applyPlacement(group, el);
  attachHighlight(group, mat, { mode: "object" });
  loadRealAssetInto(group, el, { fitToBox: [w, h, d], placeholder });
  return group;
}

// --------------------------------------------------------------------------
// Real GLB loading — when the SPATAIL pipeline normalized the asset, the
// contract carries a project-relative URL on requiredAssets[0].processedAssetPath.
// We load it async, scale it to fit the placement's size, and remove the
// placeholder geometry once it lands. If anything fails, the placeholder
// stays so the scene never goes blank.
// --------------------------------------------------------------------------

function loadRealAssetInto(group, el, { fitToBox, placeholder }) {
  const url = el.requiredAssets?.[0]?.processedAssetPath;
  if (!url) return;
  gltfLoader.load(
    url,
    (gltf) => {
      const model = gltf.scene;
      // Auto-fit: compute the model bbox, then uniformly scale + translate
      // so the model fits inside `fitToBox` (the placeholder's footprint).
      const bbox = new THREE.Box3().setFromObject(model);
      const size = new THREE.Vector3();
      const center = new THREE.Vector3();
      bbox.getSize(size);
      bbox.getCenter(center);
      const [fW = 1, fH = 1, fD = 1] = fitToBox || [];
      const ratio = Math.min(
        fW / (size.x || 1),
        fH / (size.y || 1),
        fD / (size.z || 1),
      ) * 0.95;
      model.scale.setScalar(ratio);
      // Stamp the auto-fit ratio on the parent group so consumers
      // (focus-mode leader lines, mechanisms anchored to the hero) can
      // transform local-space spotlight coords into world space.
      group.userData._autoFitRatio = ratio;
      // Center on XZ, sit it on the placement origin (Y = 0 in local space
      // since the placement engine already pinned us to the table surface).
      model.position.set(
        -center.x * ratio,
        -bbox.min.y * ratio,
        -center.z * ratio,
      );
      // Enable shadows on every mesh so the new key light renders properly,
      // and slightly nudge any flat/over-bright materials toward a more
      // photographic look. We don't override user-authored PBR — just lift
      // metallic surfaces a touch and warm the ambient occlusion intensity.
      model.traverse((obj) => {
        if (obj.isMesh) {
          obj.castShadow = true;
          obj.receiveShadow = true;
          if (obj.material) {
            const mats = Array.isArray(obj.material) ? obj.material : [obj.material];
            for (const m of mats) {
              if (m && typeof m === "object" && "envMapIntensity" in m) {
                m.envMapIntensity = 0.7;
              }
            }
          }
        }
      });
      group.add(model);
      if (placeholder) group.remove(placeholder);

      // Mark the loaded model's top-level segments as explodable so the
      // `explode` animation handler can lift them apart along +Y. We pick
      // children of the deepest single container that holds multiple
      // Mesh/Group nodes — that's typically the segmentation script's
      // output node holding N named parts.
      const explodable = pickExplodableSegments(model);
      if (explodable.length > 1) {
        for (const part of explodable) part.userData.explodable = true;
        group.userData.explodableChildren = explodable;
      }

      // If the authored GLB carries baked animation tracks, hand them to
      // the loop / transform_keyframes handlers via the element root's
      // userData. THREE.AnimationMixer plays directly against `model`.
      if (gltf.animations?.length) {
        const mixer = new THREE.AnimationMixer(model);
        group.userData.mixer = mixer;
        group.userData.animationClips = gltf.animations;
      }

      setStatus(`loaded ${el.title}`, "ok");
    },
    undefined,
    (err) => {
      console.warn(`[viewer] could not load ${url}:`, err);
      setStatus(`asset load failed: ${el.title}`, "warn");
      // Placeholder stays; nothing else to do.
    },
  );
}

/**
 * Walk a freshly-loaded glTF scene and return the most populous "siblings"
 * level — i.e. the children of the deepest single container that holds >= 2
 * Mesh / Group children. That's almost always the segmented assembly root.
 *
 * Returns [] when the model is a single mesh (nothing to explode).
 */
function pickExplodableSegments(model) {
  let best = [];
  model.traverse((obj) => {
    const kids = (obj.children || []).filter((c) => c.isMesh || c.isGroup);
    if (kids.length >= 2 && kids.length > best.length) {
      best = kids;
    }
  });
  return best;
}

function renderGuideLine(el) {
  // Visual connector between two element positions. The planner has
  // already resolved the endpoints into placement.from / placement.to
  // (absolute world positions).
  const group = new THREE.Group();
  const from = el.placement?.from;
  const to = el.placement?.to;
  if (!from || !to) {
    // Fallback: a tiny visible marker so the element doesn't go missing.
    group.add(makeLabel("(unresolved guide line)", 0.4, 0.06, {
      yOffset: 0, color: "#ef6868", bg: "rgba(0,0,0,0.8)",
    }));
    applyPlacement(group, el);
    attachHighlight(group, null, { mode: "guide" });
    return group;
  }
  // IMPORTANT: applyPlacement will set the group's origin to placement.position
  // (the segment midpoint). The line geometry must live in *local* space, so
  // subtract the midpoint from the absolute endpoints.
  const mid = el.placement.position || [
    (from[0] + to[0]) / 2,
    (from[1] + to[1]) / 2,
    (from[2] + to[2]) / 2,
  ];
  const localFrom = new THREE.Vector3(from[0] - mid[0], from[1] - mid[1], from[2] - mid[2]);
  const localTo   = new THREE.Vector3(to[0]   - mid[0], to[1]   - mid[1], to[2]   - mid[2]);
  const mat = new THREE.LineDashedMaterial({
    color: 0x6ea8ff, dashSize: 0.04, gapSize: 0.03,
    transparent: true, opacity: 0.95, linewidth: 2,
  });
  const geo = new THREE.BufferGeometry().setFromPoints([localFrom, localTo]);
  const line = new THREE.Line(geo, mat);
  line.computeLineDistances();
  group.add(line);

  // Small label near the midpoint.
  group.add(makeLabel(el.title, 0.55, 0.06, {
    yOffset: 0.05, color: "#6ea8ff", bg: "rgba(0,0,0,0.7)",
  }));
  applyPlacement(group, el);
  attachHighlight(group, mat, { mode: "guide" });
  return group;
}

function renderExplodedView(el) {
  const group = new THREE.Group();
  const components = el.sourceContent?.components || [
    { name: "Top" }, { name: "Middle" }, { name: "Bottom" },
  ];
  const partH = 0.13;
  const gap = 0.07;
  const explodable = [];
  components.forEach((c, i) => {
    const m = new THREE.Mesh(
      new THREE.BoxGeometry(0.42, partH, 0.32),
      new THREE.MeshStandardMaterial({
        color: stripeColor(i),
        roughness: 0.5, metalness: 0.2,
        emissive: stripeColor(i), emissiveIntensity: 0.15,
      }),
    );
    const y = i * (partH + gap);
    m.position.y = y;
    // Flag this box so the v0.3 explode handler can find it without
    // walking past the per-part labels.
    m.userData.explodable = true;
    explodable.push(m);
    group.add(m);

    // Per-part label tucked to the right.
    group.add(makeLabel(c.name || `Part ${i + 1}`, 0.6, 0.07, {
      yOffset: y, xOffset: 0.55, color: "#e6e8ee", bg: "rgba(0,0,0,0.75)",
    }));
  });
  group.userData.explodableChildren = explodable;

  // Guide line from target up through the assembly (CRITICAL rule:
  // exploded view must obviously align with the target part).
  const lineMat = new THREE.LineDashedMaterial({
    color: 0x6ea8ff, dashSize: 0.04, gapSize: 0.03, transparent: true, opacity: 0.7,
  });
  const lineGeo = new THREE.BufferGeometry().setFromPoints([
    new THREE.Vector3(0, -0.6, 0),
    new THREE.Vector3(0, components.length * (partH + gap), 0),
  ]);
  const line = new THREE.Line(lineGeo, lineMat);
  line.computeLineDistances();
  group.add(line);

  applyPlacement(group, el);
  attachHighlight(group, null, { mode: "explode" });
  return group;
}

function renderFloorTimeline(el) {
  const group = new THREE.Group();
  const sc = el.sourceContent || {};
  const items = sc.events || sc.steps || [];
  const stepCount = items.length || 4;
  const plateW = 0.4, plateH = 0.02, plateD = 0.4, gap = 0.12;
  const totalLen = stepCount * (plateW + gap);

  items.forEach((it, i) => {
    const plate = new THREE.Mesh(
      new THREE.BoxGeometry(plateW, plateH, plateD),
      new THREE.MeshStandardMaterial({
        color: stripeColor(i),
        emissive: stripeColor(i), emissiveIntensity: 0.25,
        roughness: 0.4, metalness: 0.1,
      }),
    );
    plate.position.set(-totalLen / 2 + i * (plateW + gap) + plateW / 2, 0, 0);
    group.add(plate);

    const label = typeof it === "string"
      ? it
      : (it.label || it.when || `Step ${i + 1}`);
    const sub = typeof it === "object" && it.when ? it.when : null;

    group.add(makeLabel(label, 0.55, 0.08, {
      yOffset: 0.05, xOffset: plate.position.x, color: "#e6e8ee",
      bg: "rgba(0,0,0,0.78)", rotateX: -Math.PI / 2,
    }));
    if (sub) {
      group.add(makeLabel(sub, 0.4, 0.05, {
        yOffset: 0.05, xOffset: plate.position.x, zOffset: 0.14,
        color: "#8b90a0", bg: "rgba(0,0,0,0.6)", rotateX: -Math.PI / 2,
      }));
    }
  });

  // Connector line on the floor between plates.
  if (items.length > 1) {
    const conn = new THREE.Mesh(
      new THREE.BoxGeometry(totalLen - plateW, 0.003, 0.04),
      new THREE.MeshBasicMaterial({ color: 0x6ea8ff, transparent: true, opacity: 0.5 }),
    );
    conn.position.y = 0.012;
    group.add(conn);
  }

  applyPlacement(group, el);
  attachHighlight(group, null, { mode: "timeline" });
  return group;
}

// ----- Shared helpers -------------------------------------------------

function applyPlacement(obj3d, el) {
  const [x = 0, y = 0, z = 0] = el.placement?.position || [];
  obj3d.position.set(x, y, z);
  const [rx, ry, rz] = el.placement?.rotationDeg || [0, 0, 0];
  obj3d.rotation.set(
    THREE.MathUtils.degToRad(rx),
    THREE.MathUtils.degToRad(ry),
    THREE.MathUtils.degToRad(rz),
  );
}

function makeLabel(text, w, h, opts = {}) {
  const PX = 320;
  const cv = document.createElement("canvas");
  cv.width = Math.round(w * PX);
  cv.height = Math.round(h * PX);
  const ctx = cv.getContext("2d");
  ctx.fillStyle = opts.bg || "rgba(0,0,0,0.78)";
  roundRect(ctx, 0, 0, cv.width, cv.height, 10); ctx.fill();
  ctx.fillStyle = opts.color || "#e6e8ee";
  ctx.font = `600 ${Math.round(cv.height * 0.5)}px ui-sans-serif, system-ui, sans-serif`;
  ctx.textBaseline = "middle";
  ctx.textAlign = "center";
  // Truncate if needed.
  let display = text;
  while (display.length > 4 && ctx.measureText(display).width > cv.width - 16) {
    display = display.slice(0, -1);
  }
  if (display !== text) display = display.slice(0, -1) + "…";
  ctx.fillText(display, cv.width / 2, cv.height / 2);

  const tex = new THREE.CanvasTexture(cv);
  tex.colorSpace = THREE.SRGBColorSpace;
  const mat = new THREE.MeshBasicMaterial({ map: tex, transparent: true, depthWrite: false });
  const mesh = new THREE.Mesh(new THREE.PlaneGeometry(w, h), mat);
  mesh.position.set(opts.xOffset || 0, opts.yOffset || 0, opts.zOffset || 0);
  if (opts.rotateX != null) mesh.rotation.x = opts.rotateX;
  return mesh;
}

function attachHighlight(obj, primaryMat, opts) {
  // Element-selection highlight. Discrete state, NOT a continuous animation —
  // a constantly-oscillating scale-pulse on a user-facing panel was being
  // read as "the camera is moving" because rotated panels create parallax
  // under any scale change. Now: a single material/halo lift on select,
  // nothing else. The discrete attention pulse triggered by the contract's
  // tap interaction (highlight_pulse animation) is the only ongoing visual.
  const halo = opts.halo;
  obj.userData.highlight = {
    update(_t, isSelected) {
      if (halo) {
        halo.material.opacity = isSelected ? 0.36 : 0.18;
      }
      if (primaryMat?.emissive) {
        const base = primaryMat.userData._baseEmissive
                  ?? primaryMat.emissiveIntensity
                  ?? 0.2;
        primaryMat.userData._baseEmissive = base;
        primaryMat.emissiveIntensity = isSelected ? base + 0.45 : base;
      }
      // No scale animation here. Tap-triggered animations (pulse/explode)
      // run through the AnimationPlayer and act on the object itself.
    },
  };
}

function flashElementInRoom(elementId) {
  // INTENTIONAL: do NOT move the camera. The user owns the camera at
  // all times. Selecting an element draws attention via the in-scene
  // highlight pulse + the sidebar's selected-element styling, never by
  // dollying the viewport. This function exists as a hook in case we
  // later want to add object-side selection FX (e.g. a halo ring) —
  // for now it's a deliberate no-op.
  void elementId;
}

// --------------------------------------------------------------------------
// Relationships — thin dashed segments between related element positions.
//
// Why a separate pass: relationship lines are a *derivative* of placement
// (they connect element world positions), not first-class spatial elements
// themselves. Drawing them in a sibling Group lets us toggle / restyle
// them without touching the element render pass.
//
// One-shot guide_line elements (which carry their own placement.from / to)
// already draw themselves in renderGuideLine — those are skipped here so
// we don't double-up.
// --------------------------------------------------------------------------

let relationshipsGroup = null;

function renderRelationshipLines(contract) {
  if (relationshipsGroup) state.scene.remove(relationshipsGroup);
  relationshipsGroup = new THREE.Group();
  relationshipsGroup.name = "RelationshipLines";
  relationshipsGroup.visible = state.showRelLines;
  state.relLines = [];

  const elementById = new Map(contract.spatialElements.map((e) => [e.id, e]));
  const guideLineEndpoints = new Set();
  for (const e of contract.spatialElements) {
    if (e.representationMode === "guide_line") {
      // Guide lines render their own segment — don't duplicate.
      const fromId = e.sourceContent?.fromElementId;
      const toId   = e.sourceContent?.toElementId;
      if (fromId && toId) {
        guideLineEndpoints.add(pairKey(fromId, toId));
      }
    }
  }

  for (const rel of contract.relationships || []) {
    if (guideLineEndpoints.has(pairKey(rel.from, rel.to))) continue;
    const fromEl = elementById.get(rel.from);
    const toEl   = elementById.get(rel.to);
    if (!fromEl || !toEl) continue;
    const fp = fromEl.placement?.position;
    const tp = toEl.placement?.position;
    if (!fp || !tp) continue;

    const color = REL_COLOR[rel.type] ?? REL_COLOR.relates_to;
    const mat = new THREE.LineDashedMaterial({
      color,
      dashSize: 0.04, gapSize: 0.04,
      transparent: true, opacity: 0.45, linewidth: 1,
      depthWrite: false,
    });
    const geo = new THREE.BufferGeometry().setFromPoints([
      new THREE.Vector3(fp[0], fp[1], fp[2]),
      new THREE.Vector3(tp[0], tp[1], tp[2]),
    ]);
    const line = new THREE.Line(geo, mat);
    line.computeLineDistances();
    line.userData.relationship = rel;
    relationshipsGroup.add(line);

    state.relLines.push({ from: rel.from, to: rel.to, type: rel.type, color, line });
  }

  state.scene.add(relationshipsGroup);
}

function pairKey(a, b) {
  // Order-independent key so guide_line dedupe matches both directions.
  return a < b ? `${a}::${b}` : `${b}::${a}`;
}

function attachRelToggle() {
  const btn = document.getElementById("rel-lines-toggle");
  if (!btn) return;
  btn.addEventListener("click", () => {
    state.showRelLines = !state.showRelLines;
    btn.setAttribute("aria-pressed", String(state.showRelLines));
    btn.textContent = state.showRelLines ? "relationships: on" : "relationships: off";
    if (relationshipsGroup) relationshipsGroup.visible = state.showRelLines;
  });
}

// --------------------------------------------------------------------------
// Stage interaction — click a 3D element to select it
// --------------------------------------------------------------------------

function attachStageInteraction() {
  // Track tap vs drag so we don't fire object animations when the user
  // is orbiting the camera. A pointerdown that moves >6 px before up is
  // a drag → no tap.
  let downX = 0, downY = 0, moved = false;
  state.renderer.domElement.addEventListener("pointerdown", (ev) => {
    downX = ev.clientX; downY = ev.clientY; moved = false;
  });
  state.renderer.domElement.addEventListener("pointermove", (ev) => {
    if (!moved && (Math.abs(ev.clientX - downX) > 6
                || Math.abs(ev.clientY - downY) > 6)) {
      moved = true;
    }
  });
  state.renderer.domElement.addEventListener("pointerup", (ev) => {
    if (moved) return; // it was a drag, not a tap — leave the camera alone
    const rect = state.renderer.domElement.getBoundingClientRect();
    state.pointer.x = ((ev.clientX - rect.left) / rect.width) * 2 - 1;
    state.pointer.y = -((ev.clientY - rect.top) / rect.height) * 2 + 1;
    state.raycaster.setFromCamera(state.pointer, state.camera);
    const hits = state.raycaster.intersectObject(elementsGroup, true);
    if (!hits.length) return;
    let node = hits[0].object;
    while (node && !node.userData?.elementId) node = node.parent;
    const eid = node?.userData?.elementId;
    if (!eid) return;
    // selectElement is the single entry point for "user inquired about
    // this element" — it paints the reasoning panel AND fires the
    // contract's tap interaction so any object-side animations play.
    selectElement(eid);
  });
}

// --------------------------------------------------------------------------
// Utilities
// --------------------------------------------------------------------------

function setStatus(text, kind = "ok") {
  if (!state.statusEl) return;
  state.statusEl.textContent = text;
  state.statusEl.style.color =
    kind === "bad" ? "var(--bad)" :
    kind === "warn" ? "var(--warn)" : "var(--text-dim)";
}

function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}
