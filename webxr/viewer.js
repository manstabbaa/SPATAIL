// SPATAIL · WebXR Content Viewer + Identify/Hitbox + object-local controls + live perception
// ----------------------------------------------------------------------------
// Renders a SPATAIL scene contract (a thin projection of
// schemas/spatialExperienceContract.schema.json) as a room-scale WebXR scene.
// Runs flat-3D on a PC browser (the dev surface) and immersive in a headset
// browser. Built capabilities:
//   • Identify / Hitboxes  — per-element AABB + label + intent + anchor + affordances + "why"
//   • Controls             — object-local control panels (MF p29/p35) driving real behaviors
//   • Spawn-in             — voxel/matter materialization on load (MF p18/p19)
//   • Detections           — sample 3D detection overlay (the contract shape)
//   • Live cam             — webcam → Gemini detector → boxes over the camera feed (MF p28/p40)
//   • Story step           — the attention track (MF p13)
//   • Strict assets        — render every GLB raw (scale 1.0, no re-centre, no fit) to
//                            reproduce phone failures; baked real-scale assets always
//                            render at 1.0 seated by their authored pivot
//   • Placement report     — POSTs what the web solver actually did back to the brain
//                            (LIVE_BRAIN_SPEC §2.2, client:"web") after a live /modular render
// ----------------------------------------------------------------------------

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { CSS2DRenderer, CSS2DObject } from 'three/addons/renderers/CSS2DRenderer.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';

const $ = (id) => document.getElementById(id);
const loadingEl = $('loading');
const DETECTOR_URL = new URLSearchParams(location.search).get('detector') || 'http://localhost:8766';
const BRAIN_URL = new URLSearchParams(location.search).get('brain') || 'http://localhost:8788';

const INTENT_COLOR = {
  explain: 0x5b4be6, inspect: 0x5b4be6, compare: 0x19c3a6, simulate: 0xe6a23c,
  guide: 0x4b9be6, place: 0x9b6be6, transform: 0xe64b9b, recognize: 0xff5c7a, default: 0x8a8a99,
};
const intentColor = (i) => INTENT_COLOR[(i || '').toLowerCase()] || INTENT_COLOR.default;
const hex = (n) => '#' + n.toString(16).padStart(6, '0');
const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const IMPL_VERBS = new Set(['rotate', 'animate', 'scale', 'isolate', 'explode']);

// ---------------------------------------------------------------- renderer/scene
const app = $('app');
const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.05;
renderer.xr.enabled = true;
app.appendChild(renderer.domElement);

const labelRenderer = new CSS2DRenderer();
labelRenderer.setSize(window.innerWidth, window.innerHeight);
Object.assign(labelRenderer.domElement.style, { position: 'fixed', top: '0', left: '0', pointerEvents: 'none', zIndex: '10' });
app.appendChild(labelRenderer.domElement);

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0d0d12);
const camera = new THREE.PerspectiveCamera(50, window.innerWidth / window.innerHeight, 0.01, 100);
camera.position.set(1.6, 1.45, 2.0);

const controls = new OrbitControls(camera, renderer.domElement);
controls.target.set(0, 0.9, 0);
controls.enableDamping = true; controls.dampingFactor = 0.08;
controls.maxDistance = 12; controls.minDistance = 0.3;

const pmrem = new THREE.PMREMGenerator(renderer);
scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
const key = new THREE.DirectionalLight(0xffffff, 2.0); key.position.set(3, 6, 4); scene.add(key);
scene.add(new THREE.HemisphereLight(0xbfc4ff, 0x202028, 0.6));

const overlayGroup = new THREE.Group(); overlayGroup.name = '__overlay'; scene.add(overlayGroup);
const liveGroup = new THREE.Group(); liveGroup.name = '__live'; scene.add(liveGroup);
const panelGroup = new THREE.Group(); panelGroup.name = '__panels'; scene.add(panelGroup);
const clock = new THREE.Clock();

// ---------------------------------------------------------------- the "room"
(function buildRoom() {
  const room = new THREE.Group(); room.name = '__room';
  const grid = new THREE.GridHelper(8, 32, 0x2a2a3a, 0x1a1a26); grid.position.y = 0.001; room.add(grid);
  const floor = new THREE.Mesh(new THREE.CircleGeometry(4, 64),
    new THREE.MeshStandardMaterial({ color: 0x14141c, roughness: 0.95 }));
  floor.rotation.x = -Math.PI / 2; room.add(floor);
  const table = new THREE.Mesh(new THREE.CylinderGeometry(0.75, 0.78, 0.74, 48),
    new THREE.MeshStandardMaterial({ color: 0x20202c, roughness: 0.6, metalness: 0.05 }));
  table.position.y = 0.37; room.add(table);
  scene.add(room);
})();

// ---------------------------------------------------------------- state
const loader = new GLTFLoader();
let contract = null;
let elements = [];          // [{el, group, box, helper, label, panel, bs, mixer, action}]
let detections = [];        // static 3D demo overlay
let identifyOn = false, liveOn = false, controlsOn = false, camOn = false;
let attentionIdx = -1, activeId = null;
let isolatedId = null, explodeOn = false;
let sceneCentroid = new THREE.Vector3(0, 1, 0);
let lastLabel = '';
const effects = [];
// Strict-asset mode: render every GLB raw (scale 1.0, no re-centre, no fit) so the
// PC reproduces phone failures instead of papering over them. Persisted; default OFF.
const STRICT_KEY = 'spatail.strictAssets';
let strictAssets = localStorage.getItem(STRICT_KEY) === '1';

// ---------------------------------------------------------------- helpers
function box3From(center, size) {
  const h = new THREE.Vector3(size[0] / 2, size[1] / 2, size[2] / 2);
  const c = new THREE.Vector3(...center);
  return new THREE.Box3(c.clone().sub(h), c.clone().add(h));
}
function boxHelper(box, color) {
  const h = new THREE.Box3Helper(box, color);
  h.material.transparent = true; h.material.opacity = 0.9; h.material.depthTest = false;
  return h;
}
function makeLabel(html, cls) {
  const div = document.createElement('div'); div.className = 'label3d' + (cls ? ' ' + cls : ''); div.innerHTML = html;
  return new CSS2DObject(div);
}
function setGroupOpacity(group, a) {
  group.traverse((o) => {
    if (!o.isMesh) return;
    const mats = Array.isArray(o.material) ? o.material : [o.material];
    for (const m of mats) { if (!m) continue; m.transparent = a < 1; m.opacity = a; m.depthWrite = a >= 1; }
  });
}
function placeModel(gltfScene, el) {
  const group = new THREE.Group(); group.add(gltfScene);
  const corrections = [];
  let s = 1;
  if (el.realScaleBaked) {
    // the asset pipeline baked real-world scale + pivot into the GLB: render at 1.0
    // and seat by mesh origin like iOS — never re-centre, so a bad bake fails here
    // exactly the way it fails on the phone.
    corrections.push('baked: rendered at scale 1.0, authored pivot honored');
  } else if (strictAssets && !el.realSizeMeters) {
    corrections.push('strict: raw render (scale 1.0, no re-centre, no fit)');
  } else {
    const box = new THREE.Box3().setFromObject(gltfScene);
    const size = new THREE.Vector3(); box.getSize(size);
    const target = el.realSizeMeters || el.placement.sizeMeters || [0.3, 0.3, 0.3];
    s = Math.max(...target) / (Math.max(size.x, size.y, size.z) || 1);
    gltfScene.scale.setScalar(s);
    corrections.push(`fit longest dim to ${Math.max(...target).toFixed(2)}m (×${s.toFixed(3)}${el.realSizeMeters ? ', from realSizeMeters' : ''})`);
    if (strictAssets) {
      corrections.push('strict: authored pivot honored (no re-centre)');
    } else {
      const center = new THREE.Vector3(); box.getCenter(center);
      gltfScene.position.sub(center.multiplyScalar(s));
      corrections.push('re-centred to AABB centre');
    }
  }
  const p = el.placement.position || [0, 0.9, 0];
  const r = el.placement.rotationDeg || [0, 0, 0];
  group.position.set(...p);
  group.rotation.set(THREE.MathUtils.degToRad(r[0]), THREE.MathUtils.degToRad(r[1]), THREE.MathUtils.degToRad(r[2]));
  group.userData.renderScale = s; group.userData.corrections = corrections;
  return group;
}
const affordList = (el) => (el.affordances || []).map((a) => `<span class="tag">${a}</span>`).join('');

// ---------------------------------------------------------------- spawn-in (MF p18/p19)
function spawnIn(group, box, color, delay) {
  const N = 320;
  const min = box.min, max = box.max;
  const pos = new Float32Array(N * 3), tgt = new Float32Array(N * 3), src = new Float32Array(N * 3);
  const rnd = (a, b) => a + Math.random() * (b - a);
  const c = new THREE.Vector3(); box.getCenter(c);
  for (let i = 0; i < N; i++) {
    const tx = rnd(min.x, max.x), ty = rnd(min.y, max.y), tz = rnd(min.z, max.z);
    tgt[i * 3] = tx; tgt[i * 3 + 1] = ty; tgt[i * 3 + 2] = tz;
    // start scattered on a sphere around the center
    const dir = new THREE.Vector3(rnd(-1, 1), rnd(-1, 1), rnd(-1, 1)).normalize().multiplyScalar(rnd(0.25, 0.6));
    src[i * 3] = c.x + dir.x; src[i * 3 + 1] = c.y + dir.y; src[i * 3 + 2] = c.z + dir.z;
    pos[i * 3] = src[i * 3]; pos[i * 3 + 1] = src[i * 3 + 1]; pos[i * 3 + 2] = src[i * 3 + 2];
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  const mat = new THREE.PointsMaterial({ color, size: 0.012, transparent: true, opacity: 0.9, depthWrite: false });
  const points = new THREE.Points(geo, mat); scene.add(points);
  setGroupOpacity(group, 0);
  const dur = 1.05, ease = (t) => 1 - Math.pow(1 - t, 3);
  effects.push({
    start: clock.elapsedTime + (delay || 0), dur, started: false,
    update(t) {
      const k = ease(t);
      for (let i = 0; i < N * 3; i++) pos[i] = src[i] + (tgt[i] - src[i]) * k;
      geo.attributes.position.needsUpdate = true;
      mat.opacity = 0.9 * (1 - t);
      setGroupOpacity(group, Math.min(1, t * 1.4));
    },
    done() { setGroupOpacity(group, 1); scene.remove(points); geo.dispose(); mat.dispose(); },
  });
}

// ---------------------------------------------------------------- living entities (MF p38–p43)
// Aliveness is NOT motion — it is the object's EXISTENCE: its nature, in context.
// Behaviour is DICTATED by what the thing IS (engine / heart / planet / chair …) crossed
// with the user's intent. A chair being evaluated is alive by sitting there real and
// STILL; a heart by beating; an engine by running; a planet by turning. So we classify
// each object's nature-in-context and let that dictate behaviour — stillness is as alive
// as motion. (This classifier is the on-device stand-in for the brain's `understanding`;
// the director should emit the nature so the runtime just expresses it.)
let focusedId = null, lastFocus = -1;
const centerRay = new THREE.Raycaster();
const _camPos = new THREE.Vector3();
const halo = new THREE.Mesh(
  new THREE.RingGeometry(0.62, 1, 48),
  new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0, side: THREE.DoubleSide, depthWrite: false, blending: THREE.AdditiveBlending }));
halo.rotation.x = -Math.PI / 2; halo.visible = false; scene.add(halo);

// nature-in-context → behaviour archetype. (subject + domain) say WHAT it is; intent
// gives the CONTEXT. The archetype dictates how it exists — not a generic motion preset.
function natureFor(el, c) {
  const intent = (el.intent || '').toLowerCase();
  const domain = ((c && c.detectedDomain && c.detectedDomain.name) || '').toLowerCase();
  const text = `${el.title || ''} ${el.id || ''} ${(c && c.sourcePrompt) || ''}`.toLowerCase();
  const has = (...ws) => ws.some((w) => text.includes(w));
  let a;
  if (has('heart', 'lung', 'pulse', 'cardiac', 'beat')) a = 'vital';
  else if (has('pendulum', 'metronome', 'swing')) a = 'pendular';
  else if (domain === 'astronomy' || has('planet', 'star', 'sun', 'moon', 'earth', 'mars', 'jupiter', 'saturn', 'galaxy', 'orbit')) a = 'celestial';
  else if (has('frog', 'lion', 'dog', 'cat', 'bird', 'fish', 'animal', 'creature', 'dragon', 'person', 'gnome', 'character', 'insect', 'reptile')) a = 'creature';
  else if (domain === 'biology' || has('plant', 'tree', 'flower', 'leaf', 'seed', 'sprout', 'cell', 'bacteria', 'virus', 'grow', 'neuron')) a = 'organic';
  else if (domain === 'mechanical' || has('engine', 'motor', 'machine', 'gear', 'turbine', 'pump', 'piston', 'crank', 'cam', 'vehicle', 'bike')) a = 'mechanical';
  else a = 'still';   // products, furniture, architecture, things to place/compare → quiet presence
  return { archetype: a, intent, domain };
}
// a heartbeat: a double-thump per cycle, not a sine.
function pulse(beat) { const p = beat - Math.floor(beat); const t1 = Math.exp(-(((p - 0.08) / 0.05) ** 2)); const t2 = 0.55 * Math.exp(-(((p - 0.26) / 0.05) ** 2)); return Math.min(1, t1 + t2); }
function collectLife(e, color) {
  e.mats = []; const col = new THREE.Color(color);
  e.group.traverse((o) => {
    if (!o.isMesh) return;
    const ms = Array.isArray(o.material) ? o.material : [o.material];
    for (const m of ms) { if (m && m.emissive !== undefined) { m.emissive = col.clone(); m.emissiveIntensity = 0.03; e.mats.push(m); } }
  });
}
function lerpAngle(a, b, t) { let d = ((b - a + Math.PI) % (2 * Math.PI)) - Math.PI; if (d < -Math.PI) d += 2 * Math.PI; return a + d * t; }

// which living thing the user is attending to (centre of gaze), else the selected one.
function updateFocus(now) {
  if (now - lastFocus < 0.12) return; lastFocus = now;
  if (!elements.length) { focusedId = null; return; }
  centerRay.setFromCamera({ x: 0, y: 0 }, camera);
  const hits = centerRay.intersectObjects(elements.map((e) => e.group), true);
  if (hits.length) { let o = hits[0].object; while (o && o.userData.elId === undefined) o = o.parent; if (o) focusedId = o.userData.elId; }
  else focusedId = activeId;
}

function updateLife(dt, now) {
  camera.getWorldPosition(_camPos);
  let topAtt = 0, topE = null;
  for (const e of elements) {
    if (!e.nature) continue;
    const A = e.nature.archetype, ph = e.phase;
    if (e.playing && e.mixer) e.mixer.update(dt);                 // its baked nature (engine runs, plant grows…)
    e.att += (((e.el.id === focusedId) ? 1 : 0) - e.att) * Math.min(1, dt * 4);
    // behaviour DICTATED BY NATURE — stillness is as alive as motion.
    let scaleMul = e.scaleStepF, bob = 0, glow = 0, rx = 0, rz = 0, face = 0, idle = 0;
    switch (A) {
      case 'still':      glow = e.att * 0.14; face = e.att * 0.5; break;                                    // exists by presence; turns to present itself when regarded
      case 'creature':   bob = 0.006 * Math.sin(now * 1.1 + ph); scaleMul *= 1 + 0.012 * Math.sin(now * 1.1 + ph); glow = 0.02 + e.att * 0.22; face = e.att * 0.85; idle = 0.04; break;
      case 'organic':    rz = 0.035 * Math.sin(now * 0.7 + ph); scaleMul *= 1 + 0.01 * Math.sin(now * 0.45 + ph); glow = 0.04 + e.att * 0.18; idle = 0.05; face = e.att * 0.25; break;
      case 'vital':      { const b = pulse(now * 1.1 + ph); scaleMul *= 1 + 0.05 * b; glow = 0.08 + 0.28 * b + e.att * 0.15; } break;   // heartbeat
      case 'pendular':   rx = 0.5 * Math.sin(now * 1.5 + ph); glow = 0.04 + e.att * 0.14; break;            // swings — that is its nature
      case 'celestial':  idle = 0.25; glow = 0.16 + 0.05 * Math.sin(now * 0.6 + ph) + e.att * 0.12; break;  // majestic turn + steady radiance
      case 'mechanical': if (!e.mixer) idle = 0.16; glow = 0.04 + e.att * 0.16; face = e.att * 0.35; break; // runs (baked clip, else a working turn)
    }
    e.idleYaw += idle * dt * (1 - 0.5 * e.att);
    if (e.spinOn) e.userYaw += 1.1 * dt;
    if (A !== 'vital') scaleMul *= 1 + e.att * 0.03;              // a calm presence lift when regarded
    e.group.scale.set(e.base.scale.x * scaleMul, e.base.scale.y * scaleMul, e.base.scale.z * scaleMul);
    e.group.position.set(e.base.pos.x + e.explodeOff.x, e.base.pos.y + e.explodeOff.y + bob, e.base.pos.z + e.explodeOff.z);
    const faceYaw = Math.atan2(_camPos.x - e.group.position.x, _camPos.z - e.group.position.z);
    e.group.rotation.x = e.base.rot.x + rx;
    e.group.rotation.z = e.base.rot.z + rz;
    e.group.rotation.y = lerpAngle(e.base.rot.y + e.idleYaw + e.userYaw, faceYaw, face);
    if (isolatedId && e.el.id !== isolatedId) glow *= 0.2;       // others fall quiet
    for (const m of e.mats) m.emissiveIntensity = glow;
    if (e.att > topAtt) { topAtt = e.att; topE = e; }
  }
  // a soft presence ring under the thing that's regarding you
  if (topE && topAtt > 0.06) {
    const sz = topE.el.placement.sizeMeters || [0.2, 0.2, 0.2];
    halo.position.set(topE.group.position.x, topE.base.pos.y - sz[1] / 2 + 0.006, topE.group.position.z);
    const r = Math.max(sz[0], sz[2]) * 0.85 + 0.05; halo.scale.set(r, r, r);
    halo.material.color.set(intentColor(topE.el.intent)); halo.material.opacity = topAtt * 0.42; halo.visible = true;
  } else halo.visible = false;
}

// ---------------------------------------------------------------- behaviour nudges (compose ON TOP of the life)
function elById(id) { return elements.find((e) => e.el.id === id); }

function applyBehavior(id, verb) {
  const e = elById(id); if (!e) return;
  switch (verb) {
    case 'rotate': e.spinOn = !e.spinOn; break;
    case 'animate':
      if (e.action) { e.playing = !e.playing; e.action.paused = !e.playing; if (e.playing && !e.action.isRunning()) e.action.play(); }
      else { e.spinOn = !e.spinOn; }
      break;
    case 'scale': { const steps = [1, 1.5, 0.6]; e.scaleStepF = steps[(steps.indexOf(e.scaleStepF) + 1) % 3]; break; }
    case 'isolate':
      if (isolatedId === id) { isolatedId = null; elements.forEach((x) => setGroupOpacity(x.group, 1)); }
      else { isolatedId = id; elements.forEach((x) => setGroupOpacity(x.group, x.el.id === id ? 1 : 0.12)); }
      break;
    case 'explode':
      explodeOn = !explodeOn;
      for (const x of elements) {
        if (explodeOn) x.explodeOff.copy(x.base.pos).sub(sceneCentroid).multiplyScalar(0.7);
        else x.explodeOff.set(0, 0, 0);
      }
      break;
    case 'reset':
      isolatedId = null; explodeOn = false;
      elements.forEach((x) => {
        x.spinOn = false; x.playing = false; x.userYaw = 0; x.scaleStepF = 1; x.explodeOff.set(0, 0, 0);
        if (x.action) x.action.paused = true; setGroupOpacity(x.group, 1);
      });
      break;
    default:
      setNarration('Behaviour', `“${verb}” is part of this living object's repertoire (not nudgeable in this build).`);
  }
  updateAllPanels();
}

function updateAllPanels() {
  for (const e of elements) {
    if (!e.panel) continue;
    const btns = e.panel.element.__btns || {};
    if (btns.rotate) btns.rotate.classList.toggle('active', !!e.spinOn);
    if (btns.animate) btns.animate.classList.toggle('active', !!e.playing);
    if (btns.scale) btns.scale.classList.toggle('active', e.scaleStepF !== 1);
    if (btns.isolate) btns.isolate.classList.toggle('active', isolatedId === e.el.id);
    if (btns.explode) btns.explode.classList.toggle('active', explodeOn);
  }
}

function makeControlPanel(e) {
  const div = document.createElement('div'); div.className = 'ctrlpanel';
  const head = document.createElement('div'); head.className = 'ph'; head.textContent = e.el.title || e.el.id; div.appendChild(head);
  const btns = {};
  const verbs = [...new Set([...(e.el.affordances || []), 'reset'])];
  for (const v of verbs) {
    const b = document.createElement('button'); b.textContent = v;
    if (!IMPL_VERBS.has(v) && v !== 'reset') b.style.opacity = '0.7';
    b.addEventListener('click', (ev) => { ev.stopPropagation(); applyBehavior(e.el.id, v); });
    div.appendChild(b); btns[v] = b;
  }
  div.__btns = btns;
  const obj = new CSS2DObject(div);
  const c = new THREE.Vector3(); e.box.getCenter(c);
  const s = new THREE.Vector3(); e.box.getSize(s);
  obj.position.set(c.x + s.x / 2 + 0.04, c.y, c.z);
  obj.visible = false; obj.center.set(0, 0.5);
  return obj;
}

// ---------------------------------------------------------------- load a scene
async function loadScene(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`scene fetch ${res.status}: ${url}`);
  await renderScene(await res.json(), url.split('/').pop());
}

async function renderScene(c, label) {
  for (const e of elements) { scene.remove(e.group); overlayGroup.remove(e.helper); overlayGroup.remove(e.label); panelGroup.remove(e.panel); }
  elements = []; $('idlist').innerHTML = '';
  lastLabel = label || c.title || '';
  setNarration('Ready', lastLabel);
  attentionIdx = -1; activeId = null; isolatedId = null; explodeOn = false;

  contract = c;
  $('sceneMeta').innerHTML =
    `<b>${contract.title || contract.experienceId || 'Scene'}</b> · ${contract.experienceType || contract.detectedDomain?.name || ''}` +
    (contract.sourcePrompt ? `<br>“${contract.sourcePrompt}”` : '');

  const els = contract.spatialElements || [];
  // compute scene centroid for explode
  const cen = new THREE.Vector3();
  els.forEach((el) => cen.add(new THREE.Vector3(...(el.placement.position || [0, 0.9, 0]))));
  if (els.length) cen.multiplyScalar(1 / els.length); sceneCentroid = cen;

  let idx = 0;
  for (const el of els) {
    try {
      let group, mixer = null, action = null;
      if (el.asset?.glbUrl) {
        const gurl = el.asset.glbUrl.startsWith('http') ? el.asset.glbUrl : (contract.glbBase || '') + el.asset.glbUrl;
        const gltf = await loader.loadAsync(gurl);
        group = placeModel(gltf.scene, el);
        if (gltf.animations && gltf.animations.length) {
          mixer = new THREE.AnimationMixer(gltf.scene);
          action = mixer.clipAction(gltf.animations[0]); action.play(); action.paused = true;
        }
      } else {
        const p = el.placement.position || [0, 0.9, 0]; const sz = el.placement.sizeMeters || [0.2, 0.2, 0.2];
        group = new THREE.Group();
        group.add(new THREE.Mesh(new THREE.BoxGeometry(...sz), new THREE.MeshStandardMaterial({ color: 0x33334a, roughness: 0.7 })));
        group.position.set(...p);
        group.userData.renderScale = 1; group.userData.corrections = ['placeholder box (no GLB yet)'];
      }
      group.userData.elId = el.id; scene.add(group);

      const center = el.placement.position || [0, 0.9, 0]; const size = el.placement.sizeMeters || [0.2, 0.2, 0.2];
      // baked elements are seated by mesh origin, so their declared box sits ON the position
      const boxCenter = el.realScaleBaked ? [center[0], center[1] + size[1] / 2, center[2]] : center;
      const box = box3From(boxCenter, size); const col = intentColor(el.intent);
      const helper = boxHelper(box, col); helper.visible = false; overlayGroup.add(helper);
      const szTxt = size.map((n) => n.toFixed(2)).join('×');
      const label = makeLabel(
        `<b>${el.title || el.id}</b>` + (el.intent ? ` <span class="li">${el.intent}</span>` : '') +
        `<br><span class="sz">${szTxt} m · ${el.anchorStrategy || el.placement.kind || ''}</span>`);
      label.position.set(boxCenter[0], boxCenter[1] + size[1] / 2 + 0.06, boxCenter[2]); label.visible = false; overlayGroup.add(label);

      const e = {
        el, group, box, helper, label, mixer, action,
        base: { pos: group.position.clone(), scale: group.scale.clone(), rot: group.rotation.clone() },
        nature: natureFor(el, contract), phase: idx * 1.7,
        att: 0, idleYaw: 0, userYaw: 0, spinOn: false, scaleStepF: 1, playing: false,
        explodeOff: new THREE.Vector3(), mats: [],
      };
      collectLife(e, col);
      // a thing's baked clip IS its nature (engine running, plant growing) — auto-run it,
      // unless the thing's nature is stillness.
      e.playing = (e.nature.archetype !== 'still') && !!action;
      if (action) action.paused = !e.playing;
      e.panel = (el.affordances && el.affordances.length) ? makeControlPanel(e) : null;
      if (e.panel) panelGroup.add(e.panel);
      e.row = addIdRow(el, col);
      elements.push(e);
      spawnIn(group, box, col, idx * 0.1);
      idx++;
    } catch (err) {
      console.error('element failed', el.id, err);
      addIdRow({ ...el, title: (el.title || el.id) + ' (load failed)' }, 0xe6a23c);
    }
  }
  $('idcount').textContent = elements.length;
  applyOverlayVisibility(); frameScene();
}

function addIdRow(el, col) {
  const row = document.createElement('div'); row.className = 'idrow'; row.dataset.id = el.id;
  row.innerHTML =
    `<div class="name"><span class="swatch" style="background:${hex(col)}"></span>${el.title || el.id}</div>` +
    `<div class="tags">` +
      (el.intent ? `<span class="tag intent">${el.intent}</span>` : '') +
      (el.representationMode ? `<span class="tag">${el.representationMode}</span>` : '') +
      (el.scaleMode ? `<span class="tag">${el.scaleMode}</span>` : '') + affordList(el) +
    `</div><div class="why">` +
      // honesty rule: brain text is labelled "Brain:" and shown verbatim; the client's
      // own layout resolution is labelled "Web solver:" — never blended.
      (el.whyThisRepresentation ? `<b>Why this:</b> ${el.whyThisRepresentation}<br>` : '') +
      (el.brainTrace && el.brainTrace.length ? `<b>Brain:</b> ${el.brainTrace.map(esc).join('<br>')}<br>` : '') +
      (el.whyThisPlacement ? `<b>Why here:</b> ${el.whyThisPlacement}<br>` : '') +
      (el.webSolver ? `<b>Web solver:</b> ${esc(el.webSolver)}` : '') + `</div>`;
  row.addEventListener('click', () => focusElement(el.id));
  $('idlist').appendChild(row); return row;
}

// ---------------------------------------------------------------- overlay visibility
function applyOverlayVisibility() {
  for (const e of elements) {
    e.helper.visible = identifyOn; e.label.visible = identifyOn;
    if (e.panel) e.panel.visible = controlsOn || e.el.id === activeId;
  }
  $('identify').classList.toggle('show', identifyOn);
  $('btnIdentify').classList.toggle('on', identifyOn);
  $('btnControls').classList.toggle('on', controlsOn);
  for (const d of detections) { d.helper.visible = liveOn; d.label.visible = liveOn; }
  $('btnLive').classList.toggle('on', liveOn);
  $('btnCam').classList.toggle('on', camOn);
}

function focusElement(id) {
  activeId = id;
  for (const e of elements) e.row?.classList.toggle('active', e.el.id === id);
  const e = elById(id); if (!e) return;
  const c = new THREE.Vector3(); e.box.getCenter(c); controls.target.copy(c);
  applyOverlayVisibility();
}

function frameScene() {
  const box = new THREE.Box3(); for (const e of elements) box.union(e.box);
  if (box.isEmpty()) return;
  const c = new THREE.Vector3(); box.getCenter(c); const s = new THREE.Vector3(); box.getSize(s);
  const r = Math.max(s.x, s.y, s.z); controls.target.copy(c);
  camera.position.set(c.x + r * 1.1, c.y + r * 0.8, c.z + r * 1.6);
}

// ---------------------------------------------------------------- click-to-select (raycast)
const raycaster = new THREE.Raycaster(); const ptr = new THREE.Vector2(); let downXY = null;
renderer.domElement.addEventListener('pointerdown', (e) => { downXY = [e.clientX, e.clientY]; });
renderer.domElement.addEventListener('pointerup', (e) => {
  if (!downXY) return;
  const moved = Math.hypot(e.clientX - downXY[0], e.clientY - downXY[1]); downXY = null;
  if (moved > 5) return; // was a drag (orbit), not a click
  ptr.x = (e.clientX / window.innerWidth) * 2 - 1; ptr.y = -(e.clientY / window.innerHeight) * 2 + 1;
  raycaster.setFromCamera(ptr, camera);
  const hits = raycaster.intersectObjects(elements.map((x) => x.group), true);
  if (!hits.length) return;
  let o = hits[0].object; while (o && o.userData.elId === undefined) o = o.parent;
  if (o && o.userData.elId) { focusElement(o.userData.elId); setNarration('Selected', elById(o.userData.elId)?.el.title || o.userData.elId); }
});

// ---------------------------------------------------------------- static detection overlay (the contract shape)
async function loadDetections(sceneUrl) {
  liveGroup.clear(); detections = []; $('hint').textContent = '';
  try {
    const res = await fetch(sceneUrl.replace(/\.json$/, '.detections.json'));
    if (!res.ok) return;
    const data = await res.json();
    for (const d of data.detections || []) {
      const helper = boxHelper(box3From(d.position, d.sizeMeters), 0x19c3a6); helper.visible = false; liveGroup.add(helper);
      const label = makeLabel(`<b>${d.label}</b> <span class="li">${Math.round((d.confidence ?? 1) * 100)}%</span>` +
        `<br><span class="sz">sample · ${d.source || 'video'}</span>`, 'live');
      label.position.set(d.position[0], d.position[1] + d.sizeMeters[1] / 2 + 0.06, d.position[2]); label.visible = false; liveGroup.add(label);
      detections.push({ d, helper, label });
    }
    if (data.detections?.length) $('hint').textContent = `sample detections: ${data.detections.length} (toggle “Detections”)`;
  } catch (e) { /* none */ }
}

// ---------------------------------------------------------------- live cam → Gemini detector (MF p28/p40)
let camStream = null, camTimer = null, camBusy = false;
const offCanvas = document.createElement('canvas');

async function startCam() {
  try {
    camStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' }, audio: false })
      .catch(() => navigator.mediaDevices.getUserMedia({ video: true, audio: false }));
  } catch (e) { setNarration('Live cam', 'Camera access denied: ' + e.message); camOn = false; applyOverlayVisibility(); return; }
  const v = $('camvideo'); v.srcObject = camStream; await v.play().catch(() => {});
  $('camview').classList.add('show');
  $('camhud').innerHTML = `<b>Live perception</b> — webcam → ${DETECTOR_URL}/detect (Gemini)`;
  camTimer = setInterval(detectFrame, 1600); detectFrame();
}
function stopCam() {
  if (camTimer) clearInterval(camTimer); camTimer = null;
  if (camStream) camStream.getTracks().forEach((t) => t.stop()); camStream = null;
  $('camview').classList.remove('show'); $('detboxes').innerHTML = '';
}
async function detectFrame() {
  if (camBusy) return; const v = $('camvideo');
  if (!v.videoWidth) return; camBusy = true;
  try {
    const w = 640, h = Math.round(640 * v.videoHeight / v.videoWidth);
    offCanvas.width = w; offCanvas.height = h;
    offCanvas.getContext('2d').drawImage(v, 0, 0, w, h);
    const dataUrl = offCanvas.toDataURL('image/jpeg', 0.6);
    const t0 = performance.now();
    const res = await fetch(DETECTOR_URL + '/detect', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ imageBase64: dataUrl, mime: 'image/jpeg' }),
    });
    if (!res.ok) throw new Error('detector ' + res.status);
    const data = await res.json();
    renderDetBoxes(data.detections || []);
    $('camhud').innerHTML = `<b>Live perception</b> — ${(data.detections || []).length} objects · ${data.model || ''} · ${Math.round(performance.now() - t0)}ms`;
  } catch (e) {
    $('camhud').innerHTML = `<b>Live perception</b> — detector unreachable at ${DETECTOR_URL}.<br>Run: <code>python webxr/live/detector_server.py</code>`;
  } finally { camBusy = false; }
}
function renderDetBoxes(dets) {
  const host = $('detboxes'); host.innerHTML = '';
  for (const d of dets) {
    const [x, y, w, h] = d.bbox || [0, 0, 0, 0];
    const div = document.createElement('div'); div.className = 'detbox';
    div.style.left = (x * 100) + '%'; div.style.top = (y * 100) + '%';
    div.style.width = (w * 100) + '%'; div.style.height = (h * 100) + '%';
    div.innerHTML = `<span class="cap">${d.label} ${Math.round((d.confidence ?? 1) * 100)}%</span>`;
    host.appendChild(div);
  }
}

// ---------------------------------------------------------------- attention track (MF p13)
function stepAttention() {
  const plan = contract?.attentionPlan || [];
  if (!plan.length) { setNarration('No story', 'This scene has no attention track.'); return; }
  attentionIdx = (attentionIdx + 1) % plan.length;
  const s = plan[attentionIdx];
  focusElement(s.focusElementId);
  if (!identifyOn) { identifyOn = true; applyOverlayVisibility(); }
  setNarration(`Step ${s.step}/${plan.length}`, s.narration);
}
function setNarration(step, text) { $('narration').innerHTML = `<span class="step">${step}</span>${text || ''}`; }

// ---------------------------------------------------------------- scene picker
async function loadIndex() {
  const idx = await (await fetch('./scenes/index.json')).json();
  const sel = $('sceneSelect'); sel.innerHTML = '';
  for (const s of idx.scenes) { const o = document.createElement('option'); o.value = s.url; o.textContent = s.title; sel.appendChild(o); }
  sel.addEventListener('change', () => switchScene(sel.value));
  return idx.scenes[0]?.url;
}
async function switchScene(url) {
  loadingEl.classList.remove('hide'); loadingEl.textContent = 'Loading scene…';
  try {
    await loadScene(url); await loadDetections(url); applyOverlayVisibility();
    loadingEl.classList.add('hide');
  } catch (e) {
    loadingEl.innerHTML = `<div class="err">Failed to load scene.<br>${e.message}<br><br>Serve the repo root over HTTP (see webxr/README.md).</div>`;
    console.error(e);
  }
}

// ---------------------------------------------------------------- live brain (/modular → web scene)
// Adapt the brain's v0.5 modular contract (+ v0.6 sceneContract) into the viewer's
// web scene. Placement is INTENT (anchor/layout/footprints), not coords, so we
// resolve positions here with a layout solver — the same job the on-device
// Placement solver does against a real RoomModel.
const TABLE_TOP = 0.78;
const mapScale = (m) => ({ dynamic: 'tabletop_scale', fit_to_surface: 'tabletop_scale', real: 'real_scale',
  real_scale: 'real_scale', room: 'room_scale', room_scale: 'room_scale' }[m] || 'tabletop_scale');
const mapAnchor = (a) => ({ table: 'world_anchor', floor: 'plane_anchor', wall: 'plane_anchor',
  object: 'object_anchor', room: 'world_anchor', free: 'simulated_anchor' }[a] || 'world_anchor');
const cap = (s) => s ? s[0].toUpperCase() + s.slice(1) : s;

function resolveLayout(items, layout, baseY) {
  const n = items.length, gap = 0.14;
  const widths = items.map((e) => Math.max(0.08, e.size[0]));
  const totalW = widths.reduce((s, w) => s + w, 0) + gap * (n - 1);
  const cols = Math.ceil(Math.sqrt(n));
  let x = -totalW / 2, stackY = baseY; const out = [];
  for (let i = 0; i < n; i++) {
    const w = widths[i], h = Math.max(0.08, items[i].size[1]), cx = x + w / 2; x += w + gap;
    // baked assets are seated by mesh origin (authored pivot), not by AABB centre
    const yOff = items[i].baked ? 0 : h / 2;
    if (layout === 'arc' && n > 1) {
      const ang = (i / (n - 1) - 0.5) * 0.7, R = 0.5 + totalW * 0.2;
      out.push([Math.sin(ang) * R, baseY + yOff, R - Math.cos(ang) * R]);
    } else if (layout === 'stack') {
      out.push([0, stackY + yOff, 0]); stackY += h + 0.02;
    } else if (layout === 'cluster' || layout === 'grid') {
      const r = Math.floor(i / cols), col = i % cols, sp = 0.28;
      out.push([(col - (cols - 1) / 2) * sp, baseY + yOff, (r - 0.5) * sp]);
    } else {
      out.push([cx, baseY + yOff, 0]); // row / default
    }
  }
  return out;
}

function brainToWebScene(c, brainBase) {
  const u = c.understanding || {};
  const pl = (c.sceneContract || {}).placement || {};
  // the §12 design-system contract (spec §2.3): the sceneContract projection embeds
  // it as placement.designSystem; the raw modular contract carries it at placement.
  // Read both so decisionTrace survives when the projection is absent.
  const ds = pl.designSystem || c.placement || {};
  const layout = (c.stage || {}).layout || pl.layout || 'arc';
  const anchor = (c.stage || {}).anchor || pl.anchorPreference || 'table';
  const sem = (ds.interaction || {}).semanticActions || ['rotate', 'scale', 'isolate', 'animate'];
  const baseAff = sem.map((s) => ({ isolate_part: 'isolate', highlight_part: 'label' }[s] || s)).filter((s) => s !== 'reset');
  const assets = c.assets || [];
  const fp = pl.footprints || {};
  const primaryId = pl.primary || (assets.find((a) => a.role === 'primary_object') || {}).id || (assets[0] || {}).id;
  const items = assets.map((a) => ({
    id: a.id, name: a.name || a.id, role: a.role, glbUrl: a.glbUrl || '',
    size: (a.realSizeMeters || fp[a.id] || a.scaleMeters || [0.2, 0.2, 0.2]).slice(0, 3),
    anim: !!a.supportsAnimation, named: !!a.supportsHighlight,
    // real-scale contract (asset_service.produce): baked → GLB is already metric,
    // render at 1.0 honoring the authored pivot; realSizeMeters → fit to it.
    baked: !!a.realScaleBaked, realSizeMeters: a.realSizeMeters || null,
    footprintSource: a.footprintSource ||
      ((a.realSizeMeters || fp[a.id] || a.scaleMeters) ? 'library' : 'default_guess'),
  }));
  const baseY = anchor === 'floor' ? 0.02 : anchor === 'wall' ? 1.3 : TABLE_TOP;
  const pos = resolveLayout(items, layout, baseY);
  // honesty: whatever the brain said travels verbatim (decisionTrace → "Brain:");
  // what THIS client resolved is labelled "Web solver:" — never blended.
  const brainTrace = Array.isArray(ds.decisionTrace) ? ds.decisionTrace : [];
  const spatialElements = items.map((a, i) => ({
    id: a.id, title: a.name,
    intent: a.id === primaryId ? (u.intent || 'inspect') : 'explain',
    representationMode: 'three_d_model',
    scaleMode: mapScale((c.stage || {}).scaleMode || pl.scaleMode),
    anchorStrategy: mapAnchor(anchor),
    asset: a.glbUrl ? { glbUrl: a.glbUrl } : undefined,
    realScaleBaked: a.baked || undefined,
    realSizeMeters: a.realSizeMeters || undefined,
    placement: { kind: anchor, position: pos[i], rotationDeg: [0, 0, 0], sizeMeters: a.size },
    affordances: [...new Set([...baseAff, ...(a.anim ? ['animate'] : []), ...(a.named ? ['isolate', 'label'] : [])])],
    whyThisRepresentation: u.summary || '',
    brainTrace,
    webSolver: `${layout} layout, baseY=${baseY}, slot ${i + 1}/${items.length}` +
      (a.baked ? ', seat-by-origin (baked)' : ''),
  }));
  const seq = c.sequence || [];
  let attentionPlan = seq.filter((s) => s && (s.assetId || s.narration || s.text))
    .map((s, i) => ({ step: i + 1, focusElementId: s.assetId || (spatialElements[Math.min(i, spatialElements.length - 1)] || {}).id, narration: s.narration || s.text || '' }))
    .filter((s) => s.focusElementId);
  if (!attentionPlan.length) attentionPlan = spatialElements.map((e, i) => ({ step: i + 1, focusElementId: e.id, narration: i === 0 ? (u.summary || e.title) : e.title }));
  return {
    schemaVersion: '0.2.0-spatail-web', experienceId: c.experienceId || 'brain',
    title: c.title || u.subject || 'Experience', sourcePrompt: u.subject || '',
    detectedDomain: { name: u.domain || 'general', confidence: 'medium', source: c.composer || 'brain' },
    experienceType: u.intent ? cap(u.intent) : (c.composer || 'Experience'),
    glbBase: brainBase, spatialElements, attentionPlan, primaryId,
    // inputs the web solver worked from — reported back via POST /placement-report
    solver: {
      anchor, layout, baseY,
      scaleMode: (c.stage || {}).scaleMode || pl.scaleMode || 'dynamic',
      // sceneContract projection carries it as coverage; the raw modular §12 contract
      // as scale.maxSurfaceCoverage (same knob). Absent in both → JSON drops undefined.
      coverage: pl.coverage ?? (ds.scale || {}).maxSurfaceCoverage,
      footprints: items.map((a) => ({ assetId: a.id, meters: a.size, source: a.footprintSource })),
    },
  };
}

// §2.2 placement report — tell the brain what this client ACTUALLY did with its
// placement intent, so /traces/view can put intent vs web reality side by side.
// Fire-and-forget: rendering never waits on it; failures go to the console.
function sendPlacementReport(web) {
  const sv = web && web.solver; if (!sv) return;   // only scenes resolved from a live /modular contract
  const fitsOn = (p, size) => {
    const reach = Math.hypot(p[0], p[2]) + Math.max(size[0], size[2]) / 2;
    if (sv.anchor === 'table') return reach <= 0.75;   // the built room's table radius
    if (sv.anchor === 'floor') return reach <= 4;      // the built room's floor radius
    return true;                                       // wall/free: nothing built to collide with
  };
  for (const e of elements) e.group.updateMatrixWorld(true);
  const body = {
    experienceId: web.experienceId,
    client: 'web',
    reportedAt: Date.now() / 1000,
    solverInputs: {
      anchorPreference: sv.anchor,
      scaleMode: sv.scaleMode,
      coverage: sv.coverage,
      footprints: sv.footprints,
      roomSummary: { surfaces: [                       // the synthetic room, as built
        { kind: 'table', sizeMeters: [1.5, 1.5], y: 0.74 },
        { kind: 'floor', sizeMeters: [8, 8], y: 0 },
      ] },
    },
    plan: {
      anchor: sv.anchor,
      placements: elements.map((e) => ({
        elementId: e.el.id,
        position: e.el.placement.position || [0, 0, 0],
        yaw: THREE.MathUtils.degToRad((e.el.placement.rotationDeg || [0, 0, 0])[1]),
        scale: e.group.userData.renderScale ?? 1,
        fits: fitsOn(e.el.placement.position || [0, 0, 0], e.el.placement.sizeMeters || [0.2, 0.2, 0.2]),
        reason: e.el.webSolver || '',
      })),
    },
    finalPlacements: elements.map((e) => ({
      elementId: e.el.id,
      worldTransform: e.group.matrixWorld.elements.slice(),   // 16 floats, column-major (three.js native)
      renderScale: e.group.userData.renderScale ?? 1,
      corrections: e.group.userData.corrections || [],
    })),
  };
  fetch(BRAIN_URL + '/placement-report', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }).then((r) => { if (!r.ok) console.warn('placement-report rejected: ' + r.status); })
    .catch((e) => console.warn('placement-report failed:', e.message));
}

// Poll a Meshy generation job and hot-swap the placeholder for the real mesh when
// it lands. The brain runs the full Meshy pipeline (asset_service.produce) and
// streams 9 determinate stages via /jobs/{id}; we surface them and spawn the GLB in.
let genToken = 0;
async function pollGeneration(jobId, targetElId) {
  const myToken = ++genToken;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  while (genToken === myToken) {
    let j;
    try { j = await (await fetch(BRAIN_URL + '/jobs/' + jobId)).json(); }
    catch (e) { $('hint').textContent = `gen poll failed: ${e.message}`; return; }
    if (j.status === 'queued') {
      $('hint').textContent = `Meshy queued${j.queuePosition ? ` (${j.queuePosition} ahead)` : ''} · ${jobId}`;
    } else if (j.status === 'done') {
      if (j.glb_url) {
        $('hint').textContent = `Meshy mesh ready — spawning in`;
        try { await swapElementGlb(targetElId, j.glb_url.startsWith('http') ? j.glb_url : BRAIN_URL + j.glb_url); } catch (e) { console.error('swap failed', e); }
        $('hint').textContent = `Meshy asset live · ${jobId}`;
      } else { $('hint').textContent = `gen done (no GLB) · ${jobId}`; }
      return;
    } else if (j.status === 'error' || j.status === 'failed') {
      $('hint').textContent = `Meshy generation failed: ${j.message || ''}`; return;
    } else {
      const pct = j.percent != null ? ` · ${j.percent}%` : '';
      const k = j.stageTotal ? ` (${j.stageIndex || 0}/${j.stageTotal})` : '';
      $('hint').textContent = `Meshy: ${j.stage || j.status}${k}${pct}`;
    }
    await sleep(1600);
  }
}

async function swapElementGlb(elId, url) {
  const e = elById(elId) || elements.find((x) => !x.el.asset);  // primary, else first placeholder
  if (!e) return;
  const gltf = await loader.loadAsync(url);
  const newGroup = placeModel(gltf.scene, e.el);
  newGroup.userData.elId = e.el.id;
  scene.remove(e.group); scene.add(newGroup);
  e.group = newGroup;
  e.base = { pos: newGroup.position.clone(), scale: newGroup.scale.clone(), rot: newGroup.rotation.clone() };
  e.att = 0; e.idleYaw = 0; e.userYaw = 0; e.spinOn = false; e.scaleStepF = 1; e.playing = false; e.explodeOff = new THREE.Vector3();
  e.nature = natureFor(e.el, contract); collectLife(e, intentColor(e.el.intent));
  e.mixer = null; e.action = null;
  if (gltf.animations && gltf.animations.length) {
    e.mixer = new THREE.AnimationMixer(gltf.scene);
    e.action = e.mixer.clipAction(gltf.animations[0]); e.action.play(); e.action.paused = true;
  }
  e.playing = (e.nature.archetype !== 'still') && !!e.action;     // its baked nature runs (unless it's a still thing)
  if (e.action) e.action.paused = !e.playing;
  spawnIn(newGroup, e.box, intentColor(e.el.intent), 0);   // "spawning in" the real Meshy mesh
  e.row?.classList.remove('placeholder');
  newGroup.userData.corrections = [...(newGroup.userData.corrections || []), 'meshy hot-swap'];
  sendPlacementReport(contract);   // the final placement changed — re-report (append-only server side)
}

async function generateFromBrain() {
  const prompt = $('brainPrompt').value.trim();
  if (!prompt) { $('brainPrompt').focus(); return; }
  const btn = $('btnBrain'); btn.classList.add('busy'); btn.textContent = '✦ Thinking…';
  loadingEl.classList.remove('hide'); loadingEl.textContent = `Asking the brain: “${prompt}”…`;
  try {
    const res = await fetch(BRAIN_URL + '/modular', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kind: 'text', text: prompt, use_llm: true }),
    });
    if (!res.ok) throw new Error('brain ' + res.status);
    const c = await res.json();
    if (c.error) throw new Error(c.error);
    liveGroup.clear(); detections = [];
    const web = brainToWebScene(c, BRAIN_URL);
    await renderScene(web, c.title);
    sendPlacementReport(web);
    const placeholders = (c.assets || []).filter((a) => !a.glbUrl).length;
    $('hint').textContent = `live from brain · ${c.composer || ''}` +
      (placeholders ? ` · ${placeholders} asset(s) generating via Meshy` : '') +
      (c.generationJobId ? ` · job ${c.generationJobId}` : '');
    loadingEl.classList.add('hide');
    // If the brain queued a Meshy build for a missing asset, poll it and hot-swap.
    if (c.generationJobId) pollGeneration(c.generationJobId, web.primaryId);
    else { genToken++; }   // cancel any prior poll
  } catch (e) {
    loadingEl.innerHTML = `<div class="err">Brain request failed.<br>${e.message}<br><br>Start it: <code>python studio/server/job_server.py --port 8788</code><br>or pass <code>?brain=http://host:port</code>.</div>`;
    console.error(e);
  } finally { btn.classList.remove('busy'); btn.textContent = '✦ Generate'; }
}
$('btnBrain').addEventListener('click', generateFromBrain);
$('brainPrompt').addEventListener('keydown', (e) => { if (e.key === 'Enter') generateFromBrain(); });

// ---------------------------------------------------------------- UI wiring
$('btnStrict').classList.toggle('on', strictAssets);
$('btnStrict').addEventListener('click', async () => {
  strictAssets = !strictAssets;
  localStorage.setItem(STRICT_KEY, strictAssets ? '1' : '0');
  $('btnStrict').classList.toggle('on', strictAssets);
  if (contract) {
    try { await renderScene(contract, lastLabel); sendPlacementReport(contract); }
    catch (err) { console.error('strict re-render failed', err); }
  }
  // after the re-render, so renderScene's "Ready" doesn't swallow the feedback
  setNarration('Strict assets', strictAssets
    ? 'ON — every GLB renders raw (scale 1.0, no re-centre, no fit), like the phone.'
    : 'OFF — the viewer fits + re-centres GLBs for preview.');
});
$('btnIdentify').addEventListener('click', () => { identifyOn = !identifyOn; applyOverlayVisibility(); });
$('btnControls').addEventListener('click', () => { controlsOn = !controlsOn; applyOverlayVisibility(); });
$('btnLive').addEventListener('click', () => { liveOn = !liveOn; applyOverlayVisibility(); });
$('btnCam').addEventListener('click', () => { camOn = !camOn; applyOverlayVisibility(); camOn ? startCam() : stopCam(); });
$('btnStep').addEventListener('click', stepAttention);
window.addEventListener('keydown', (e) => {
  if (e.target.tagName === 'SELECT') return;
  const k = e.key.toLowerCase();
  if (k === 'i') { identifyOn = !identifyOn; applyOverlayVisibility(); }
  if (k === 'o') { controlsOn = !controlsOn; applyOverlayVisibility(); }
  if (k === 'l') { liveOn = !liveOn; applyOverlayVisibility(); }
  if (k === 'c') { camOn = !camOn; applyOverlayVisibility(); camOn ? startCam() : stopCam(); }
  if (e.code === 'Space') { e.preventDefault(); stepAttention(); }
});

// immersive XR (no addon button — keeps the PC chrome clean)
let xrSession = null;
async function toggleXR() {
  if (!navigator.xr) return;
  if (xrSession) { await xrSession.end(); return; }
  try {
    const session = await navigator.xr.requestSession('immersive-vr', { optionalFeatures: ['local-floor', 'bounded-floor', 'hand-tracking'] });
    xrSession = session; renderer.xr.setReferenceSpaceType('local-floor'); await renderer.xr.setSession(session);
    $('btnVR').textContent = '⛶ Exit XR';
    session.addEventListener('end', () => { xrSession = null; $('btnVR').textContent = '⛶ Enter XR'; });
  } catch (e) { console.warn('XR failed', e); $('hint').textContent = 'XR start failed: ' + e.message; }
}
$('btnVR').addEventListener('click', toggleXR);
if (navigator.xr && navigator.xr.isSessionSupported) {
  navigator.xr.isSessionSupported('immersive-vr').then((ok) => { $('btnVR').textContent = ok ? '⛶ Enter XR' : '⛶ XR n/a (PC)'; $('btnVR').disabled = !ok; })
    .catch(() => { $('btnVR').textContent = '⛶ XR n/a (PC)'; $('btnVR').disabled = true; });
} else { $('btnVR').textContent = '⛶ XR n/a (PC)'; $('btnVR').disabled = true; }

window.addEventListener('resize', () => {
  camera.aspect = window.innerWidth / window.innerHeight; camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight); labelRenderer.setSize(window.innerWidth, window.innerHeight);
});

// ---------------------------------------------------------------- main loop
let __loopErr = 0;
function tick() {
 try {
  window.__frames = (window.__frames || 0) + 1;
  const dt = clock.getDelta(); const now = clock.elapsedTime;
  controls.update();
  // effects (spawn-in)
  for (let i = effects.length - 1; i >= 0; i--) {
    const fx = effects[i]; if (now < fx.start) continue;
    const t = Math.min(1, (now - fx.start) / fx.dur); fx.update(t);
    if (t >= 1) { fx.done(); effects.splice(i, 1); }
  }
  // living entities: breathe, idle, notice the user, express by intent
  updateFocus(now);
  updateLife(dt, now);
  // overlay pulse
  const p = 0.55 + 0.35 * (0.5 + 0.5 * Math.sin(now * 2));
  overlayGroup.children.forEach((c) => { if (c.material) c.material.opacity = p; });
  renderer.render(scene, camera);
  labelRenderer.render(scene, camera);
 } catch (err) {
   if (__loopErr++ < 2) { console.error('LOOP ERROR:', err.message, err.stack?.split('\n')[1]); window.__loopError = err.message; }
 }
}
renderer.setAnimationLoop(tick);
// rAF pauses when the tab/preview panel is backgrounded — keep rendering via a
// timer fallback so the viewer never freezes when it loses focus.
let __fallback = null;
function manageFallback() {
  if (document.hidden) { if (!__fallback) __fallback = setInterval(tick, 33); }
  else if (__fallback) { clearInterval(__fallback); __fallback = null; }
}
document.addEventListener('visibilitychange', manageFallback);
manageFallback();

// ---------------------------------------------------------------- debug
window.__dbg = () => ({
  els: elements.length,
  labelVis: elements.map((e) => e.label?.visible),
  labelInDoc: elements.map((e) => !!e.label?.element?.parentNode),
  panelVis: elements.map((e) => e.panel?.visible),
  lrKids: labelRenderer.domElement.children.length,
  lrInApp: labelRenderer.domElement.parentNode === document.getElementById('app'),
  identifyOn, controlsOn, strictAssets,
  tri: renderer.info.render.triangles, calls: renderer.info.render.calls, frames: window.__frames || 0,
});
window.__behave = (id, verb) => applyBehavior(id, verb);
window.__select = (id) => focusElement(id);
window.__poll = (jobId, elId) => pollGeneration(jobId, elId);
window.__nature = (title, intent, domain, prompt) => natureFor({ title, intent, id: title }, { detectedDomain: { name: domain || '' }, sourcePrompt: prompt || '' }).archetype;
window.__state = () => elements.map((e) => {
  let op = 1; e.group.traverse((o) => { if (o.isMesh && o.material && !Array.isArray(o.material)) op = o.material.opacity; });
  return { id: e.el.id, arch: e.nature?.archetype, spin: e.spinOn ? 1 : 0, att: +(e.att ?? 0).toFixed(2), glow: +(e.mats?.[0]?.emissiveIntensity ?? 0).toFixed(3),
           rotY: +e.group.rotation.y.toFixed(2), scale: +e.group.scale.x.toFixed(3),
           renderScale: +(e.group.userData.renderScale ?? 1).toFixed(4), corrections: e.group.userData.corrections || [],
           pos: e.group.position.toArray().map((n) => +n.toFixed(2)), opacity: +op.toFixed(2) };
});

// ---------------------------------------------------------------- boot
(async function boot() {
  try { const first = await loadIndex(); if (first) await switchScene(first); else loadingEl.classList.add('hide'); }
  catch (e) { loadingEl.innerHTML = `<div class="err">Could not start viewer.<br>${e.message}</div>`; console.error(e); }
})();
