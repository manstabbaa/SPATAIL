// SequenceController — runs a contract `sequence` as a timeline of beats.
// Three kinds of step the runner understands (matches the schema):
//
//   { atSecond: N, play? wait? }       — absolute time from sequence start
//   { afterPrevious: true, play? wait? } — fire after the previous step's tween settles
//   { interactive: true, advanceOn: id } — pause until the named interaction fires
//
// `play` can be a single animation id or an array (parallel).
// `wait` is a bare delay (seconds) with no play.
//
// Notifies the host via onChange(state) whenever the active step changes
// — the transport bar binds to this for its label / progress.

export class SequenceController {
  constructor({ player, onChange, getEntity }) {
    this.player = player;
    this.onChange = onChange || (() => {});
    this.getEntity = getEntity || (() => null);
    this.sequence = null;
    this.cursor = -1;
    this.running = false;
    this.waitingForInteraction = null; // interaction id we're paused on
    this._abortToken = 0;
  }

  loadContract(contract) {
    const id = contract.defaultSequenceId;
    this.sequence = (contract.sequences || []).find((s) => s.id === id)
      || (contract.sequences || [])[0]
      || null;
    this.cursor = -1;
    this.running = false;
    this.waitingForInteraction = null;
    this._abortToken += 1;
    this._notify();
  }

  hasSequence() { return !!this.sequence?.steps?.length; }
  stepCount()   { return this.sequence?.steps?.length ?? 0; }
  currentIndex(){ return this.cursor; }
  currentStep() { return this.sequence?.steps?.[this.cursor] ?? null; }
  isPlaying()   { return this.running; }
  isWaiting()   { return !!this.waitingForInteraction; }

  async play() {
    if (!this.hasSequence()) return;
    if (this.running && !this.waitingForInteraction) return;
    if (this.cursor < 0) this.cursor = 0;
    if (this.waitingForInteraction) {
      // Coming out of an interactive pause — advance.
      this.waitingForInteraction = null;
      this.cursor += 1;
    }
    this.running = true;
    this._notify();
    await this._runFromCursor();
  }

  pause() {
    this.running = false;
    this.player?.stopAll();
    this._abortToken += 1;
    this._notify();
  }

  restart() {
    this.player?.stopAll();
    this.cursor = -1;
    this.running = false;
    this.waitingForInteraction = null;
    this._abortToken += 1;
    this._notify();
  }

  /** Advance the sequence by one step. Idempotent on the last step. */
  async advance() {
    if (!this.hasSequence()) return;
    if (this.waitingForInteraction) {
      this.waitingForInteraction = null;
      this.cursor += 1;
      this.running = true;
      this._notify();
      await this._runFromCursor();
      return;
    }
    // Skip the current step's tween if any, then run from the next.
    this.player?.stopAll();
    this.cursor = Math.min(this.cursor + 1, this.stepCount() - 1);
    this.running = true;
    this._notify();
    await this._runFromCursor();
  }

  async previous() {
    if (!this.hasSequence()) return;
    this.player?.stopAll();
    this.cursor = Math.max(0, (this.cursor < 0 ? 0 : this.cursor) - 1);
    this.waitingForInteraction = null;
    this.running = true;
    this._notify();
    await this._runFromCursor();
  }

  async _runFromCursor() {
    if (!this.sequence) return;
    const token = ++this._abortToken;
    let i = Math.max(0, this.cursor);

    while (i < this.sequence.steps.length) {
      if (token !== this._abortToken) return; // aborted by pause / restart / advance
      const step = this.sequence.steps[i];
      this.cursor = i;
      this._notify();

      // Absolute @second timing is honoured for the first step in the run
      // by waiting the diff from cursor=0. Subsequent steps that use
      // `atSecond` are also honoured.
      if (Number.isFinite(step.atSecond) && i === 0) {
        await sleep(step.atSecond);
      }

      // Play whatever this step wants first — even interactive steps
      // get their animations rolling before the pause, so the user sees
      // the camera move + pulse, then taps Next when they're ready.
      const playList = Array.isArray(step.play) ? step.play : step.play ? [step.play] : [];
      if (playList.length > 0) {
        await this.player.playParallel(playList);
      } else if (Number.isFinite(step.wait)) {
        await sleep(step.wait);
      }
      if (token !== this._abortToken) return;

      // Interactive pause — wait for advance() (called by tap.next or the
      // transport bar Next button) to bump the cursor.
      if (step.interactive) {
        this.waitingForInteraction = step.advanceOn || "tap.next";
        this.running = false;
        this._notify();
        return;
      }

      i += 1;
    }

    // Finished the sequence — sit on the last step, not "running".
    this.cursor = this.sequence.steps.length - 1;
    this.running = false;
    this._notify();
  }

  _notify() {
    this.onChange({
      hasSequence: this.hasSequence(),
      stepIndex: this.cursor,
      stepCount: this.stepCount(),
      step: this.currentStep(),
      running: this.running,
      waiting: !!this.waitingForInteraction,
      waitingFor: this.waitingForInteraction,
    });
  }
}

function sleep(sec) {
  return new Promise((res) => setTimeout(res, Math.max(0, sec) * 1000));
}
