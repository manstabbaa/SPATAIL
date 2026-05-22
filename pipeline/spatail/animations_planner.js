// AnimationsPlanner — builds the v0.3 animations[] / interactions[] /
// sequences[] block for a contract from its already-classified elements.
//
// Two product principles to keep in mind here:
//
//   1. Sequences are first-class. Every contract with non-trivial
//      structure gets a default story for free, so any new demo
//      walks the user through itself without hand-authored choreography.
//
//   2. Strictly modular. This file only knows the *element shape* — it
//      doesn't import handlers, doesn't know how `explode` is rendered.
//      Adding a new primitive is a new spec file + new viewer handler;
//      this planner just emits the primitive name + params.
//
// Inputs:  the already-placed elements + the attention plan.
// Outputs: { animations, interactions, sequences, defaultSequenceId }.

const TAP_NEXT_ID     = "tap.next";
const TAP_PREVIOUS_ID = "tap.previous";

export function buildAnimationLayer({ elements, attentionPlan, experienceId }) {
  const animations = [];
  const interactions = [];

  // Global "advance / rewind" interactions the transport bar + tap on the
  // scene background can fire. The viewer wires these to the
  // SequenceController.
  interactions.push({
    id: TAP_NEXT_ID,
    trigger: { type: "scene_event", target: "transport_next" },
    actions: [{ type: "advance_step" }],
    note: "Fired by the transport bar Next button (and tap on the scene background).",
  });
  interactions.push({
    id: TAP_PREVIOUS_ID,
    trigger: { type: "scene_event", target: "transport_previous" },
    actions: [{ type: "previous_step" }],
    note: "Fired by the transport bar Previous button.",
  });

  // Locate well-known elements once.
  const hero = elements.find((e) => e.contentType === "physical_target");
  const explicitExploded = elements.find((e) => e.contentType === "assembly_explode");

  // When the hero loads a real (likely pre-segmented) GLB, treat IT as the
  // explodable target — the loaded model's child nodes are the parts. This
  // keeps the card declarative: an author who pre-segments the asset gets
  // explode for free, without inventing an `assembly_explode` source.
  const heroHasRealAsset = !!hero?.requiredAssets?.[0]?.processedAssetPath;
  const explodable = explicitExploded
    || (heroHasRealAsset ? hero : null);

  const tabletopModels = elements.filter((e) =>
    e.contentType === "process_model" || e.representationMode === "tabletop_model",
  );
  const callouts = elements.filter((e) =>
    e.representationMode === "anchored_callout"
    || e.contentType === "anchored_marker"
    || e.contentType === "diagnostic_finding",
  );

  // -----------------------------------------------------------------------
  // Animation library (per-element)
  // -----------------------------------------------------------------------

  // Hero pulse — quick attention beat the user sees right after the scene
  // settles, before anything else happens.
  let heroPulseId = null;
  if (hero) {
    heroPulseId = animId("pulse", hero.id);
    animations.push({
      id: heroPulseId,
      primitive: "highlight_pulse",
      target: hero.id,
      duration: 1.2,
      easing: "sine-in-out",
      params: { intensity: 1.4, pulses: 1 },
    });
    // Element-level default — if a renderer wants to autoplay something
    // when the element first activates, this is the hook.
    hero.defaultAnimation = heroPulseId;
  }

  // Assembly explode + assemble. Targets either the explicit
  // assembly_explode element (legacy) or the hero highlighted_target when
  // its GLB is pre-segmented (current Mercedes wheel flow).
  let explodeId = null, assembleId = null;
  if (explodable) {
    explodeId  = animId("explode",  explodable.id);
    assembleId = animId("assemble", explodable.id);
    animations.push({
      id: explodeId,
      primitive: "explode",
      target: explodable.id,
      duration: 1.6,
      easing: "ease-out-cubic",
      params: { spread: 0.18, axis: "y", stagger: 0.06 },
    });
    animations.push({
      id: assembleId,
      primitive: "assemble",
      target: explodable.id,
      duration: 1.4,
      easing: "ease-in-out-cubic",
      params: { stagger: 0.04 },
    });
  }

  // Tabletop / process models get a self-pulse — never a camera move. The
  // user controls the camera; the SCENE earns attention by animating itself.
  for (const t of tabletopModels) {
    if (t === explicitExploded || t === hero) continue;
    const id = animId("pulse", t.id);
    animations.push({
      id,
      primitive: "highlight_pulse",
      target: t.id,
      duration: 1.4,
      easing: "sine-in-out",
      params: { intensity: 1.3, pulses: 1 },
    });
    t.defaultAnimation = id;
  }

  // Per-callout response. Tap a callout = "explain this part" = the
  // callout panel "explodes in" (scales up + lifts toward the user) and
  // pulses; tap again = "collapse" (assemble back to rest). No camera move.
  // The viewer's explode handler operates on the callout's child nodes
  // (panel + label), which is enough to read as a small in/out animation.
  const calloutBeats = [];
  for (const c of callouts) {
    const pulseId    = animId("pulse",    c.id);
    const explodeId  = animId("explode",  c.id);
    const assembleId = animId("assemble", c.id);
    const tapId      = `tap.${c.id}`;

    animations.push({
      id: pulseId,
      primitive: "highlight_pulse",
      target: c.id,
      duration: 1.2,
      easing: "sine-in-out",
      params: { intensity: 1.6, pulses: 2 },
    });
    animations.push({
      id: explodeId,
      primitive: "explode",
      target: c.id,
      duration: 0.55,
      easing: "ease-out-cubic",
      params: { spread: 0.08, axis: "radial", stagger: 0.03 },
    });
    animations.push({
      id: assembleId,
      primitive: "assemble",
      target: c.id,
      duration: 0.45,
      easing: "ease-in-out-cubic",
      params: { stagger: 0.02 },
    });

    interactions.push({
      id: tapId,
      trigger: { type: "tap", target: c.id },
      actions: [
        // Object-side response only. Pulse for attention; explode-in to
        // signal "I'm showing you detail"; the next tap on the scene
        // (or the transport bar) assembles it back. Camera doesn't move.
        { type: "play_animation", ref: pulseId },
        { type: "play_animation", ref: explodeId },
      ],
      note: "Tap a callout to inquire about it. The callout itself pulses and explodes in; the camera stays where the user put it.",
    });

    c.defaultAnimation = pulseId;
    c.respondsTo = [tapId];
    calloutBeats.push({ pulseId, explodeId, assembleId, calloutId: c.id });
  }

  // -----------------------------------------------------------------------
  // Default sequence — fade in, hero pulse, explode, walk through callouts
  // interactively, assemble, done. Skipped entirely when there's nothing
  // to choreograph (e.g. a Q3 corporate review).
  // -----------------------------------------------------------------------

  const sequences = [];
  let defaultSequenceId = null;
  const hasStory = hero || explodable || callouts.length > 0;

  if (hasStory) {
    const seqId = `seq.${experienceId}.walkthrough`;
    const steps = [];

    steps.push({
      atSecond: 0,
      label: "Settle into the room.",
      // No animation — gives the renderer ~0.5s to fade panels in via
      // their own defaults.
      wait: 0.5,
    });

    if (heroPulseId) {
      steps.push({
        afterPrevious: true,
        label: "Look at the hero.",
        play: heroPulseId,
      });
    }

    if (explodeId) {
      steps.push({
        afterPrevious: true,
        label: "Take the assembly apart.",
        play: explodeId,
      });
    }

    for (const beat of calloutBeats) {
      steps.push({
        interactive: true,
        advanceOn: TAP_NEXT_ID,
        label: `Focus on ${nameOf(elements, beat.calloutId)}.`,
        // Object-side only: pulse + explode-in on the callout. Camera
        // stays under user control throughout the sequence.
        play: [beat.pulseId, beat.explodeId],
      });
    }

    if (assembleId) {
      steps.push({
        afterPrevious: true,
        label: "Put it back together.",
        play: assembleId,
      });
    }

    steps.push({
      afterPrevious: true,
      label: "Done — restart to walk through again.",
      wait: 0.6,
    });

    sequences.push({
      id: seqId,
      title: "Default walkthrough",
      generatedFromAttentionPlan: !!attentionPlan?.length,
      steps,
    });
    defaultSequenceId = seqId;
  }

  return { animations, interactions, sequences, defaultSequenceId };
}

function animId(verb, elementId) {
  // Stable, readable ids — `anim.<verb>.<element-id>`. Used as both the
  // ref in sequences[] and the cache key inside the AnimationPlayer.
  return `anim.${verb}.${elementId}`;
}

function nameOf(elements, id) {
  const e = elements.find((x) => x.id === id);
  return e?.title || id;
}
