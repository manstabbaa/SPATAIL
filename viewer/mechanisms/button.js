// Button mechanism — the typical momentary pushbutton an F1 wheel uses
// for radio / DRS / pit-limiter / drink: cap on top, return spring, dome
// contact, housing base. Stacks along local +Y so the existing `explode`
// handler's default axis spreads the parts vertically in a way that reads
// like a cross-section.

import * as THREE from "three";

const PALETTE = {
  cap:      { color: 0xc8c8d2, roughness: 0.35, metalness: 0.10 }, // anodised plastic cap
  spring:   { color: 0xc77a2a, roughness: 0.38, metalness: 0.85 }, // brass coil
  contact:  { color: 0xd7a76b, roughness: 0.30, metalness: 0.90 }, // gold-plated dome
  housing:  { color: 0x14161a, roughness: 0.55, metalness: 0.15 }, // black plastic body
};

const PART_HEIGHT = {
  cap:     0.014,
  spring:  0.012,
  contact: 0.004,
  housing: 0.012,
};

export function buttonMechanism({ scale = 1.0, accent = null } = {}) {
  const group = new THREE.Group();
  group.name = "mechanism.button";

  const radius = 0.018 * scale;
  const parts = [];

  // Parts are added from BOTTOM to TOP so the local +Y stack is intuitive
  // and the explode-handler's index ordering produces a clean "lift off the housing" feel.
  const housing = makeHousing(radius, PART_HEIGHT.housing * scale);
  housing.name = "button.housing";
  housing.userData = { partRole: "housing", explodable: true,
    description: "Black plastic body. Carries the through-holes for the contacts and the snap-mount lugs that hold the button into the chassis." };
  housing.position.y = (PART_HEIGHT.housing * scale) / 2;
  parts.push(housing);

  const contact = makeDisc(radius * 0.7, PART_HEIGHT.contact * scale, PALETTE.contact);
  contact.name = "button.contact";
  contact.userData = { partRole: "contact", explodable: true,
    description: "Gold-plated dome contact. Inverts on press, shorts the trace pair on the PCB below. Audible click is mechanical, not synthesised." };
  contact.position.y = housing.position.y + PART_HEIGHT.housing * scale / 2
                     + PART_HEIGHT.contact * scale / 2;
  parts.push(contact);

  const spring = makeSpring(radius * 0.55, PART_HEIGHT.spring * scale);
  spring.name = "button.spring";
  spring.userData = { partRole: "spring", explodable: true,
    description: "Conical brass return spring. Sets the actuation force (~150 cN race-spec, less than a desktop keyboard)." };
  spring.position.y = contact.position.y + PART_HEIGHT.contact * scale / 2
                    + PART_HEIGHT.spring * scale / 2;
  parts.push(spring);

  const cap = makeCap(radius * 1.05, PART_HEIGHT.cap * scale, accent);
  cap.name = "button.cap";
  cap.userData = { partRole: "cap", explodable: true,
    description: "Anodised plastic cap. Surface bears the function label. Recessed into the chassis so the driver finds it by feel." };
  cap.position.y = spring.position.y + PART_HEIGHT.spring * scale / 2
                 + PART_HEIGHT.cap * scale / 2;
  parts.push(cap);

  for (const p of parts) group.add(p);

  // Explicit list — explode.js prefers this over heuristics.
  group.userData.explodableChildren = parts;
  group.userData.mechanism = "button";
  group.userData.partLabels = parts.map((p) => ({
    role: p.userData.partRole,
    name: p.name,
    description: p.userData.description,
  }));

  return group;
}

// --------------------------------------------------------------------------
// Geometry helpers
// --------------------------------------------------------------------------

function makeCap(r, h, accent) {
  const geo = new THREE.CylinderGeometry(r * 0.98, r, h, 32);
  const color = accent ? new THREE.Color(accent).getHex() : PALETTE.cap.color;
  const mat = new THREE.MeshStandardMaterial({
    color, roughness: PALETTE.cap.roughness, metalness: PALETTE.cap.metalness,
  });
  const mesh = new THREE.Mesh(geo, mat);
  mesh.castShadow = mesh.receiveShadow = true;
  return mesh;
}

function makeSpring(r, h) {
  // Use a TorusKnotGeometry as a tight helix proxy — visually unmistakable
  // as a spring without modelling actual coil turns. Cheap and reads well.
  const geo = new THREE.TorusKnotGeometry(r * 0.65, r * 0.10, 64, 8, 3, 12);
  geo.scale(1, h / (r * 1.6), 1);
  const mat = new THREE.MeshStandardMaterial({
    color: PALETTE.spring.color,
    roughness: PALETTE.spring.roughness,
    metalness: PALETTE.spring.metalness,
  });
  const mesh = new THREE.Mesh(geo, mat);
  mesh.castShadow = mesh.receiveShadow = true;
  return mesh;
}

function makeDisc(r, h, palette) {
  const geo = new THREE.CylinderGeometry(r, r * 1.04, h, 32);
  const mat = new THREE.MeshStandardMaterial({
    color: palette.color,
    roughness: palette.roughness,
    metalness: palette.metalness,
  });
  const mesh = new THREE.Mesh(geo, mat);
  mesh.castShadow = mesh.receiveShadow = true;
  return mesh;
}

function makeHousing(r, h) {
  // Hollow cylinder: outer cylinder + inner cylinder simulating the bore.
  // Threejs has no boolean primitive; for an ortho close-up the silhouette
  // and the top rim are what reads — we leave the inside as a darker cap.
  const grp = new THREE.Group();
  const outer = new THREE.Mesh(
    new THREE.CylinderGeometry(r * 1.15, r * 1.2, h, 32),
    new THREE.MeshStandardMaterial({
      color: PALETTE.housing.color,
      roughness: PALETTE.housing.roughness,
      metalness: PALETTE.housing.metalness,
    }),
  );
  outer.castShadow = outer.receiveShadow = true;
  grp.add(outer);
  const rim = new THREE.Mesh(
    new THREE.RingGeometry(r * 0.85, r * 1.13, 32),
    new THREE.MeshStandardMaterial({
      color: 0x1f2228, roughness: 0.6, side: THREE.DoubleSide,
    }),
  );
  rim.rotation.x = -Math.PI / 2;
  rim.position.y = h / 2 + 0.0005;
  grp.add(rim);
  return grp;
}
