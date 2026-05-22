// Mechanism registry — modular procedural sub-assemblies that get
// attached to anchored_callout elements so the user can SEE the part's
// mechanics, not just read a label.
//
// Each mechanism is a `build(host, opts)` function that:
//   1. Creates a Group of named child Meshes (the "parts")
//   2. Sets `userData.explodableChildren = [...children]` on the Group
//      so the explode handler can find them deterministically
//   3. Returns the Group (caller positions it)
//
// Every Mesh inside the mechanism MUST:
//   - have a sensible `name` (visible in DevTools + the Why panel later)
//   - have `userData.partRole` describing what it represents
//   - have `userData.explodable = true`
//   - have its rest position set so explode/assemble can snapshot and
//     restore it
//
// Adding a new mechanism = a new file under /viewer/mechanisms/ + one
// register() call here. Zero other code touched. That's the modularity
// contract the senior-3D-artist workflow needs: change a mechanism, the
// rest of SPATAIL doesn't care.

import { buttonMechanism } from "./button.js";
import { rotaryMechanism } from "./rotary.js";
import { paddleMechanism } from "./paddle.js";

const REGISTRY = new Map();

export function registerMechanism(key, builder) {
  REGISTRY.set(key, builder);
}

export function getMechanism(key) {
  return REGISTRY.get(key) || null;
}

// --------------------------------------------------------------------------
// Built-in mechanism types (v1 — F1 wheel controls).
// --------------------------------------------------------------------------
registerMechanism("button", buttonMechanism);
registerMechanism("rotary", rotaryMechanism);
registerMechanism("paddle", paddleMechanism);

// --------------------------------------------------------------------------
// Inference from a callout's content. Senior-artist directive: every
// labelled region picks the *best* mechanism for what it represents,
// without the card author having to spell it out. The card CAN override
// by setting `mechanismKind` on the source.
// --------------------------------------------------------------------------

export function inferMechanismKind(element) {
  const explicit = element.sourceContent?.mechanismKind;
  if (explicit && REGISTRY.has(explicit)) return explicit;

  const hay = `${element.title || ""} ${element.sourceContent?.finding || ""}`.toLowerCase();
  if (/paddle/.test(hay))             return "paddle";
  if (/rotary|dial|knob|encoder/.test(hay)) return "rotary";
  if (/release|quick|nut|spline/.test(hay)) return "rotary";   // quick-release ~ rotary feel
  if (/button|switch|trigger|press/.test(hay)) return "button";
  if (/bay|cluster|console|display/.test(hay)) return "button"; // bays = banks of buttons
  if (/grip|handle|rim/.test(hay))    return null;             // structural — no mechanism
  return "button";
}
