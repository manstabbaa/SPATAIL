// fade — opacity transition across every material under the target.
// Forces transparent:true for the duration, restores the original flag.

import { ease } from "../easing.js";

export const fadeHandler = {
  name: "fade",

  start({ target, params, duration, easing }) {
    const from = clamp01(numberOr(params?.from, 0));
    const to   = clamp01(numberOr(params?.to, 1));
    const dur  = numberOr(duration, 0.6);

    const records = [];
    target.traverse((obj) => {
      if (!obj.isMesh) return;
      const mats = Array.isArray(obj.material) ? obj.material : [obj.material];
      for (const m of mats) {
        if (!m) continue;
        records.push({
          mat: m,
          wasTransparent: m.transparent,
          baseOpacity: m.opacity,
        });
        m.transparent = true;
      }
    });

    return {
      kind: "tween",
      durationSec: dur,
      tick(elapsed) {
        const k = ease(easing, Math.min(1, elapsed / dur));
        const value = from + (to - from) * k;
        for (const r of records) r.mat.opacity = value;
      },
      finish() {
        for (const r of records) {
          r.mat.opacity = to;
          if (!r.wasTransparent && to >= 1) r.mat.transparent = false;
        }
      },
    };
  },
};

function numberOr(v, fallback) { return Number.isFinite(v) ? v : fallback; }
function clamp01(v) { return Math.max(0, Math.min(1, v)); }
