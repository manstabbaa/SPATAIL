// camera_path — INTENTIONAL NO-OP.
//
// SPATAIL never moves the user's camera. Attention is drawn by animating
// the *object*, not by dollying the viewport. Older contracts that
// reference `camera_path` are still accepted (so the viewer doesn't
// crash), but the handler does nothing.

export const cameraPathHandler = {
  name: "camera_path",

  start() {
    return {
      kind: "noop",
      _reason: "camera_path is deprecated; SPATAIL never moves the user's camera. Use object-side primitives (explode, highlight_pulse, fade) instead.",
    };
  },
};
