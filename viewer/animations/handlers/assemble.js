// assemble — reverse of explode. Drives the target's animatable child
// parts back to their snapshotted rest positions with eased motion.
// If no rest snapshot exists (target was never exploded), this is a no-op.

import { ease } from "../easing.js";

export const assembleHandler = {
  name: "assemble",

  start({ target, params, duration, easing }) {
    const snap = target.userData?.__explodeRestSnapshot;
    if (!snap || !snap.parts?.length) return { kind: "noop" };
    const parts = snap.parts;
    const rest = snap.rest;
    const current = parts.map((p) => ({ x: p.position.x, y: p.position.y, z: p.position.z }));
    const stagger = numberOr(params?.stagger, 0.04);
    const dur = numberOr(duration, 1.4);

    return {
      kind: "tween",
      durationSec: dur + stagger * (parts.length - 1),
      tick(elapsed) {
        for (let i = 0; i < parts.length; i++) {
          const local = Math.max(0, Math.min(1, (elapsed - i * stagger) / dur));
          const k = ease(easing, local);
          parts[i].position.set(
            current[i].x + (rest[i].x - current[i].x) * k,
            current[i].y + (rest[i].y - current[i].y) * k,
            current[i].z + (rest[i].z - current[i].z) * k,
          );
        }
      },
    };
  },
};

function numberOr(v, fallback) {
  return Number.isFinite(v) ? v : fallback;
}
