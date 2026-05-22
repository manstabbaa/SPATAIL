// transform_keyframes — plays a baked glTF animation track on the target.
// v0.3 minimal: if the target has an AnimationMixer attached as
// userData.mixer + a clip named params.trackName (or any clip when omitted),
// play it; otherwise no-op. Procedural inline tracks are TODO.

import * as THREE from "three";

function tokens(s) {
  return String(s || "").toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
}

export const transformKeyframesHandler = {
  name: "transform_keyframes",

  start({ target, params, duration }) {
    const mixer = target.userData?.mixer;
    const clips = target.userData?.animationClips;
    if (!mixer || !clips?.length) return { kind: "noop" };

    // The Blender glTF exporter sometimes names clips after the NLA
    // strip, sometimes after the action. Try both, then fall back to a
    // token-overlap match so a strip "seq.foo.02.explode_radial" still
    // finds a clip named "act_explode" when Blender shortened things.
    let clip = null;
    if (params?.trackName) {
      clip = THREE.AnimationClip.findByName(clips, params.trackName);
    }
    if (!clip && params?.actionName) {
      clip = THREE.AnimationClip.findByName(clips, params.actionName);
    }
    if (!clip) {
      const needle = tokens(params?.trackName) // try by tokens of either id
        .concat(tokens(params?.actionName));
      let best = null;
      let bestScore = 0;
      for (const c of clips) {
        const c_tokens = tokens(c.name);
        const overlap = needle.filter((t) => c_tokens.includes(t)).length;
        if (overlap > bestScore) { bestScore = overlap; best = c; }
      }
      if (bestScore >= 1) clip = best;
    }
    clip = clip || clips[0];
    if (!clip) return { kind: "noop" };

    const action = mixer.clipAction(clip);
    action.setLoop(params?.loop ? THREE.LoopRepeat : THREE.LoopOnce, Infinity);
    action.clampWhenFinished = true;
    action.reset().play();

    const dur = Number.isFinite(duration) ? duration : clip.duration;
    return {
      kind: "tween",
      durationSec: dur,
      tick(elapsed, dt) {
        mixer.update(dt);
      },
      finish() {
        // Leave the mixer in clamped state so the model rests at the
        // end frame instead of snapping back.
      },
    };
  },
};
