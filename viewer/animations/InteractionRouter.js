// InteractionRouter — wires the contract's interactions[] (trigger ->
// actions) to viewer-side input events. Tap is the only trigger we wire
// today; hover / dwell / scene_event are surfaced through the same
// fire() entry point so the SequenceController can emit them too.
//
// Actions are dispatched against the AnimationPlayer + SequenceController
// passed in via setHosts(). The router is purely a lookup.

export class InteractionRouter {
  constructor() {
    this.interactionsById = new Map();
    this.byTapTarget = new Map(); // elementId -> [interactionId]
    this.player = null;
    this.sequence = null;
  }

  setHosts({ player, sequence }) {
    this.player = player;
    this.sequence = sequence;
  }

  loadContract(contract) {
    this.interactionsById.clear();
    this.byTapTarget.clear();
    for (const i of contract.interactions || []) {
      if (!i.id) continue;
      this.interactionsById.set(i.id, i);
      if (i.trigger?.type === "tap" && i.trigger.target) {
        const list = this.byTapTarget.get(i.trigger.target) || [];
        list.push(i.id);
        this.byTapTarget.set(i.trigger.target, list);
      }
    }
  }

  /**
   * The viewer raycaster calls this when the user taps an element. We
   * fire every interaction whose trigger matches.
   */
  onTap(elementId) {
    const ids = this.byTapTarget.get(elementId);
    if (!ids?.length) return false;
    for (const id of ids) this.fire(id);
    return true;
  }

  /**
   * Fire an interaction by id — used by the transport bar (`tap.next` /
   * `tap.previous`) and by `scene_event` triggers in general.
   */
  fire(interactionId) {
    const def = this.interactionsById.get(interactionId);
    if (!def) return;
    for (const action of def.actions || []) this.dispatch(action, def);
  }

  dispatch(action, def) {
    switch (action.type) {
      case "play_animation":
        if (action.ref) this.player?.play(action.ref);
        break;
      case "stop_animation":
        if (action.ref) this.player?.stop(action.ref);
        break;
      case "advance_step":
        this.sequence?.advance();
        break;
      case "previous_step":
        this.sequence?.previous();
        break;
      case "restart_sequence":
        this.sequence?.restart();
        break;
      case "set_visible": {
        // Lightweight equivalent of the set_visible primitive — toggles
        // a named element without going through the animation registry.
        const ent = this.sequence?.getEntity?.(action.ref);
        if (ent) ent.visible = !!action.params?.visible;
        break;
      }
      default:
        console.warn(`[InteractionRouter] unknown action type '${action.type}' on ${def.id}`);
    }
  }
}
