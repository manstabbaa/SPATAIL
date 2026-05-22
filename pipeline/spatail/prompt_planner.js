// Prompt → spatial contract.
//
// Takes a free-text prompt + the active room + the current contract
// (which carries the wheel and its callouts), and returns a NEW contract
// the viewer should swap to. The new contract is the same scene with one
// callout (or a small handful) promoted to `active_focus` and the rest
// quieted; new "ghost" elements may appear if the prompt asks for
// something the planner thinks is missing.
//
// v0.4 is keyword-driven, by design — the architectural contract is what
// matters now, not the language model. The same module signature
// `planFromPrompt(prompt, ctx) -> contract` will later be swapped for an
// LLM call without anything downstream having to change.

const MECHANISM_HINTS = {
  brake_bias:   ["brake bias", "bbw", "brake balance", "bias"],
  diff:         ["differential", "diff", "torque diff"],
  paddle:       ["paddle", "upshift", "downshift", "shift", "gear"],
  clutch:       ["clutch", "bite point", "biting", "launch"],
  quick_release:["quick release", "remove the wheel", "egress"],
  drs:          ["drs", "drs button"],
  // drs_aero only triggers on aero-specific phrasing so it doesn't fire
  // on every "DRS" prompt — the wheel-side `drs` category is preferred
  // when the user is in the wheel context.
  drs_aero:     ["rear wing flap", "wing flap", "drs flap", "open the wing"],
  pit_limiter:  ["pit limit", "limiter", "pit speed", " pl ", "pl button"],
  pit_confirm:  ["pit confirm", " pc ", "pc button"],
  ers:          ["ers", "deploy", "harvest", "battery", "mguk", "mguh"],
  display:      ["display", "dash", "lcd", "readout", "screen"],
  radio:        ["radio", "talk", "rs button"],
  strategy:     ["strategy", "strat", "torque map", "fuel mix"],
  front_wing:   ["front wing", "nose", "front aero"],
  rear_wing:    ["rear wing", "back wing"],
  floor:        ["floor", "ground effect", "venturi", "underbody"],
  sidepod:      ["sidepod", "side pod", "radiator"],
};

// Maps an inferred mechanism category to one of the wheel's callout
// element ids. Specific buttons / rotaries are preferred over the
// generic bay / grip callouts (a "DRS" prompt should find `btn_drs`,
// not "Right Grip"). Each hint is a substring matched against the
// element id; the first match wins.
const MECHANISM_TO_CALLOUT_HINT = {
  brake_bias:    ["rot_bbw", "top_left_bay"],
  clutch:        ["top_left_bay"],
  diff:          ["rot_diff", "top_right_bay"],
  strategy:      ["rot_strat", "top_right_bay"],
  paddle:        ["left_grip", "right_grip"],
  quick_release: ["quick_release"],
  drs:           ["btn_drs", "right_grip"],
  pit_limiter:   ["btn_pl", "lower_rim"],
  pit_confirm:   ["btn_pc"],
  ers:           ["btn_drs", "right_grip"],
  display:       ["center_console"],
  radio:         ["btn_rs", "center_console"],
  // F1 aero categories.
  drs_aero:      ["mark_rear_wing_drs"],
  front_wing:    ["mark_front_wing"],
  rear_wing:     ["mark_rear_wing_drs", "mark_beam_wing"],
  floor:         ["mark_floor_tunnel"],
  sidepod:       ["mark_sidepod_inlets"],
};

export function planFromPrompt(prompt, { contract, constraints = [] }) {
  const phrase = String(prompt || "").trim();
  if (!phrase) {
    return { contract, focusElementId: null, reason: "empty prompt" };
  }
  const lower = phrase.toLowerCase();

  // 1. Pick the mechanism category from keywords. Use word-boundary
  //    matching so "drs button" doesn't accidentally trigger "rs button".
  const matchedCategories = [];
  for (const [cat, words] of Object.entries(MECHANISM_HINTS)) {
    if (words.some((w) => wordMatch(lower, w))) matchedCategories.push(cat);
  }

  // 2. Resolve those categories to callout element ids by matching the
  //    contract's element ids against the mechanism-to-callout hints.
  //    First match wins per category — that's why the most specific hint
  //    is listed first in the hint table.
  const calloutIds = new Set();
  for (const cat of matchedCategories) {
    const hint = MECHANISM_TO_CALLOUT_HINT[cat];
    if (!hint) continue;
    const hints = Array.isArray(hint) ? hint : [hint];
    for (const h of hints) {
      const id = pickElementIdByHint(contract, h);
      if (id) { calloutIds.add(id); break; }
    }
  }

  // 3. If nothing matched, fall back to text-similarity against the
  //    callout titles — every word of the prompt wins points.
  if (calloutIds.size === 0) {
    const ranked = rankCalloutsByTextSimilarity(contract, lower);
    if (ranked[0]) calloutIds.add(ranked[0].id);
  }

  // 4. Build the new contract: same elements, but `fidelity` and
  //    `attentionBehavior` rebalanced. Matched callouts go to active_focus;
  //    everything else dims to ambient. The matched callouts' source
  //    content also gains an `intentPhrase` so the ghost label (if any)
  //    cites the user's prompt.
  // Constraint pre-pass: index any fix_position constraints by element id.
  const fixedPositions = new Map();
  for (const c of constraints || []) {
    if (c?.type === "fix_position" && c.elementId && Array.isArray(c.position)) {
      fixedPositions.set(c.elementId, c.position);
    }
  }

  // Focus mode: when the prompt unambiguously matched something, strip
  // the scene to the bare minimum — hero + matched callout(s) + any
  // alignment_guide that connects them. Everything else falls to
  // attentionBehavior="on_demand" so the renderer can hide it.
  //
  // The hero stays "active_focus" because we want it visible as context;
  // the strip-down is the renderer's job (it reads attentionBehavior and
  // fades/hides accordingly).
  const focusMode = calloutIds.size > 0;
  const isHero = (el) =>
    el.contentType === "physical_target"
    || el.representationMode === "highlighted_target"
    || el.representationMode === "tabletop_model"
    || el.representationMode === "three_d_model";
  const isContextual = (el) =>
    el.representationMode === "airflow_field"
    || el.representationMode === "guide_line";

  const newElements = contract.spatialElements.map((el) => {
    const matched = calloutIds.has(el.id);
    const next = { ...el };
    if (matched) {
      next.attentionBehavior = "active_focus";
      next.fidelity = "committed";
      next.sourceContent = {
        ...(el.sourceContent || {}),
        intentPhrase: phrase,
        promptPhrase: phrase,
      };
    } else if (focusMode && isHero(el)) {
      next.attentionBehavior = "active_focus";
    } else if (focusMode && isContextual(el)) {
      next.attentionBehavior = "guiding";
    } else if (focusMode) {
      // Everything else gets stripped from view. The renderer reads
      // on_demand and hides; no clutter, no overlapping panels.
      next.attentionBehavior = "on_demand";
    } else if (el.representationMode === "anchored_callout") {
      next.attentionBehavior = "ambient";
    }
    // Honour any user-issued position constraint. The user dragged the
    // element to a new spot; the re-plan respects that override over the
    // planner's automatic placement.
    const fixed = fixedPositions.get(el.id);
    if (fixed) {
      next.placement = { ...(el.placement || {}), position: fixed };
      next.whyThisPlacement = (el.whyThisPlacement || "")
        + ` — user override: dragged to (${fixed.map((n) => n.toFixed(2)).join(", ")}).`;
    }
    return next;
  });

  const focusElementId = calloutIds.size > 0 ? [...calloutIds][0] : null;
  return {
    contract: { ...contract, spatialElements: newElements },
    focusElementId,
    matchedCategories,
    matchedCallouts: [...calloutIds],
    reason: matchedCategories.length
      ? `Matched ${matchedCategories.join(", ")} → ${[...calloutIds].join(", ") || "no callout"}`
      : `Text-only similarity match → ${[...calloutIds][0] || "no callout"}`,
  };
}

/** Word-boundary substring match — `wordMatch("drs button", "rs button")` is false. */
function wordMatch(haystack, needle) {
  if (!needle) return false;
  // Escape regex meta and require whitespace / punctuation around the needle.
  const esc = needle.replace(/[-/\\^$*+?.()|[\]{}]/g, "\\$&");
  const re = new RegExp(`(^|[^a-z0-9])${esc}([^a-z0-9]|$)`, "i");
  return re.test(haystack);
}

function pickElementIdByHint(contract, hint) {
  if (!hint) return null;
  const want = String(hint).toLowerCase();
  for (const el of contract.spatialElements) {
    const id = (el.id || "").toLowerCase();
    if (id.includes(want)) return el.id;
  }
  return null;
}

function rankCalloutsByTextSimilarity(contract, lowerPrompt) {
  const tokens = lowerPrompt.split(/[^a-z0-9]+/).filter(Boolean);
  const out = [];
  for (const el of contract.spatialElements) {
    if (el.representationMode !== "anchored_callout") continue;
    const hay = `${el.title || ""} ${el.sourceContent?.finding || ""}`.toLowerCase();
    let score = 0;
    for (const t of tokens) if (t.length >= 3 && hay.includes(t)) score += 1;
    if (score > 0) out.push({ id: el.id, score });
  }
  return out.sort((a, b) => b.score - a.score);
}
