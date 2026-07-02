// ContractPlayer — executes a spatial contract beat by beat.
// Each beat is a parallel-action group; actions inside fire concurrently
// (with optional startAt offsets), and the player waits the beat's
// `duration` (or the longest action) before moving on.

import { ExplanationCard } from "./components/explanation_card.js";
import { PartLabel } from "./components/part_label.js";

export class ContractPlayer {
  constructor({ viewer, overlay, subtitle, onProgress, onFinish }) {
    this.viewer = viewer;
    this.overlay = overlay;
    this.subtitle = subtitle;
    this.onProgress = onProgress || (() => {});
    this.onFinish = onFinish || (() => {});

    this._contract = null;
    this._activePanels = new Map();   // panel id → DOM element
    this._activeLabels = new Map();   // label id → PartLabel instance
    this._activeArrows = [];          // 3D arrow Object3Ds
    this._beatIndex = -1;
    this._abortToken = 0;
    this._labelRafHandle = null;
  }

  // ---------------------------------------------------------------
  // Lifecycle
  // ---------------------------------------------------------------

  async play(contract) {
    this._abortToken += 1;
    const myToken = this._abortToken;

    this._contract = contract;
    this._beatIndex = -1;
    this._clearOverlay();
    this._stopLabelTicker();

    // Apply initial scene state
    await this._applyScene(contract.scene || {});

    // Start the per-frame label ticker (re-projects world-anchored labels each RAF)
    this._startLabelTicker();

    // Play each beat in order
    const beats = contract.beats || [];
    for (let i = 0; i < beats.length; i++) {
      if (myToken !== this._abortToken) return;  // aborted by a newer call
      this._beatIndex = i;
      const beat = beats[i];
      this.onProgress(i, beats.length, 0, beat);
      this._showSubtitle(beat.narration);
      await this._playBeat(beat, myToken);
      this.onProgress(i, beats.length, 1, beat);
    }
    if (myToken === this._abortToken) this.onFinish();
  }

  /** Halt any in-flight play()/scrub loop and wipe per-beat overlay + subtitle.
   *  The loaded asset stays in the scene (the viewer keeps rendering); this just
   *  stops the beat sequence — used when the UI navigates away mid-walkthrough. */
  stop() {
    this._abortToken += 1;
    this._beatIndex = -1;
    this._stopLabelTicker();
    this._clearOverlay();
    this._showSubtitle(null);
  }

  async seekBeat(target, { relative = false } = {}) {
    if (!this._contract) return;
    const beats = this._contract.beats || [];
    const idx = relative
      ? Math.max(0, Math.min(beats.length - 1, this._beatIndex + target))
      : Math.max(0, Math.min(beats.length - 1, target));
    this._abortToken += 1;
    this._clearOverlay();
    await this._applyScene(this._contract.scene || {});
    for (let i = 0; i <= idx; i++) {
      this._beatIndex = i;
      const beat = beats[i];
      this._showSubtitle(beat.narration);
      const fastForward = i < idx;
      await this._playBeat(beat, this._abortToken, fastForward);
      this.onProgress(i, beats.length, 1, beat);
    }
  }

  /** Fast-forward to the END STATE of beat `idx` without waiting any beat's
   *  natural duration. Use for screenshot/validation passes where we want
   *  the visual at each beat's settle point as quickly as possible. */
  async scrubToBeat(target, contract = null) {
    const c = contract || this._contract;
    if (!c) return;
    this._contract = c;
    const beats = c.beats || [];
    const idx = Math.max(0, Math.min(beats.length - 1, target));
    this._abortToken += 1;
    this._clearOverlay();
    this._stopLabelTicker();
    await this._applyScene(c.scene || {});
    // Start the label ticker so any labels added during this scrub get
    // re-projected to screen coords each frame. Without this, labels are
    // created at (0,0) and the visual validator sees them as "floating in
    // empty space".
    this._startLabelTicker();
    for (let i = 0; i <= idx; i++) {
      this._beatIndex = i;
      const beat = beats[i];
      // Between beats during scrub, wipe per-beat overlay state (labels,
      // halos, arrows) so each beat's capture shows ONLY what that beat
      // declared. Otherwise "Crank Throw" from beat N lingers into beat
      // N+1 where the narration is about something else.
      if (i > 0) {
        this._clearOverlay();
        this.viewer.resetHighlights();  // also drop halos from prior beat
      }
      this._showSubtitle(beat.narration);
      await this._playBeat(beat, this._abortToken, /*fastForward=*/ true);
    }
    // Force-render a frame so projection runs and the panel CSS transition
    // has a chance to settle before whoever called scrubToBeat tries to
    // captureFrame().
    this.viewer.renderer.render(this.viewer.scene, this.viewer.camera);
    await this._sleep(450);  // panel opacity transition is 300ms; give some buffer
  }

  /** Navigate to beat `target` with MOTION (unlike scrubToBeat, which snaps for
   *  captures). Forward moves animate the target beat's assemble from the current
   *  state — no scene reset, so no flash. Backward moves animate the parts seated
   *  since the target back OUT, then re-show the target beat's overlay. This is
   *  what the prev/next transport and step-rail clicks should call. */
  async goToBeat(target) {
    const c = this._contract;
    if (!c) return;
    const beats = c.beats || [];
    if (!beats.length) return;
    const idx = Math.max(0, Math.min(beats.length - 1, target));
    const cur = this._beatIndex;
    if (idx === cur) return;
    this._abortToken += 1;
    const token = this._abortToken;
    this._stopLabelTicker();
    this._startLabelTicker();

    if (idx > cur) {
      // FORWARD — snap any skipped intermediate beats, animate the target.
      for (let i = cur + 1; i < idx; i++) {
        if (token !== this._abortToken) return;
        this._clearOverlay();
        this.viewer.resetHighlights();
        await this._playBeat(beats[i], token, /*fastForward=*/true);
      }
      if (token !== this._abortToken) return;
      this._clearOverlay();
      this.viewer.resetHighlights();
      this._beatIndex = idx;
      this._showSubtitle(beats[idx].narration);
      await this._playBeat(beats[idx], token, /*fastForward=*/false, /*waitDuration=*/false);
    } else {
      // BACKWARD — animate the parts seated by beats (idx+1..cur) back out, then
      // re-show the target beat's overlay (its own parts stay seated).
      const toExplode = new Set();
      for (let i = idx + 1; i <= cur; i++) {
        for (const a of (beats[i].actions || [])) {
          if (a.type !== "assemble") continue;
          const ps = a.parts ?? (a.scope === "all" ? "all" : []);
          if (ps === "all") this.viewer._assemblyIds("all").forEach((p) => toExplode.add(p));
          else (ps || []).forEach((p) => toExplode.add(p));
        }
      }
      if (toExplode.size) {
        await this.viewer.explodeParts([...toExplode], { fastForward: false, duration: 0.5 });
      }
      if (token !== this._abortToken) return;
      this._clearOverlay();
      this.viewer.resetHighlights();
      this._beatIndex = idx;
      this._showSubtitle(beats[idx].narration);
      // Replay target beat's overlay/camera + re-seat its own parts (snap).
      await this._playBeat(beats[idx], token, /*fastForward=*/true);
    }
    if (token === this._abortToken) this.onProgress(idx, beats.length, 1, beats[idx]);
  }

  // ---------------------------------------------------------------
  // Beat execution
  // ---------------------------------------------------------------

  async _playBeat(beat, myToken, fastForward = false, waitDuration = true) {
    this._beatStart = performance.now();
    const actions = beat.actions || [];

    // Each action is dispatched after its startAt offset, swallowing its own errors
    // so one bad action can't deadlock the rest of the beat.
    const dispatchPromises = actions.map(async (action) => {
      const delay = (action.startAt || 0) * 1000;
      if (delay > 0) await this._sleep(delay);
      if (myToken !== this._abortToken) return;
      try {
        return await this._dispatchAction(action, beat, fastForward);
      } catch (err) {
        console.warn(`[player] action '${action.type}' threw:`, err);
      }
    });

    if (fastForward) {
      await Promise.all(dispatchPromises);
      return;
    }

    if (!waitDuration) {
      // Interactive single-step (prev/next): run the beat's actions ANIMATED but
      // don't hold for the full narration duration — return once motion settles.
      await Promise.all(dispatchPromises);
      return;
    }

    // Run actions in parallel with a duration gate. The beat's declared
    // duration is the minimum wait time. We use setTimeout for the gate
    // (not RAF) so the player still advances when the browser throttles
    // animation frames (headless previews, background tabs).
    const beatDurMs = Math.max(1, (beat.duration || 1) * 1000);
    const durationPromise = new Promise((resolve) => {
      setTimeout(() => {
        if (myToken === this._abortToken) {
          this.onProgress(this._beatIndex, this._contract.beats.length, 1, beat);
        }
        resolve();
      }, beatDurMs);
    });

    await Promise.all([Promise.all(dispatchPromises), durationPromise]);
  }

  // ---------------------------------------------------------------
  // Action dispatch
  // ---------------------------------------------------------------

  async _dispatchAction(action, beat, fastForward = false) {
    const v = this.viewer;
    switch (action.type) {
      case "highlight":
        v.highlight(action.target, { color: action.color, intensity: action.intensity ?? 1.0 });
        break;

      case "highlight_region":
        v.highlightRegion(action.region ?? action.target, {
          color: action.color, intensity: action.intensity ?? 1.0,
        });
        break;

      case "dim_others":
        v.dimOthers({ except: action.except || [], factor: action.factor ?? 0.25 });
        break;

      case "hide":
        v.setVisible(action.target, false);
        break;

      case "show":
        v.setVisible(action.target, true);
        break;

      case "show_only":
        v.showOnly(action.target);
        break;

      case "play_animation":
        if (fastForward) {
          // Snap the mixer to the END of the requested [from, to] window so
          // each beat lands on a deterministic, range-specific pose. Just
          // racing the clip with rate:1e6 leaves every clip at its `to`,
          // which collapses the four-stroke beats to identical frames.
          v.scrubAnimation(action.animation, {
            from: action.from ?? 0,
            to: action.to ?? 1,
            position01: 1,
          });
        } else {
          await v.playAnimation(action.animation, {
            from: action.from ?? 0,
            to: action.to ?? 1,
            rate: action.rate ?? 1.0,
            loop: action.loop ?? false,
          });
        }
        break;

      case "move_camera":
        if (fastForward) {
          v.applyCameraPose(action.to);
        } else {
          await v.tweenCamera(action.to, action.duration ?? 0.9, action.ease ?? "easeInOut");
        }
        break;

      case "frame_on": {
        const pose = v.poseFor(action.target, { margin: action.margin ?? 1.8, dirHint: action.dir });
        if (!pose) { console.warn(`[player] frame_on: no resolvable target`, action.target); break; }
        if (fastForward) v.applyCameraPose(pose);
        else await v.tweenCamera(pose, action.duration ?? 0.9, action.ease ?? "easeInOut");
        break;
      }

      case "label":
        this._showLabel(action, beat);
        break;

      case "show_panel":
        this._showPanel(action, beat);
        break;

      case "hide_panel":
        this._hidePanel(action.component);
        break;

      case "arrow":
        this._showArrow(action);
        break;

      case "pulse":
        await this._pulse(action, fastForward);
        break;

      case "reset":
        this._resetScope(action.scope || "highlights");
        break;

      case "explode":
        // Move parts to their exploded entrance positions. Animate during normal
        // playback (parts visibly fly apart to "lay out"); snap on scrub/capture.
        await v.explodeParts(action.parts ?? (action.scope === "all" ? "all" : []), {
          duration: action.duration ?? 0.7,
          fastForward,
        });
        break;

      case "assemble":
        // Tween parts from their current pose into their seated rest pose.
        await v.assembleParts(action.parts ?? (action.scope === "all" ? "all" : []), {
          duration: action.duration ?? 0.9,
          fastForward,
        });
        break;

      default:
        console.warn("[player] unknown action type", action.type);
    }
  }

  _resetScope(scope) {
    const v = this.viewer;
    if (scope === "highlights" || scope === "all") v.resetHighlights();
    if (scope === "visibility" || scope === "all") { v.resetVisibility(); v.resetDim(); }
    if (scope === "camera"     || scope === "all") {/* no implicit camera reset for now */}
    if (scope === "all") this._clearOverlay();
  }

  // ---------------------------------------------------------------
  // Scene initialisation
  // ---------------------------------------------------------------

  async _applyScene(scene) {
    const v = this.viewer;
    v.resetHighlights();
    v.resetVisibility();
    v.resetDim();
    v.stopAllAnimations();

    if (scene.visibility?.hide) v.setVisible(scene.visibility.hide, false);
    if (scene.visibility?.show) v.setVisible(scene.visibility.show, true);
    // Assembly metadata (generated flat-pack assets): register per-part offsets
    // and seat everything at rest so each beat's explode/assemble is relative
    // to a known state — keeps prev/next + scrub deterministic.
    if (scene.assembly) { v.setAssembly(scene.assembly); v.resetAssembly(); }
    if (scene.camera)           v.applyCameraPose(scene.camera);
    if (scene.environment?.background) {
      try {
        v.scene.background = new (await import("three")).Color(scene.environment.background);
      } catch (_) { /* ignore */ }
    }
  }

  // ---------------------------------------------------------------
  // Overlay (HTML) helpers
  // ---------------------------------------------------------------

  _showSubtitle(text) {
    if (!text) {
      this.subtitle.classList.add("hidden");
      return;
    }
    this.subtitle.textContent = text;
    this.subtitle.classList.remove("hidden");
  }

  _showPanel(action, beat) {
    const id = action.component + (action.id ? "_" + action.id : "");
    this._hidePanel(action.component);  // one of each component at a time
    const el = ExplanationCard.create(action.props || {});
    el.classList.add("anchor-" + (action.anchor || "screen-top-right"));
    this.overlay.appendChild(el);
    requestAnimationFrame(() => el.classList.add("is-visible"));
    this._activePanels.set(id, el);
  }

  _hidePanel(component) {
    if (!component) {
      for (const el of this._activePanels.values()) el.remove();
      this._activePanels.clear();
      return;
    }
    for (const [id, el] of this._activePanels) {
      if (id.startsWith(component)) { el.remove(); this._activePanels.delete(id); }
    }
  }

  _showLabel(action, beat) {
    const target = action.target;
    const rawPos = this.viewer.getPartWorldPosition(target);
    if (!rawPos) {
      console.warn(`[player] label target not in registry: ${target}`);
      return;
    }
    // Concentric parts (e.g. a fan's frame + rotor share a center) would
    // stack their callouts on the same point, hiding all but the last. Bucket
    // labels by XZ footprint and lift each colliding one a card-height higher
    // so every part in a beat stays readable. Asset-relative via _assetDiag.
    const diag = this.viewer._assetDiag || 1;
    const partPos = rawPos.slice();
    if (!this._labelStack) this._labelStack = new Map();
    const cell = diag * 0.15;
    const key = `${Math.round(partPos[0] / cell)},${Math.round(partPos[2] / cell)}`;
    const n = this._labelStack.get(key) || 0;
    this._labelStack.set(key, n + 1);
    partPos[1] += n * diag * 0.30;
    const label = new PartLabel({
      text: action.text,
      kicker: action.kicker,
      anchor: action.anchor || "auto",
    });
    label.mount(this.overlay);
    label.setWorldPosition(partPos);
    this._activeLabels.set(target + "_" + beat.id, label);
    requestAnimationFrame(() => label.show());
    // ALSO mirror the label as a 3D canvas sprite so it survives
    // canvas.toDataURL() — the HTML overlay is invisible to that capture.
    try {
      this.viewer.addCanvasLabel(target + "_" + beat.id, partPos, {
        text: action.text,
        kicker: action.kicker,
      });
    } catch (err) {
      console.warn("[player] addCanvasLabel failed", err);
    }
  }

  _clearOverlay() {
    for (const el of this._activePanels.values()) el.remove();
    for (const lbl of this._activeLabels.values()) lbl.destroy();
    this._activePanels.clear();
    this._activeLabels.clear();
    // Also clear the canvas-mirrored labels — without this the prior beat's
    // labels would linger in screenshots even after the HTML overlays vanish.
    try { this.viewer.clearCanvasLabels?.(); } catch (_) {}
    for (const arr of this._activeArrows) {
      this.viewer.scene.remove(arr);
      arr.geometry?.dispose?.();
      arr.material?.dispose?.();
    }
    this._activeArrows = [];
    this._labelStack = new Map();
    this.subtitle.classList.add("hidden");
  }

  _showArrow(action) {
    // Lightweight 3D arrow primitive (cylinder + cone).
    import("three").then((THREE) => {
      const resolveAnchor = (a) => {
        if (Array.isArray(a)) return new THREE.Vector3(...a);
        const pos = this.viewer.getPartWorldPosition(a);
        return pos ? new THREE.Vector3(...pos) : null;
      };
      const from = resolveAnchor(action.from);
      const to   = resolveAnchor(action.to);
      if (!from || !to) return;
      const dir = new THREE.Vector3().subVectors(to, from);
      const len = dir.length();
      if (len < 0.01) return;
      const color = new THREE.Color(action.color || "#5046E5");
      const helper = new THREE.ArrowHelper(dir.normalize(), from, len, color, len * 0.18, len * 0.06);
      this.viewer.scene.add(helper);
      this._activeArrows.push(helper);
    });
  }

  async _pulse(action, fastForward) {
    if (fastForward) return;
    const cycles = action.cycles || 1;
    for (let i = 0; i < cycles; i++) {
      this.viewer.highlight(action.target, { intensity: 1.6 });
      await this._sleep(220);
      this.viewer.highlight(action.target, { intensity: 0.8 });
      await this._sleep(220);
    }
  }

  // ---------------------------------------------------------------
  // Label re-projection ticker
  // ---------------------------------------------------------------

  _startLabelTicker() {
    const tick = () => {
      for (const label of this._activeLabels.values()) {
        const wp = label.worldPosition;
        if (!wp) continue;
        const s = this.viewer.projectToScreen(wp);
        label.setScreenPosition(s.x, s.y, s.visible);
      }
      this._labelRafHandle = requestAnimationFrame(tick);
    };
    this._labelRafHandle = requestAnimationFrame(tick);
  }

  _stopLabelTicker() {
    if (this._labelRafHandle) cancelAnimationFrame(this._labelRafHandle);
    this._labelRafHandle = null;
  }

  // ---------------------------------------------------------------
  // Utility
  // ---------------------------------------------------------------

  _sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

  _sleepWithProgress(ms, onPct) {
    this._beatStart = performance.now();
    return new Promise((r) => {
      const step = () => {
        const t = performance.now() - this._beatStart;
        const pct = Math.min(1, t / Math.max(ms, 1));
        onPct(pct);
        if (t >= ms) r();
        else requestAnimationFrame(step);
      };
      requestAnimationFrame(step);
    });
  }
}
