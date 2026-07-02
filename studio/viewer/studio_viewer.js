// SPATAIL Studio viewer — the "tester room" on the desktop.
//
// Loads the StudioSceneContract, renders the studio GLB at human scale from the
// user's eye, plays the looping physics animation, draws the XR comfort guides
// (so the placement reasoning is visible), and steps through the beats.
//
// Frame: the contract + GLB are y-up metres, so contract anchors map straight
// to THREE.Vector3. The same contract will drive the real XR runtime later.

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { CSS2DRenderer, CSS2DObject } from "three/addons/renderers/CSS2DRenderer.js";

const CONTRACT_URL = "/studio/out/StudioSceneContract.json";

const S = {
  contract: null, scene: null, camera: null, renderer: null, labelRenderer: null,
  controls: null, mixer: null, clock: new THREE.Clock(), playing: true,
  guides: null, guidesOn: true, beatLabels: [], activeBeat: 0,
  camTween: null,
};

window.__studio = S;   // debug hook: inspect/drive camera from the console
boot().catch((e) => { console.error(e); setStatus("fatal: " + e.message); });

async function boot() {
  const res = await fetch(CONTRACT_URL, { cache: "no-store" });
  if (!res.ok) throw new Error("no StudioSceneContract.json — run `python studio/run.py` first");
  S.contract = await res.json();
  document.getElementById("scene-title").textContent = S.contract.title || "";

  initThree();
  buildControls();
  buildGuides();
  await loadStudio();
  buildBeatLabels();
  paintFacts();
  focusBeat(0, true);
  wireAskBar();
  setStatus("ready");
  animate();
}

// SPATAIL EDUCATOR front door: ask a question -> server runs the Blender
// pipeline -> reload the tester room with the freshly built answer.
function wireAskBar() {
  const form = document.getElementById("ask-form");
  const input = document.getElementById("ask-input");
  const btn = document.getElementById("ask-btn");
  if (!form) return;
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const q = input.value.trim();
    if (!q) return;
    btn.disabled = true; input.disabled = true;
    setStatus(`building "${q}" in Blender… (this can take a minute)`);
    try {
      const res = await fetch("/api/ask", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: q }),
      });
      const data = await res.json();
      if (data.ok) { setStatus("built — reloading…"); location.reload(); }
      else { setStatus("couldn't build that yet: " + (data.log || "").split("\n").pop()); }
    } catch (err) {
      setStatus("ask failed: " + err.message);
    } finally {
      btn.disabled = false; input.disabled = false;
    }
  });
}

function initThree() {
  const wrap = document.getElementById("canvas-wrap");
  const w = wrap.clientWidth, h = wrap.clientHeight;
  const eye = S.contract.comfortGuides.eye_height_m;

  S.scene = new THREE.Scene();
  S.scene.background = new THREE.Color(0x0e1116);

  S.camera = new THREE.PerspectiveCamera(55, w / h, 0.05, 100);
  S.camera.position.set(0, eye + 0.1, 1.7);

  S.renderer = new THREE.WebGLRenderer({ antialias: true });
  S.renderer.setPixelRatio(window.devicePixelRatio);
  S.renderer.setSize(w, h);
  S.renderer.toneMapping = THREE.ACESFilmicToneMapping;
  S.renderer.toneMappingExposure = 1.1;
  wrap.appendChild(S.renderer.domElement);

  S.labelRenderer = new CSS2DRenderer({ element: document.getElementById("labels") });
  S.labelRenderer.setSize(w, h);

  S.controls = new OrbitControls(S.camera, S.renderer.domElement);
  S.controls.enableDamping = true;
  S.controls.dampingFactor = 0.08;
  S.controls.target.set(0, S.contract.comfortGuides.baseline_z_m, -1.6);

  S.scene.add(new THREE.HemisphereLight(0xdfe9ff, 0x1a1f2a, 1.0));
  const key = new THREE.DirectionalLight(0xffffff, 2.0); key.position.set(3, 6, 4);
  S.scene.add(key);
  const fill = new THREE.DirectionalLight(0xb0c8ff, 0.7); fill.position.set(-4, 3, 2);
  S.scene.add(fill);

  window.addEventListener("resize", onResize);
}

function onResize() {
  const wrap = document.getElementById("canvas-wrap");
  const w = wrap.clientWidth, h = wrap.clientHeight;
  S.camera.aspect = w / h; S.camera.updateProjectionMatrix();
  S.renderer.setSize(w, h); S.labelRenderer.setSize(w, h);
}

async function loadStudio() {
  setStatus("loading studio…");
  const url = "/" + S.contract.assets[0].processedPath;
  const gltf = await new Promise((res, rej) =>
    new GLTFLoader().load(url, res, undefined, rej));
  S.scene.add(gltf.scene);

  // Play every clip so the demo animates whether the exporter produced one
  // combined clip or one per object.
  if (gltf.animations && gltf.animations.length) {
    S.mixer = new THREE.AnimationMixer(gltf.scene);
    for (const clip of gltf.animations) {
      const a = S.mixer.clipAction(clip);
      a.setLoop(THREE.LoopRepeat); a.play();
    }
  }
  const a = S.contract.studio.animation;
  setHud(`${a.frames}f @ ${a.fps}fps · ${a.seconds}s loop · ${gltf.animations?.length || 0} clip(s)`);
}

// --- XR comfort guides --------------------------------------------------------

function buildGuides() {
  const g = S.contract.comfortGuides;
  const grp = new THREE.Group(); grp.name = "comfortGuides";
  const eye = g.eye_height_m;
  const lineMat = new THREE.LineBasicMaterial({ color: 0x4f8cff, transparent: true, opacity: 0.7 });
  const softMat = new THREE.LineBasicMaterial({ color: 0x3fb950, transparent: true, opacity: 0.5 });

  // eye-height reference ring on a pole at the origin (where the user stands)
  grp.add(new THREE.Mesh(
    new THREE.CylinderGeometry(0.02, 0.02, eye, 12),
    new THREE.MeshBasicMaterial({ color: 0x243040 })
  ).translateY(eye / 2));

  // 30° no-head-turn cone (horizontal bounds) from the eye, out to the stage
  const D = g.stage_distance_m + 0.3;
  const half = THREE.MathUtils.degToRad(g.cone_deg / 2);
  for (const s of [-1, 1]) {
    const pts = [new THREE.Vector3(0, eye, 0),
      new THREE.Vector3(s * D * Math.sin(half), eye, -D * Math.cos(half))];
    grp.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), lineMat));
  }
  // gaze-down baseline line (12° below horizon)
  const gd = THREE.MathUtils.degToRad(g.gaze_down_deg);
  grp.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints([
    new THREE.Vector3(0, eye, 0),
    new THREE.Vector3(0, eye - D * Math.tan(gd), -D)]), lineMat));

  // focal-plane ring (0.74 m) — where a single manipulable object would sit
  grp.add(ring(g.focal_plane_m, g.focal_point_yup[1], 0x4f8cff, 0.6));
  // staging arc ring (where the exhibits actually sit)
  grp.add(ring(g.stage_distance_m, g.baseline_z_m, 0x3fb950, 0.7));

  S.guides = grp; S.scene.add(grp);
}

function ring(radius, y, color, opacity) {
  const pts = [];
  for (let i = 0; i <= 64; i++) {
    const t = (i / 64) * Math.PI * 2;
    pts.push(new THREE.Vector3(Math.sin(t) * radius, y, -Math.cos(t) * radius));
  }
  return new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(pts),
    new THREE.LineBasicMaterial({ color, transparent: true, opacity }));
}

// --- beat labels (CSS2D) ------------------------------------------------------

function buildBeatLabels() {
  for (const b of S.contract.beats) {
    const el = document.createElement("div");
    el.className = "beat-label dim";
    el.innerHTML = `<div class="law">${b.law}</div><div class="sub">${b.subtitle}</div>`;
    const obj = new CSS2DObject(el);
    obj.position.set(...b.labelAnchor);
    S.scene.add(obj);
    S.beatLabels.push({ el, obj, beat: b });
  }
}

// --- interactions -------------------------------------------------------------

function buildControls() {
  const root = document.getElementById("controls");
  for (const it of S.contract.interactions) {
    const btn = document.createElement("button");
    btn.textContent = it.label; btn.dataset.id = it.id;
    if (it.id === "play_pause" || it.id === "toggle_guides") btn.classList.add("on");
    btn.onclick = () => handle(it.id, btn);
    root.appendChild(btn);
  }
}

function handle(id, btn) {
  if (id === "play_pause") {
    S.playing = !S.playing; btn.classList.toggle("on", S.playing);
  } else if (id === "toggle_guides") {
    S.guidesOn = !S.guidesOn; S.guides.visible = S.guidesOn;
    btn.classList.toggle("on", S.guidesOn);
  } else if (id === "next_beat") {
    focusBeat((S.activeBeat + 1) % S.contract.beats.length);
  } else if (id === "prev_beat") {
    focusBeat((S.activeBeat - 1 + S.contract.beats.length) % S.contract.beats.length);
  } else if (id === "reset_view") {
    const eye = S.contract.comfortGuides.eye_height_m;
    tweenCamera(new THREE.Vector3(0, eye + 0.1, 1.7),
      new THREE.Vector3(0, S.contract.comfortGuides.baseline_z_m, -1.6));
  }
}

function focusBeat(i, instant) {
  S.activeBeat = i;
  const b = S.contract.beats[i];
  const target = new THREE.Vector3(...b.focusTarget);
  // view the exhibit from the user's side, slightly above
  const camPos = target.clone().add(new THREE.Vector3(0, 0.4, 1.5));
  if (instant) { S.camera.position.copy(camPos); S.controls.target.copy(target); }
  else tweenCamera(camPos, target);

  S.beatLabels.forEach((L, idx) => {
    L.el.classList.toggle("active", idx === i);
    L.el.classList.toggle("dim", idx !== i);
  });
  const n = document.getElementById("narration");
  n.innerHTML = `<div class="law">${b.law} — ${b.subtitle}</div>` +
    `<div class="title">${b.title}</div><div>${b.narration}</div>`;
}

function tweenCamera(pos, target) {
  S.camTween = { fromP: S.camera.position.clone(), toP: pos,
    fromT: S.controls.target.clone(), toT: target, t: 0 };
}

// --- sidebar facts ------------------------------------------------------------

function paintFacts() {
  const g = S.contract.comfortGuides;
  const st = S.contract.staging;
  document.getElementById("why").textContent = st.reasoning;
  const rows = [
    ["eye height", g.eye_height_m + " m"],
    ["stage dist", st.distance_m + " m"],
    ["arc spread", st.spread_deg + "°"],
    ["baseline", g.baseline_z_m + " m"],
    ["comfort cone", g.cone_deg + "°"],
    ["focal plane", g.focal_plane_m + " m"],
  ];
  const dl = document.getElementById("facts"); dl.innerHTML = "";
  for (const [k, v] of rows) {
    const dt = document.createElement("dt"); dt.textContent = k;
    const dd = document.createElement("dd"); dd.textContent = v;
    dl.appendChild(dt); dl.appendChild(dd);
  }
}

// --- loop ---------------------------------------------------------------------

function animate() {
  requestAnimationFrame(animate);
  const dt = S.clock.getDelta();
  if (S.mixer && S.playing) S.mixer.update(dt);
  if (S.camTween) {
    const tw = S.camTween; tw.t = Math.min(1, tw.t + dt * 1.8);
    const e = tw.t * tw.t * (3 - 2 * tw.t); // smoothstep
    S.camera.position.lerpVectors(tw.fromP, tw.toP, e);
    S.controls.target.lerpVectors(tw.fromT, tw.toT, e);
    if (tw.t >= 1) S.camTween = null;
  }
  S.controls.update();
  S.renderer.render(S.scene, S.camera);
  S.labelRenderer.render(S.scene, S.camera);
}

function setStatus(t) { const e = document.getElementById("status"); if (e) e.textContent = t; }
function setHud(t) { const e = document.getElementById("hud"); if (e) e.textContent = t; }
