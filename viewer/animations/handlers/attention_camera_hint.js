// attention_camera_hint — INTENTIONAL NO-OP.
//
// The product principle: the user owns the camera. Attention is drawn by
// animating the *object* (highlight_pulse, explode, fade), never by
// dollying the viewport. Older contracts may still reference this
// primitive — we leave the handler in place so they don't error, but it
// does nothing. The animations planner no longer emits this primitive
// for new contracts.

export const attentionCameraHintHandler = {
  name: "attention_camera_hint",

  start() {
    return {
      kind: "noop",
      _reason: "attention_camera_hint is deprecated; SPATAIL never moves the user's camera. Use highlight_pulse / explode / fade on the target instead.",
    };
  },
};
