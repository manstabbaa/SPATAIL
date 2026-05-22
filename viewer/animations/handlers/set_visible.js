// set_visible — discrete on/off at a single beat. Duration is 0 by spec;
// the handler reports kind:"discrete" so the player advances immediately.

export const setVisibleHandler = {
  name: "set_visible",
  start({ target, params }) {
    target.visible = params?.visible !== false;
    return { kind: "discrete" };
  },
};
