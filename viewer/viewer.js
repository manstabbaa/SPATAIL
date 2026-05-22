// Spatial Studio v0.1 — web viewer.
//
// Loads /scene_contracts/SpatialSceneContract.json, loads every asset
// listed there (currently .glb after Blender normalization, with .obj
// and .stl as pass-through fallbacks), and wires the contract's UI
// elements to the interaction bricks it declares.
//
// The browser side is intentionally not the source of truth: every
// interaction must be expressed in the contract first so that the
// future visionOS player gets the same behavior for free.

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { OBJLoader } from "three/addons/loaders/OBJLoader.js";
import { STLLoader } from "three/addons/loaders/STLLoader.js";

// --------------------------------------------------------------------------
// State
// --------------------------------------------------------------------------

const state = {
  contract: null,
  scene: null,
  camera: null,
  renderer: null,
  controls: null,
  root: null,            // THREE.Group containing every loaded asset
  assetGroups: [],       // [{ assetId, role, group, originalPosition, originalMaterials }]
  defaultCamera: { position: new THREE.Vector3(), target: new THREE.Vector3() },
  activeBricks: new Set(),
  hudEl: null,
  statusEl: null,
};

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

  setStatus("fetching scene contract…");
  const res = await fetch("/scene_contracts/SpatialSceneContract.json", { cache: "no-store" });
  if (!res.ok) {
    throw new Error(
      "no SpatialSceneContract.json yet — run `npm run generate` first.",
    );
  }
  state.contract = await res.json();

  paintMeta(state.contract);
  paintStory(state.contract);
  paintInventory(state.contract);
  paintUnderstanding(state.contract);

  initThree();
  await loadAllAssets(state.contract);
  frameSceneToCamera();
  saveDefaultView();

  buildButtons(state.contract);

  setStatus("ready");
  startRenderLoop();
}

// --------------------------------------------------------------------------
// Sidebar painting
// --------------------------------------------------------------------------

function paintMeta(contract) {
  document.getElementById("scene-name").textContent =
    contract.sceneName || "(untitled)";
  const u = contract.spatialUnderstanding || {};
  const domain = u.detectedDomain || "unknown";
  const conf = u.domainConfidence ? ` (${u.domainConfidence})` : "";
  document.getElementById("scene-domain").textContent = `${domain}${conf}`;
}

function paintStory(contract) {
  const ol = document.getElementById("story-list");
  ol.innerHTML = "";
  for (const step of contract.storySequence || []) {
    const li = document.createElement("li");
    li.innerHTML = `<strong>${step.title}</strong><small>${step.description}</small>`;
    ol.appendChild(li);
  }
}

function paintInventory(contract) {
  const ul = document.getElementById("asset-list");
  ul.innerHTML = "";
  for (const a of contract.assets || []) {
    const li = document.createElement("li");
    const dot = document.createElement("span");
    dot.className = "status-dot " + statusClass(a.status);
    const tag = document.createElement("span");
    tag.className = "role-tag " + (a.role === "primary_object" ? "primary" : "component");
    tag.textContent = a.role === "primary_object" ? "primary" : "part";
    const name = document.createElement("span");
    name.className = "asset-name";
    name.textContent = a.detectedObjectName || a.fileName;
    name.title = a.fileName;
    li.appendChild(dot); li.appendChild(tag); li.appendChild(name);
    ul.appendChild(li);
  }
}

function statusClass(status) {
  if (status === "processed" || status === "ok") return "ok";
  if (status === "unsupported") return "unsupported";
  return "failed";
}

function paintUnderstanding(contract) {
  const dl = document.getElementById("understanding");
  dl.innerHTML = "";
  const u = contract.spatialUnderstanding || {};
  const p = contract.placement || {};
  const rows = [
    ["primary",       u.primaryObject || "—"],
    ["use case",      u.likelyUseCase || "—"],
    ["representation",u.representationMode || "—"],
    ["anchor",        p.anchorType || "—"],
    ["scale",         p.scaleMode || "—"],
  ];
  for (const [k, v] of rows) {
    const dt = document.createElement("dt"); dt.textContent = k;
    const dd = document.createElement("dd"); dd.textContent = v;
    dl.appendChild(dt); dl.appendChild(dd);
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

  state.camera = new THREE.PerspectiveCamera(45, w / h, 0.01, 10000);
  state.camera.position.set(3, 2, 4);

  state.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  state.renderer.setPixelRatio(window.devicePixelRatio);
  state.renderer.setSize(w, h);
  state.renderer.toneMapping = THREE.ACESFilmicToneMapping;
  state.renderer.toneMappingExposure = 1.0;
  wrap.appendChild(state.renderer.domElement);

  state.controls = new OrbitControls(state.camera, state.renderer.domElement);
  state.controls.enableDamping = true;
  state.controls.dampingFactor = 0.08;

  // Lighting. Three-point-ish, soft.
  const hemi = new THREE.HemisphereLight(0xc6dfff, 0x1a1f2a, 0.85);
  state.scene.add(hemi);

  const key = new THREE.DirectionalLight(0xffffff, 1.2);
  key.position.set(4, 6, 4);
  state.scene.add(key);

  const fill = new THREE.DirectionalLight(0xb0c8ff, 0.4);
  fill.position.set(-5, 2, -3);
  state.scene.add(fill);

  // Ground grid for spatial reference.
  const grid = new THREE.GridHelper(20, 20, 0x2a2f3a, 0x1a1d24);
  grid.position.y = 0;
  grid.material.transparent = true;
  grid.material.opacity = 0.6;
  state.scene.add(grid);

  state.root = new THREE.Group();
  state.root.name = "SpatialRoot";
  state.scene.add(state.root);

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
  const tick = () => {
    state.controls.update();
    state.renderer.render(state.scene, state.camera);
    requestAnimationFrame(tick);
  };
  tick();
}

// --------------------------------------------------------------------------
// Asset loading
// --------------------------------------------------------------------------

const gltfLoader = new GLTFLoader();
const objLoader = new OBJLoader();
const stlLoader = new STLLoader();

async function loadAllAssets(contract) {
  for (const asset of contract.assets || []) {
    if (asset.status !== "processed" && asset.status !== "ok") {
      console.warn(`[viewer] skipping ${asset.fileName}: status=${asset.status}`);
      continue;
    }
    const url = asset.processedPath ? "/" + asset.processedPath : null;
    if (!url) continue;
    try {
      setStatus(`loading ${asset.fileName}…`);
      const group = await loadByExt(url);
      group.name = asset.id;
      group.userData.assetId = asset.id;
      group.userData.role = asset.role;
      group.userData.detectedObjectName = asset.detectedObjectName;
      state.root.add(group);

      // Snapshot originals for reset / isolate / explode.
      const originalMaterials = new Map();
      group.traverse((obj) => {
        if (obj.isMesh) {
          originalMaterials.set(obj.uuid, obj.material);
        }
      });

      state.assetGroups.push({
        assetId: asset.id,
        role: asset.role,
        group,
        originalPosition: group.position.clone(),
        originalMaterials,
      });
    } catch (e) {
      console.error(`[viewer] failed to load ${asset.fileName}:`, e);
      setStatus(`failed: ${asset.fileName}`, "bad");
    }
  }
}

function loadByExt(url) {
  const lower = url.toLowerCase();
  if (lower.endsWith(".glb") || lower.endsWith(".gltf")) {
    return new Promise((res, rej) => gltfLoader.load(url, (g) => res(g.scene), undefined, rej));
  }
  if (lower.endsWith(".obj")) {
    return new Promise((res, rej) => objLoader.load(url, (o) => res(o), undefined, rej));
  }
  if (lower.endsWith(".stl")) {
    return new Promise((res, rej) =>
      stlLoader.load(
        url,
        (geom) => {
          const mat = new THREE.MeshStandardMaterial({
            color: 0xb6c2d1, roughness: 0.55, metalness: 0.15,
          });
          const mesh = new THREE.Mesh(geom, mat);
          const g = new THREE.Group();
          g.add(mesh);
          res(g);
        },
        undefined,
        rej,
      ));
  }
  return Promise.reject(new Error(`unsupported asset url: ${url}`));
}

// --------------------------------------------------------------------------
// Auto-framing
// --------------------------------------------------------------------------

function sceneBounds() {
  const box = new THREE.Box3();
  let hasContent = false;
  state.root.traverse((obj) => {
    if (obj.isMesh) {
      obj.geometry?.computeBoundingBox?.();
      const b = new THREE.Box3().setFromObject(obj);
      if (isFinite(b.min.x)) {
        if (!hasContent) { box.copy(b); hasContent = true; }
        else box.union(b);
      }
    }
  });
  return hasContent ? box : null;
}

function frameSceneToCamera() {
  const box = sceneBounds();
  if (!box) {
    state.hudEl.textContent = "no geometry loaded";
    return;
  }
  const size = new THREE.Vector3();
  const center = new THREE.Vector3();
  box.getSize(size);
  box.getCenter(center);

  const maxDim = Math.max(size.x, size.y, size.z);
  const fov = state.camera.fov * (Math.PI / 180);
  let dist = (maxDim / 2) / Math.tan(fov / 2);
  dist *= 1.8; // padding

  // Place camera off-axis so the model isn't a flat silhouette.
  const dir = new THREE.Vector3(1, 0.7, 1).normalize();
  state.camera.position.copy(center).addScaledVector(dir, dist);
  state.camera.near = Math.max(dist / 1000, 0.001);
  state.camera.far = dist * 100;
  state.camera.updateProjectionMatrix();

  state.controls.target.copy(center);
  state.controls.update();

  state.hudEl.textContent =
    `bbox ${size.x.toFixed(2)} × ${size.y.toFixed(2)} × ${size.z.toFixed(2)}  ` +
    `units · ${state.assetGroups.length} part(s)`;
}

function saveDefaultView() {
  state.defaultCamera.position.copy(state.camera.position);
  state.defaultCamera.target.copy(state.controls.target);
}

// --------------------------------------------------------------------------
// UI buttons -> interaction bricks
// --------------------------------------------------------------------------

// Keyed by brick *type* (not id), so future contracts can add many bricks
// of the same type with different ids/targets and they all dispatch correctly.
const BRICK_HANDLERS = {
  reset_view: brickReset,
  highlight: brickHighlight,
  isolate: brickIsolate,
  explode: brickExplode,
};

function buildButtons(contract) {
  const root = document.getElementById("ui-buttons");
  root.innerHTML = "";

  const bricksById = new Map();
  for (const b of contract.interactionBricks || []) bricksById.set(b.id, b);

  for (const el of contract.uiElements || []) {
    if (el.type !== "button") continue;
    if (Array.isArray(el.visibleIn) && !el.visibleIn.includes("viewer")) continue;

    const brick = bricksById.get(el.action);
    const handler = brick ? BRICK_HANDLERS[brick.type] : null;

    const btn = document.createElement("button");
    btn.className = "brick";
    btn.textContent = el.label;
    btn.dataset.brickId = el.action;
    btn.dataset.brickType = brick?.type || "unknown";

    if (!handler) {
      btn.disabled = true;
      btn.title = "no viewer handler for this brick yet";
    } else {
      btn.addEventListener("click", () => {
        try {
          const isActive = state.activeBricks.has(el.action);
          handler({ brick, element: el, toggleOff: isActive });
          if (isActive) {
            state.activeBricks.delete(el.action);
            btn.classList.remove("active");
          } else if (brick.type !== "reset_view") {
            // reset_view is a one-shot; everything else toggles.
            state.activeBricks.add(el.action);
            btn.classList.add("active");
          }
          setStatus(`brick: ${brick.type}`);
        } catch (err) {
          console.error(err);
          setStatus(`brick failed: ${err.message}`, "bad");
        }
      });
    }
    root.appendChild(btn);
  }
}

// --------------------------------------------------------------------------
// Bricks
// --------------------------------------------------------------------------

function brickReset() {
  // Clear other toggled bricks too — reset is the global undo.
  for (const ag of state.assetGroups) {
    ag.group.position.copy(ag.originalPosition);
    ag.group.visible = true;
    ag.group.traverse((obj) => {
      if (obj.isMesh) {
        const orig = ag.originalMaterials.get(obj.uuid);
        if (orig) obj.material = orig;
      }
    });
  }
  state.activeBricks.clear();
  for (const b of document.querySelectorAll("button.brick.active")) {
    b.classList.remove("active");
  }
  state.camera.position.copy(state.defaultCamera.position);
  state.controls.target.copy(state.defaultCamera.target);
  state.controls.update();
}

function brickHighlight({ toggleOff }) {
  for (const ag of state.assetGroups) {
    if (ag.role !== "primary_object") continue;
    ag.group.traverse((obj) => {
      if (!obj.isMesh) return;
      if (toggleOff) {
        const orig = ag.originalMaterials.get(obj.uuid);
        if (orig) obj.material = orig;
      } else {
        obj.material = new THREE.MeshStandardMaterial({
          color: 0x6ea8ff,
          emissive: 0x1f3a6e,
          emissiveIntensity: 0.4,
          roughness: 0.35,
          metalness: 0.2,
        });
      }
    });
  }
}

function brickIsolate({ toggleOff }) {
  // If there's only one asset, this is a no-op (graceful per spec).
  if (state.assetGroups.length <= 1) return;
  for (const ag of state.assetGroups) {
    if (ag.role === "primary_object") {
      ag.group.visible = true;
    } else {
      ag.group.visible = toggleOff ? true : false;
    }
  }
}

function brickExplode({ toggleOff }) {
  // Move every component outward from the scene center along its current
  // offset vector. If only one asset exists, no-op (per spec).
  if (state.assetGroups.length <= 1) return;
  const box = sceneBounds();
  if (!box) return;
  const center = new THREE.Vector3();
  box.getCenter(center);
  const size = new THREE.Vector3();
  box.getSize(size);
  const factor = 0.6; // how far to push apart, in scene units.
  const maxDim = Math.max(size.x, size.y, size.z) || 1;

  for (const ag of state.assetGroups) {
    if (toggleOff) {
      ag.group.position.copy(ag.originalPosition);
      continue;
    }
    const groupBox = new THREE.Box3().setFromObject(ag.group);
    const groupCenter = new THREE.Vector3();
    groupBox.getCenter(groupCenter);
    const offset = groupCenter.sub(center);
    if (offset.lengthSq() < 1e-6) {
      // Co-incident parts: nudge along Y so they don't overlap perfectly.
      offset.set(0, maxDim * 0.1, 0);
    }
    offset.normalize().multiplyScalar(maxDim * factor);
    ag.group.position.copy(ag.originalPosition).add(offset);
  }
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
