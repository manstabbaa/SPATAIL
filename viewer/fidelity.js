// Fidelity gradient — Step 3 of v0.4.
//
// The renderer reads `element.fidelity` and styles the output so the user
// can SEE the planner's confidence:
//
//   ghost     — dotted wireframe of the planned footprint + an intent
//               label drawn from the prompt phrase. The "I'm proposing
//               this" state. Appears instantly when a prompt is received.
//
//   draft     — solid coloured block at the proposed pose with a
//               provisional accent material. The planner has committed
//               to a shape but not the final geometry.
//
//   committed — the existing renderer output (real geometry, real
//               material, real placement). What we've been shipping.
//
//   authored  — same geometry as committed, but tinted with a subtle
//               "production seal" outline so the user can tell which
//               elements are blessed by the Blender pass.
//
// This module is intentionally a *decorator*: it takes whatever the
// representation renderer returned and wraps/replaces it based on the
// element's fidelity. New fidelities = new branches here.

import * as THREE from "three";

export function applyFidelity(builtMesh, element) {
  const fidelity = element.fidelity || "committed";
  switch (fidelity) {
    case "ghost":     return ghostOf(element, builtMesh);
    case "draft":     return draftOf(element, builtMesh);
    case "authored":  return authoredOf(element, builtMesh);
    case "committed": // fall through — return as-is
    default:          return builtMesh;
  }
}

// --------------------------------------------------------------------------
// ghost — dotted wireframe footprint
// --------------------------------------------------------------------------

function ghostOf(element, builtMesh) {
  // Replace the built mesh entirely. The ghost is a dashed outline of the
  // element's *placement footprint* + an intent label. The user reads
  // this as "this is where this thing is going to land if you don't
  // correct me."
  const group = new THREE.Group();
  group.name = `ghost.${element.id}`;

  const dims = footprintFor(element);
  const wire = makeDashedRect(dims.w, dims.h, dims.d);
  group.add(wire);

  // Vertical "claim flag" — a thin upright line + label so the ghost
  // reads from across the room.
  const flagH = Math.max(0.06, dims.h * 0.6);
  const flagGeom = new THREE.BufferGeometry().setFromPoints([
    new THREE.Vector3(0, 0, 0),
    new THREE.Vector3(0, flagH, 0),
  ]);
  const flagMat = new THREE.LineDashedMaterial({
    color: 0x6ea8ff, dashSize: 0.012, gapSize: 0.010,
    transparent: true, opacity: 0.65,
  });
  const flag = new THREE.Line(flagGeom, flagMat);
  flag.computeLineDistances();
  group.add(flag);

  group.add(makeLabel(intentTextFor(element), {
    w: Math.max(0.32, dims.w * 0.9), h: 0.06, yOffset: flagH + 0.04,
    color: "#3c4250", bg: "rgba(255,255,255,0.92)",
    border: "rgba(110,168,255,0.85)",
  }));

  applyPlacement(group, element);
  return group;
}

function makeDashedRect(w, h, d) {
  // Pick the two largest dimensions to draw a rectangle on.
  const dims = [w, h, d];
  const sorted = [...dims].map((v, i) => [v, i]).sort((a, b) => b[0] - a[0]);
  const [a, ai] = sorted[0];
  const [b, bi] = sorted[1];
  const axes = [
    [1, 0, 0], [0, 1, 0], [0, 0, 1],
  ];
  const va = axes[ai], vb = axes[bi];
  const corners = [
    add(scale(va, -a / 2), scale(vb, -b / 2)),
    add(scale(va,  a / 2), scale(vb, -b / 2)),
    add(scale(va,  a / 2), scale(vb,  b / 2)),
    add(scale(va, -a / 2), scale(vb,  b / 2)),
    add(scale(va, -a / 2), scale(vb, -b / 2)),
  ];
  const pts = corners.map((c) => new THREE.Vector3(c[0], c[1], c[2]));
  const geo = new THREE.BufferGeometry().setFromPoints(pts);
  const mat = new THREE.LineDashedMaterial({
    color: 0x6ea8ff, dashSize: 0.015, gapSize: 0.010,
    transparent: true, opacity: 0.85,
  });
  const line = new THREE.Line(geo, mat);
  line.computeLineDistances();
  return line;
}

function add(a, b)  { return [a[0] + b[0], a[1] + b[1], a[2] + b[2]]; }
function scale(v, k) { return [v[0] * k, v[1] * k, v[2] * k]; }

// --------------------------------------------------------------------------
// draft — solid colour block, provisional material
// --------------------------------------------------------------------------

function draftOf(element, _builtMesh) {
  const group = new THREE.Group();
  group.name = `draft.${element.id}`;
  const dims = footprintFor(element);
  const block = new THREE.Mesh(
    new THREE.BoxGeometry(dims.w, dims.h, dims.d),
    new THREE.MeshStandardMaterial({
      color: 0xb8c4d6, roughness: 0.7, metalness: 0.0,
      transparent: true, opacity: 0.78,
    }),
  );
  block.position.y = dims.h / 2;
  block.castShadow = block.receiveShadow = true;
  group.add(block);

  // Crisp outline so the block reads as a placeholder, not as final geometry.
  const edges = new THREE.LineSegments(
    new THREE.EdgesGeometry(block.geometry),
    new THREE.LineBasicMaterial({ color: 0x6b7280 }),
  );
  edges.position.copy(block.position);
  group.add(edges);

  group.add(makeLabel(element.title || element.id, {
    w: Math.max(0.32, dims.w * 0.9), h: 0.05,
    yOffset: dims.h + 0.03,
    color: "#3c4250", bg: "rgba(255,255,255,0.92)",
    border: "rgba(120,140,170,0.7)",
  }));

  applyPlacement(group, element);
  return group;
}

// --------------------------------------------------------------------------
// authored — committed geometry with a subtle production seal
// --------------------------------------------------------------------------

function authoredOf(_element, builtMesh) {
  // The committed renderer already produced the correct geometry.
  // We just add a thin halo ring under it to signal "authored" — a small
  // visual seal of approval. The user can verify which elements are
  // Blender-blessed at a glance.
  if (!builtMesh) return builtMesh;
  const ring = new THREE.Mesh(
    new THREE.RingGeometry(0.18, 0.20, 48),
    new THREE.MeshBasicMaterial({
      color: 0xb56cff, transparent: true, opacity: 0.45,
      side: THREE.DoubleSide,
    }),
  );
  ring.rotation.x = -Math.PI / 2;
  ring.position.y = 0.001;
  ring.renderOrder = 10;
  builtMesh.add(ring);
  return builtMesh;
}

// --------------------------------------------------------------------------
// Helpers
// --------------------------------------------------------------------------

function footprintFor(element) {
  const s = element.placement?.sizeMeters || [];
  // PlaneGeometry-style: [w, h] for panels; BoxGeometry-style: [w, h, d]
  // for 3D. Provide sensible defaults for either case.
  const w = s[0] ?? 0.4;
  const h = (s.length >= 3 ? s[1] : s[1]) ?? 0.05;
  const d = s.length >= 3 ? (s[2] ?? 0.4) : 0.01;
  return { w: Math.max(0.05, w), h: Math.max(0.02, h), d: Math.max(0.02, d) };
}

function applyPlacement(obj, el) {
  const [x = 0, y = 0, z = 0] = el.placement?.position || [];
  obj.position.set(x, y, z);
  const [rx, ry, rz] = el.placement?.rotationDeg || [0, 0, 0];
  obj.rotation.set(
    THREE.MathUtils.degToRad(rx),
    THREE.MathUtils.degToRad(ry),
    THREE.MathUtils.degToRad(rz),
  );
}

function intentTextFor(element) {
  // The ghost label is the prompt phrase that brought this element into
  // being — provenance back to user intent. If we don't have a phrase,
  // fall back to the element's title.
  return element.sourceContent?.intentPhrase
      || element.sourceContent?.promptPhrase
      || element.title
      || element.id;
}

function makeLabel(text, opts) {
  const PX = 384;
  const cv = document.createElement("canvas");
  cv.width = Math.round(opts.w * PX);
  cv.height = Math.round(opts.h * PX);
  const ctx = cv.getContext("2d");
  const bg = opts.bg || "rgba(255,255,255,0.92)";
  const color = opts.color || "#1a1d24";
  const border = opts.border || "rgba(20,22,28,0.18)";
  // Pill background
  ctx.fillStyle = bg;
  roundRect(ctx, 0, 0, cv.width, cv.height, Math.min(cv.height * 0.5, 16));
  ctx.fill();
  ctx.strokeStyle = border;
  ctx.lineWidth = 2;
  roundRect(ctx, 1, 1, cv.width - 2, cv.height - 2, Math.min(cv.height * 0.5, 16));
  ctx.stroke();
  // Text
  ctx.fillStyle = color;
  ctx.font = `500 ${Math.round(cv.height * 0.5)}px ui-sans-serif, system-ui, sans-serif`;
  ctx.textBaseline = "middle";
  ctx.textAlign = "center";
  let display = String(text || "");
  while (display.length > 4 && ctx.measureText(display).width > cv.width - 24) {
    display = display.slice(0, -1);
  }
  if (display !== text) display = display.slice(0, -1) + "…";
  ctx.fillText(display, cv.width / 2, cv.height / 2);
  const tex = new THREE.CanvasTexture(cv);
  tex.colorSpace = THREE.SRGBColorSpace;
  const mat = new THREE.MeshBasicMaterial({
    map: tex, transparent: true, depthWrite: false,
  });
  const mesh = new THREE.Mesh(new THREE.PlaneGeometry(opts.w, opts.h), mat);
  mesh.position.set(opts.xOffset || 0, opts.yOffset || 0, opts.zOffset || 0);
  if (opts.rotateX != null) mesh.rotation.x = opts.rotateX;
  return mesh;
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
