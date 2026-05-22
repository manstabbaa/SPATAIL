// AnimationPlayer — the contract-side registry. Loads animations[] from
// the active SpatialExperienceContract, maps each `primitive` to a
// handler, and exposes play/stop/chain by animation id.
//
// Handlers are pure functions of (target Entity + params). They do not
// know about the contract. Adding a new primitive = new handler file +
// one register() call — nothing else changes.
//
// Lifecycle of an active animation:
//   start()  ── returns a `tween` { kind, durationSec, tick(elapsed,dt), finish? }
//   tick()  ── called every frame from the host render loop until elapsed > durationSec
//   finish() ── runs once after the tween completes; resolves the play() promise.

export class AnimationPlayer {
  constructor({ resolveEntity }) {
    // resolveEntity(elementId) -> THREE.Object3D | null
    this.resolveEntity = resolveEntity;
    this.handlers = new Map();
    this.animations = new Map(); // id -> { primitive, target, duration, easing, params }
    this.active = new Map();     // id -> { tween, startTime, resolve }
    this.ctx = null;             // viewer-side context passed to handlers (camera, controls, …)
  }

  register(handler) {
    if (!handler?.name || typeof handler.start !== "function") {
      throw new Error("AnimationPlayer.register: handler needs name + start()");
    }
    this.handlers.set(handler.name, handler);
  }

  setContext(ctx) { this.ctx = ctx; }

  loadContract(contract) {
    this.stopAll();
    this.animations.clear();
    for (const a of contract.animations || []) {
      if (!a.id || !a.primitive) continue;
      this.animations.set(a.id, a);
    }
  }

  /**
   * Play one animation by id. Returns a Promise that resolves when the
   * tween completes (or immediately for `discrete` / `noop`).
   */
  play(animationId) {
    if (this.active.has(animationId)) this.stop(animationId);
    const def = this.animations.get(animationId);
    if (!def) {
      console.warn(`[AnimationPlayer] unknown animation '${animationId}'`);
      return Promise.resolve();
    }
    const handler = this.handlers.get(def.primitive);
    if (!handler) {
      console.warn(`[AnimationPlayer] no handler for primitive '${def.primitive}'`);
      return Promise.resolve();
    }
    const target = this.resolveEntity(def.target);
    if (!target) {
      console.warn(`[AnimationPlayer] target element '${def.target}' not in scene yet`);
      return Promise.resolve();
    }
    const tween = handler.start({
      target,
      params: def.params || {},
      duration: def.duration,
      easing: def.easing || "ease-out-cubic",
      ctx: this.ctx,
    });
    if (!tween || tween.kind === "noop") return Promise.resolve();
    if (tween.kind === "discrete") {
      // No frames; "discrete" animations (set_visible) complete instantly.
      return Promise.resolve();
    }
    return new Promise((resolve) => {
      // Why both setTimeout AND tick(): completion MUST fire on schedule
      // even when requestAnimationFrame is throttled (backgrounded tab,
      // power-saver mode). setTimeout drives sequence advancement; tick()
      // drives the per-frame visual interpolation when frames are running.
      const startTime = performance.now() / 1000;
      const durationMs = Math.max(0, tween.durationSec) * 1000;
      const entry = { tween, startTime, resolve, animationId };
      entry.timer = setTimeout(() => this._complete(animationId), durationMs);
      this.active.set(animationId, entry);
    });
  }

  /** Internal: monotonic seconds since page load. */
  _now() { return performance.now() / 1000; }

  _complete(animationId) {
    const entry = this.active.get(animationId);
    if (!entry) return;
    // Run one final tick with elapsed == durationSec so the tween lands
    // exactly on its final values regardless of rAF throttling.
    try { entry.tween.tick(entry.tween.durationSec, 0); } catch {}
    entry.tween.finish?.();
    if (entry.timer) clearTimeout(entry.timer);
    entry.resolve?.();
    this.active.delete(animationId);
  }

  /** Play several animations in parallel; resolves when ALL finish. */
  playParallel(ids) {
    return Promise.all(ids.map((id) => this.play(id)));
  }

  /** Play several animations one after the other. */
  async chain(ids) {
    for (const id of ids) await this.play(id);
  }

  stop(animationId) {
    const entry = this.active.get(animationId);
    if (!entry) return;
    if (entry.timer) clearTimeout(entry.timer);
    entry.tween.finish?.();
    entry.resolve?.();
    this.active.delete(animationId);
  }

  stopAll() {
    for (const id of [...this.active.keys()]) this.stop(id);
  }

  /** Driven once per frame from the host render loop. Only paints frames;
   *  completion is driven by setTimeout in play() so it fires even when
   *  rAF is throttled.
   */
  tick(_nowSecIgnored, dtSec) {
    if (this.active.size === 0) return;
    const now = this._now();
    for (const [, entry] of this.active) {
      const elapsed = Math.min(now - entry.startTime, entry.tween.durationSec);
      entry.tween.tick(elapsed, dtSec);
    }
  }

  /** Diagnostic for the transport bar. */
  isPlaying() { return this.active.size > 0; }
  has(id) { return this.animations.has(id); }
}
