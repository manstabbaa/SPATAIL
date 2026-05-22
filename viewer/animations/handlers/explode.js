// explode — spreads the target's animatable child parts outward from the
// parent centroid along the chosen axis. See schema/animations/v1/explode.json
// for the parameter contract.
//
// Discovery order for "what is a part":
//   1. obj.userData.explodableChildren (explicit list set by the renderer)
//   2. immediate Mesh / Group children with userData.explodable = true
//   3. immediate children that are Mesh or Group nodes (fallback)
//
// Original transforms are snapshotted on the target's userData so `assemble`
// (or any reset) can return to rest without round-tripping through the contract.

import { ease } from "../easing.js";

export const explodeHandler = {
  name: "explode",

  start({ target, params, duration, easing }) {
    const parts = discoverParts(target);
    if (parts.length === 0) return { kind: "noop" };
    const snapshot = snapshotRest(target, parts);
    const spread   = numberOr(params?.spread, 0.18);
    const axis     = params?.axis || "y";
    const stagger  = numberOr(params?.stagger, 0.06);
    const dur      = numberOr(duration, 1.6);

    // Pre-compute per-part offset vectors so the per-frame tick is cheap.
    const offsets = parts.map((p, i) => offsetFor(p, i, parts.length, spread, axis));

    return {
      kind: "tween",
      durationSec: dur + stagger * (parts.length - 1),
      tick(elapsed) {
        for (let i = 0; i < parts.length; i++) {
          const local = Math.max(0, Math.min(1, (elapsed - i * stagger) / dur));
          const k = ease(easing, local);
          const rest = snapshot.rest[i];
          parts[i].position.set(
            rest.x + offsets[i].x * k,
            rest.y + offsets[i].y * k,
            rest.z + offsets[i].z * k,
          );
        }
      },
    };
  },
};

function discoverParts(target) {
  if (Array.isArray(target.userData?.explodableChildren)
      && target.userData.explodableChildren.length > 0) {
    return target.userData.explodableChildren;
  }
  // Walk DESCENDANTS (not just immediate kids) so GLB-loaded models work too.
  // We collect Mesh + Group nodes whose userData.explodable is true.
  const flagged = [];
  target.traverse((obj) => {
    if (obj === target) return;
    if (obj.userData?.explodable === true) flagged.push(obj);
  });
  if (flagged.length > 1) return flagged;

  // Fallback: take the deepest single container that holds multiple Mesh
  // children — that's typically the loaded GLB's root group.
  const groups = [];
  target.traverse((obj) => {
    const meshKids = (obj.children || []).filter((c) => c.isMesh || c.isGroup);
    if (meshKids.length >= 2) groups.push({ obj, kids: meshKids, count: meshKids.length });
  });
  if (groups.length === 0) return [];
  // Prefer the group with the most kids (closest to "the segmented assembly").
  groups.sort((a, b) => b.count - a.count);
  return groups[0].kids;
}

function snapshotRest(target, parts) {
  const cacheKey = "__explodeRestSnapshot";
  if (target.userData[cacheKey]?.parts === parts) {
    return target.userData[cacheKey];
  }
  const rest = parts.map((p) => ({ x: p.position.x, y: p.position.y, z: p.position.z }));
  target.userData[cacheKey] = { parts, rest };
  return target.userData[cacheKey];
}

function offsetFor(part, index, total, spread, axis) {
  // The offset distance is scaled by part index so successive parts
  // travel slightly farther — looks more ordered than a uniform shove.
  const dist = spread * (0.6 + (index / Math.max(1, total - 1)) * 0.6);
  if (axis === "y") return { x: 0, y: dist, z: 0 };
  if (axis === "x") return { x: dist, y: 0, z: 0 };
  if (axis === "z") return { x: 0, y: 0, z: dist };
  if (axis === "radial") {
    // Radial-from-centroid: direction = the part's resting offset from origin,
    // normalised. If part sits at origin, fall back to +Y.
    const p = part.position;
    const len = Math.hypot(p.x, p.y, p.z);
    if (len < 1e-5) return { x: 0, y: dist, z: 0 };
    return { x: p.x / len * dist, y: p.y / len * dist, z: p.z / len * dist };
  }
  return { x: 0, y: dist, z: 0 };
}

function numberOr(v, fallback) {
  return Number.isFinite(v) ? v : fallback;
}
