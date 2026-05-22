// highlight_pulse — emissive throb on every material slot under the target.
// Falls back to a scale-up pulse when the materials don't support emissive
// (e.g. UnlitMaterial panels), so the element still reads as "attention".

import * as THREE from "three";
import { ease } from "../easing.js";

export const highlightPulseHandler = {
  name: "highlight_pulse",

  start({ target, params, duration, easing }) {
    const intensity = numberOr(params?.intensity, 1.2);
    const pulses    = Math.max(1, Math.floor(params?.pulses ?? 2));
    const dur       = numberOr(duration, 1.2);
    const colorHex  = params?.colorHex || "#6ea8ff";
    const color     = new THREE.Color(colorHex);

    // Collect emissive-capable materials; if none, mark "scale fallback".
    const mats = [];
    target.traverse((obj) => {
      if (!obj.isMesh) return;
      const list = Array.isArray(obj.material) ? obj.material : [obj.material];
      for (const m of list) {
        if (!m) continue;
        if (m.emissive) {
          mats.push({
            mat: m,
            baseIntensity: m.userData._pulseBaseI ?? m.emissiveIntensity ?? 0.0,
            baseColor: m.emissive.clone(),
          });
          m.userData._pulseBaseI = m.userData._pulseBaseI ?? m.emissiveIntensity ?? 0.0;
        }
      }
    });

    const fallbackScale = mats.length === 0;
    const baseScale = target.scale.x || 1;

    return {
      kind: "tween",
      durationSec: dur,
      tick(elapsed) {
        const t = Math.min(1, elapsed / dur);
        const k = ease(easing, t);
        // Sinusoidal pulses inside the eased envelope. Envelope = sin(πt),
        // wave = sin(2π · pulses · t). Product peaks at intensity, fades out.
        const envelope = Math.sin(Math.PI * k);
        const wave = (Math.sin(2 * Math.PI * pulses * t) + 1) * 0.5;
        const driver = envelope * wave;

        if (fallbackScale) {
          target.scale.setScalar(baseScale * (1 + 0.08 * driver));
          return;
        }
        for (const entry of mats) {
          entry.mat.emissiveIntensity = entry.baseIntensity + intensity * driver;
          // Lerp the emissive color toward the accent at the peak.
          entry.mat.emissive.copy(entry.baseColor).lerp(color, 0.7 * driver);
        }
      },
      finish() {
        if (fallbackScale) target.scale.setScalar(baseScale);
        for (const entry of mats) {
          entry.mat.emissiveIntensity = entry.baseIntensity;
          entry.mat.emissive.copy(entry.baseColor);
        }
      },
    };
  },
};

function numberOr(v, fallback) {
  return Number.isFinite(v) ? v : fallback;
}
