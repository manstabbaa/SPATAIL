// Paddle mechanism — the paddle-shift assembly behind each grip:
// the paddle blade itself, the pivot pin, the return spring, the
// magnetic-sensor target / Hall element. Explodes along +Y so it reads
// the same as the button / rotary mechanisms when viewed in the studio.

import * as THREE from "three";

const PALETTE = {
  blade:   { color: 0x07080a, roughness: 0.32, metalness: 0.05 }, // carbon-fibre weave (dark matte)
  pivot:   { color: 0xb0b3bb, roughness: 0.20, metalness: 0.95 }, // hardened steel pin
  spring:  { color: 0xc77a2a, roughness: 0.38, metalness: 0.85 }, // brass torsion spring
  sensor:  { color: 0x14a06b, roughness: 0.35, metalness: 0.10 }, // PCB green
  magnet:  { color: 0x60667a, roughness: 0.40, metalness: 0.80 }, // neodymium grey
};

export function paddleMechanism({ scale = 1.0 } = {}) {
  const group = new THREE.Group();
  group.name = "mechanism.paddle";

  const s = scale;
  const parts = [];

  // PCB sensor (base) — a thin green slab.
  const sensor = new THREE.Mesh(
    new THREE.BoxGeometry(0.06 * s, 0.004 * s, 0.03 * s),
    pbr(PALETTE.sensor),
  );
  sensor.name = "paddle.sensor";
  sensor.userData = { partRole: "hall_sensor", explodable: true,
    description: "PCB-mounted Hall-effect sensor. Reads the magnet on the paddle, reports a 12-bit position to the ECU at 1 kHz." };
  sensor.position.y = 0.002 * s;
  sensor.castShadow = sensor.receiveShadow = true;
  parts.push(sensor);

  // Magnet target on the paddle.
  const magnet = new THREE.Mesh(
    new THREE.CylinderGeometry(0.006 * s, 0.006 * s, 0.004 * s, 18),
    pbr(PALETTE.magnet),
  );
  magnet.name = "paddle.magnet";
  magnet.userData = { partRole: "magnet", explodable: true,
    description: "Neodymium magnet bonded to the paddle. Travel = ~6 mm; the sensor sees the field change without any physical contact, hence no wear." };
  magnet.position.y = sensor.position.y + 0.010 * s;
  magnet.castShadow = magnet.receiveShadow = true;
  parts.push(magnet);

  // Torsion spring.
  const spring = new THREE.Mesh(
    new THREE.TorusGeometry(0.008 * s, 0.0012 * s, 10, 24),
    pbr(PALETTE.spring),
  );
  spring.name = "paddle.spring";
  spring.userData = { partRole: "spring", explodable: true,
    description: "Torsion return spring. Sets the paddle's resting position and the resistance the driver feels — race-tuned to a specific gram-force per millimetre." };
  spring.rotation.x = Math.PI / 2;
  spring.position.y = magnet.position.y + 0.008 * s;
  spring.castShadow = spring.receiveShadow = true;
  parts.push(spring);

  // Pivot pin.
  const pivot = new THREE.Mesh(
    new THREE.CylinderGeometry(0.0025 * s, 0.0025 * s, 0.05 * s, 12),
    pbr(PALETTE.pivot),
  );
  pivot.name = "paddle.pivot";
  pivot.userData = { partRole: "pivot_pin", explodable: true,
    description: "Hardened steel pivot pin. The paddle rotates around it; precision-ground so there's zero free play." };
  pivot.rotation.z = Math.PI / 2;
  pivot.position.y = spring.position.y + 0.005 * s;
  pivot.castShadow = pivot.receiveShadow = true;
  parts.push(pivot);

  // Paddle blade (top — what the finger touches).
  const blade = new THREE.Mesh(
    new THREE.BoxGeometry(0.07 * s, 0.005 * s, 0.022 * s),
    pbr(PALETTE.blade),
  );
  blade.name = "paddle.blade";
  blade.userData = { partRole: "blade", explodable: true,
    description: "Carbon-fibre composite blade. ~2 mm thick at the trailing edge to minimise mass; magnetic target is bonded behind the leading edge so the sensor sees motion immediately." };
  blade.position.y = pivot.position.y + 0.010 * s;
  blade.castShadow = blade.receiveShadow = true;
  parts.push(blade);

  for (const p of parts) group.add(p);
  group.userData.explodableChildren = parts;
  group.userData.mechanism = "paddle";
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
