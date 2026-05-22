// Rotary mechanism — brake-bias / differential rotaries on an F1 wheel.
// 12-position click-detented encoder: outer knob, indicator notch, detent
// ring with 12 bumps, encoder shaft. Stacks along +Y like the button so
// the same explode primitive works.

import * as THREE from "three";

const PALETTE = {
  knob:    { color: 0x1f2127, roughness: 0.45, metalness: 0.30 },
  indicator:{ color: 0xff6464, roughness: 0.30, metalness: 0.10 },
  detent:  { color: 0x9a9aa0, roughness: 0.30, metalness: 0.85 },
  encoder: { color: 0x2d2f36, roughness: 0.40, metalness: 0.65 },
  base:    { color: 0x0e1015, roughness: 0.60, metalness: 0.15 },
};

export function rotaryMechanism({ scale = 1.0 } = {}) {
  const group = new THREE.Group();
  group.name = "mechanism.rotary";

  const r = 0.024 * scale;
  const parts = [];

  // Base (lowest)
  const base = new THREE.Mesh(
    new THREE.CylinderGeometry(r * 1.15, r * 1.2, 0.010 * scale, 32),
    pbr(PALETTE.base),
  );
  base.name = "rotary.base";
  base.userData = { partRole: "base", explodable: true,
    description: "PCB mounting collar. Houses the rotary encoder pads and the wiring harness termination." };
  base.position.y = 0.005 * scale;
  base.castShadow = base.receiveShadow = true;
  parts.push(base);

  // Encoder shaft
  const encoder = new THREE.Mesh(
    new THREE.CylinderGeometry(r * 0.35, r * 0.35, 0.014 * scale, 24),
    pbr(PALETTE.encoder),
  );
  encoder.name = "rotary.encoder";
  encoder.userData = { partRole: "encoder", explodable: true,
    description: "Optical or magnetic 12-position encoder. Each detent reports a discrete state to the ECU, never a sweep." };
  encoder.position.y = base.position.y + 0.012 * scale;
  encoder.castShadow = encoder.receiveShadow = true;
  parts.push(encoder);

  // Detent ring
  const detentGroup = new THREE.Group();
  detentGroup.name = "rotary.detent";
  const detentRing = new THREE.Mesh(
    new THREE.TorusGeometry(r * 0.75, r * 0.05, 12, 36),
    pbr(PALETTE.detent),
  );
  detentRing.rotation.x = Math.PI / 2;
  detentGroup.add(detentRing);
  // 12 bumps around the ring make the "click" feel readable.
  for (let i = 0; i < 12; i++) {
    const a = (i / 12) * Math.PI * 2;
    const bump = new THREE.Mesh(
      new THREE.SphereGeometry(r * 0.06, 12, 12),
      pbr(PALETTE.detent),
    );
    bump.position.set(Math.cos(a) * r * 0.75, 0, Math.sin(a) * r * 0.75);
    detentGroup.add(bump);
  }
  detentGroup.userData = { partRole: "detent_ring", explodable: true,
    description: "Sprung detent ring with 12 dimples. Sets the tactile click and the gross angular resolution of the dial." };
  detentGroup.position.y = encoder.position.y + 0.010 * scale;
  detentGroup.traverse((c) => { if (c.isMesh) { c.castShadow = c.receiveShadow = true; } });
  parts.push(detentGroup);

  // Knob body
  const knob = new THREE.Mesh(
    new THREE.CylinderGeometry(r, r * 1.05, 0.018 * scale, 32),
    pbr(PALETTE.knob),
  );
  knob.name = "rotary.knob";
  knob.userData = { partRole: "knob", explodable: true,
    description: "Knurled knob. Diameter sized for a gloved finger — the driver hits it without looking down." };
  knob.position.y = detentGroup.position.y + 0.014 * scale;
  knob.castShadow = knob.receiveShadow = true;
  parts.push(knob);

  // Indicator notch (the red painted slot showing current setting)
  const indicator = new THREE.Mesh(
    new THREE.BoxGeometry(r * 0.10, 0.020 * scale + 0.001, r * 0.55),
    pbr(PALETTE.indicator),
  );
  indicator.name = "rotary.indicator";
  indicator.userData = { partRole: "indicator", explodable: true,
    description: "Painted indicator slot. The only part of the dial the driver actually reads — points to the current position number engraved on the chassis around the knob." };
  indicator.position.set(0, knob.position.y, r * 0.5);
  indicator.castShadow = indicator.receiveShadow = true;
  parts.push(indicator);

  for (const p of parts) group.add(p);
  group.userData.explodableChildren = parts;
  group.userData.mechanism = "rotary";
  group.userData.partLabels = parts.map((p) => ({
    role: p.userData.partRole,
    name: p.name,
    description: p.userData.description,
  }));
  return group;
}

function pbr({ color, roughness, metalness }) {
  return new THREE.MeshStandardMaterial({ color, roughness, metalness });
}
