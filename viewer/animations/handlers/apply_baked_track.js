// apply_baked_track — replays per-frame material / light samples baked
// by the Blender exporter. This is how shader-graph node-socket
// keyframes ("Emission Strength", "Base Color", roughness …) and light
// intensity / color animations cross the bash boundary.
//
// Mapping from Blender node-socket names to Three.js material props:
//   Emission Strength  → material.emissiveIntensity
//   Emission / Emission Color → material.emissive (Color)
//   Base Color         → material.color (Color)
//   Roughness          → material.roughness
//   Metallic           → material.metalness
//
// Materials are matched by name across the scene root the target's
// part of; lights are matched by name across the whole THREE.Scene.

import * as THREE from "three";

export const applyBakedTrackHandler = {
  name: "apply_baked_track",

  start({ target, params, duration, ctx }) {
    const dur = Number.isFinite(duration) ? duration : 1.0;
    const fps = Number.isFinite(params?.fps) ? params.fps : 30;
    const frameStart = Number.isFinite(params?.frameStart) ? params.frameStart : 0;
    const matTracks = Array.isArray(params?.materials) ? params.materials : [];
    const lightTracks = Array.isArray(params?.lights) ? params.lights : [];

    // Resolve all targets once (materials are mutable — we restore on finish).
    const materialResolutions = matTracks.map((track) =>
      resolveMaterial(target, track.material, track)
    ).filter(Boolean);
    const lightResolutions = lightTracks.map((track) =>
      resolveLight(ctx?.scene, track.light, track)
    ).filter(Boolean);
    if (materialResolutions.length === 0 && lightResolutions.length === 0) {
      return { kind: "noop" };
    }

    // Snapshot for restore-on-finish so animation doesn't permanently
    // mutate the scene if the user re-presses ▶.
    const snapshot = [];
    for (const m of materialResolutions) snapshot.push(snapshotMaterial(m));
    for (const l of lightResolutions) snapshot.push(snapshotLight(l));

    return {
      kind: "tween",
      durationSec: dur,
      tick(elapsed) {
        const t = Math.max(0, Math.min(dur, elapsed));
        const f = frameStart + t * fps;
        for (const m of materialResolutions) applyMaterialAtFrame(m, f);
        for (const l of lightResolutions) applyLightAtFrame(l, f);
      },
      finish() {
        // Leave the last sampled value as the resting state — matches
        // how Blender users expect the timeline's last frame to land.
      },
    };
  },
};

// ---------------------------------------------------------------------------
// Material plumbing
// ---------------------------------------------------------------------------

function resolveMaterial(target, materialName, track) {
  const mats = [];
  target.traverse?.((obj) => {
    if (!obj.isMesh || !obj.material) return;
    const list = Array.isArray(obj.material) ? obj.material : [obj.material];
    for (const m of list) {
      if (!m) continue;
      // Match by exact name OR by case-insensitive token overlap so
      // glTF importer suffixes ("wheel_pbr.001") don't break the map.
      if (m.name === materialName || tokenOverlap(m.name, materialName)) {
        mats.push(m);
      }
    }
  });
  if (mats.length === 0) return null;
  return { materials: mats, track };
}

function snapshotMaterial(entry) {
  return {
    kind: "material",
    entry,
    saved: entry.materials.map((m) => ({
      emissiveIntensity: m.emissiveIntensity,
      emissive: m.emissive ? m.emissive.clone() : null,
      color: m.color ? m.color.clone() : null,
      roughness: m.roughness,
      metalness: m.metalness,
    })),
  };
}

function applyMaterialAtFrame(entry, frame) {
  const sample = sampleAt(entry.track.samples, frame);
  if (sample == null) return;
  for (const mat of entry.materials) {
    applyToMaterial(mat, entry.track.input, sample);
  }
}

function applyToMaterial(mat, inputName, value) {
  switch (inputName) {
    case "Emission Strength":
      if ("emissiveIntensity" in mat) mat.emissiveIntensity = numberOf(value);
      break;
    case "Emission":
    case "Emission Color":
      if (mat.emissive && Array.isArray(value)) {
        mat.emissive.setRGB(value[0] || 0, value[1] || 0, value[2] || 0);
      }
      break;
    case "Base Color":
      if (mat.color && Array.isArray(value)) {
        mat.color.setRGB(value[0] || 0, value[1] || 0, value[2] || 0);
      }
      break;
    case "Roughness":
      if ("roughness" in mat) mat.roughness = numberOf(value);
      break;
    case "Metallic":
      if ("metalness" in mat) mat.metalness = numberOf(value);
      break;
    default:
      // Unknown input — gracefully ignored (logged on first occurrence
      // to keep the console quiet).
      if (!warned[inputName]) {
        warned[inputName] = true;
        console.warn(`[apply_baked_track] no Three.js mapping for socket '${inputName}'`);
      }
  }
}

const warned = {};

// ---------------------------------------------------------------------------
// Light plumbing
// ---------------------------------------------------------------------------

function resolveLight(scene, lightName, track) {
  if (!scene) return null;
  let hit = null;
  scene.traverse((obj) => {
    if (hit || !obj.isLight) return;
    if (obj.name === lightName || tokenOverlap(obj.name, lightName)) hit = obj;
  });
  if (!hit) return null;
  return { light: hit, track };
}

function snapshotLight(entry) {
  return {
    kind: "light",
    entry,
    saved: {
      intensity: entry.light.intensity,
      color: entry.light.color ? entry.light.color.clone() : null,
    },
  };
}

function applyLightAtFrame(entry, frame) {
  const sample = sampleAt(entry.track.samples, frame);
  if (sample == null) return;
  const param = entry.track.parameter;
  if (param === "energy") {
    entry.light.intensity = numberOf(sample);
  } else if (param === "color" && Array.isArray(sample)) {
    entry.light.color.setRGB(sample[0] || 0, sample[1] || 0, sample[2] || 0);
  }
}

// ---------------------------------------------------------------------------
// Sampling
// ---------------------------------------------------------------------------

function sampleAt(samples, f) {
  if (!Array.isArray(samples) || samples.length === 0) return null;
  if (samples.length === 1) return samples[0].v;
  // Find bracketing samples (frames stored in `.f`).
  let i = 0;
  while (i < samples.length - 1 && samples[i + 1].f <= f) i++;
  const a = samples[i], b = samples[Math.min(i + 1, samples.length - 1)];
  const span = (b.f - a.f) || 1;
  const k = Math.max(0, Math.min(1, (f - a.f) / span));
  if (Array.isArray(a.v) && Array.isArray(b.v)) {
    return a.v.map((av, idx) => av + (b.v[idx] - av) * k);
  }
  return a.v + (b.v - a.v) * k;
}

function numberOf(v) { return Array.isArray(v) ? v[0] : Number(v); }
function tokenOverlap(a, b) {
  const ta = new Set(String(a || "").toLowerCase().split(/[^a-z0-9]+/).filter(Boolean));
  const tb = new Set(String(b || "").toLowerCase().split(/[^a-z0-9]+/).filter(Boolean));
  for (const t of ta) if (tb.has(t)) return true;
  return false;
}
