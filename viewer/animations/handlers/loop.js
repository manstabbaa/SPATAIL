// loop — plays a wrapped track on repeat for the step's duration. The
// most common wrapper today is `wraps: "gltf_track"` which restarts
// the named glTF animation clip on the target's AnimationMixer.
//
// When the wrapped track is missing, the handler degrades to a no-op
// — important because authored sequences shouldn't break on assets
// that were imported without animation tracks.

import * as THREE from "three";

export const loopHandler = {
  name: "loop",

  start({ target, params, duration }) {
    const dur = Number.isFinite(duration) ? duration : 5.0;
    if (params?.wraps !== "gltf_track") return { kind: "noop" };

    const mixer = target.userData?.mixer;
    const clips = target.userData?.animationClips;
    if (!mixer || !clips?.length) return { kind: "noop" };

    let clip = null;
    if (params?.trackName) {
      clip = THREE.AnimationClip.findByName(clips, params.trackName);
    }
    clip = clip || clips[0];
    if (!clip) return { kind: "noop" };

    const action = mixer.clipAction(clip);
    action.setLoop(THREE.LoopRepeat, Infinity);
    action.reset().play();

    return {
      kind: "tween",
      durationSec: dur,
      tick(_elapsed, dt) {
        mixer.update(dt || 0);
      },
      finish() {
        action.stop();
      },
    };
  },
};
