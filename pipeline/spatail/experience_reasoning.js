// ExperienceReasoning
//
// Pure helpers that derive *explainable* per-element fields and scene-level
// reasoning artifacts (relationships, attention plan, interaction plan,
// reasoning summary) from already-classified elements.
//
// Split out from experience_planner.js so consumers — including the future
// visionOS bundler and any "explain my contract" agent — can reuse the
// reasoning logic without spinning up the whole planner.

export function attentionBehaviorFor(seed) {
  switch (seed.contentType) {
    case "physical_target":      return "active_focus";
    case "assembly_explode":     return "active_focus";
    case "diagnostic_finding":   return "on_demand";
    case "step_sequence":        return "active_focus";
    // Anchored markers (callouts) are HIDDEN by default. The hero
    // geometry needs to dominate at load time. Discovery dots on the
    // hero surface the affordances; tap or prompt promotes one to
    // active_focus with mechanism + leader line + label.
    case "anchored_marker":      return "on_demand";
    case "alignment_guide":      return "on_demand";
    case "airflow_streamlines":  return "guiding";
    case "decision_set":         return "on_demand";
    case "numeric_summary":      return "on_demand";
    case "timeline":             return "on_demand";
    // Default also hidden — the void shows ONLY the hero + the matched
    // focus content. Anything that would clutter the canvas (info panels,
    // summaries, lists) stays off until a prompt asks for it. The
    // debug drawer remains for inspection.
    default:                     return "on_demand";
  }
}

export function priorityFor(seed) {
  if (seed.contentType === "physical_target") return 90;
  if (seed.contentType === "assembly_explode") return 80;
  if (seed.contentType === "process_model") return 80;
  if (seed.contentType === "diagnostic_finding") return 78;
  if (seed.contentType === "step_sequence") return 75;
  if (seed.contentType === "anchored_marker") return 65;
  if (seed.contentType === "alignment_guide") return 60;
  if (seed.contentType === "numeric_summary") return 70;
  if (seed.contentType === "timeline") return 60;
  if (seed.contentType === "decision_set") return 55;
  return 40;
}

export function fallbackGeometryFor(mode) {
  switch (mode) {
    case "two_d_panel":
    case "wall_dashboard":
    case "floating_decision_card":
    case "anchored_callout":
    case "diagnostic_overlay":
      return "panel";
    case "tabletop_model":
    case "three_d_model":
    case "highlighted_target":
    case "exploded_view":
      return "box";
    case "floor_timeline":
      return "floor_strip";
    case "guide_line":
      return "line_segment";
    default:
      return "panel";
  }
}

export function interactionsFor(seed) {
  switch (seed.representationMode) {
    case "exploded_view":
      return [
        { id: "explode_assembly",  type: "explode",       behavior: "spread parts apart vertically along Y" },
        { id: "collapse_assembly", type: "collapse",      behavior: "collapse parts back into the assembly" },
        { id: "next_step",         type: "next_step",     behavior: "advance the attention plan to the next part" },
        { id: "previous_step",     type: "previous_step", behavior: "rewind the attention plan to the previous part" },
      ];
    case "highlighted_target":
      return [
        { id: "highlight_current_part", type: "highlight", behavior: "tint the target with the bright blue shader" },
        { id: "isolate_target_part",    type: "isolate",   behavior: "hide every other element so only the target remains" },
        { id: "tap_part_to_show_label", type: "expand",    behavior: "show a fuller label panel for this target" },
      ];
    case "tabletop_model":
    case "three_d_model":
      return [
        { id: "orbit_around",           type: "focus",  behavior: "orbit camera around the model" },
        { id: "tap_part_to_show_label", type: "expand", behavior: "open a label panel for the tapped part" },
      ];
    case "floor_timeline":
      return [
        { id: "next_step",     type: "next_step",     behavior: "advance to the next stone on the path" },
        { id: "previous_step", type: "previous_step", behavior: "step back to the previous stone" },
      ];
    case "floating_decision_card":
      return [
        { id: "select_decision", type: "select", behavior: "pick this option and advance the experience" },
      ];
    case "anchored_callout":
      return [
        { id: "tap_part_to_show_label", type: "expand", behavior: "show fuller detail about this anchored marker" },
      ];
    case "diagnostic_overlay":
      return [
        { id: "tap_part_to_show_label", type: "expand", behavior: "expand the diagnostic finding into a fuller panel" },
      ];
    case "two_d_panel":
    case "wall_dashboard":
      return [
        { id: "tap_part_to_show_label", type: "expand", behavior: "expand this panel to a fuller view" },
      ];
    case "guide_line":
      return [];
    case "airflow_field":
      return [
        { id: "toggle_regime", type: "select", behavior: "switch DRS-closed vs DRS-open airflow regime" },
      ];
    default:
      return [];
  }
}

export function narrationFor(e) {
  switch (e.contentType) {
    case "summary_panel":      return `Reference: ${e.title}.`;
    case "numeric_summary":    return `Numbers at a glance — ${e.title}.`;
    case "list":               return `${e.title}.`;
    case "step_sequence":      return `Walk through: ${e.title}.`;
    case "timeline":           return `Timeline — ${e.title}.`;
    case "decision_set":       return `Choose what to do next — ${e.title}.`;
    case "physical_target":    return `Focus on ${e.title}.`;
    case "assembly_explode":   return `Inspect the exploded assembly above the target.`;
    case "anchored_marker":    return `Marker: ${e.title}.`;
    case "diagnostic_finding": return `Finding: ${e.title}.`;
    case "alignment_guide":    return `Follow the guide line.`;
    case "process_model":      return `Walk around: ${e.title}.`;
    default:                   return `Look at ${e.title}.`;
  }
}

export function buildAttentionPlan(elements) {
  const ordered = [...elements].sort((a, b) => b.priority - a.priority);
  return ordered.map((e, i) => ({
    step: i + 1,
    focusElementId: e.id,
    narration: narrationFor(e),
  }));
}

export function summarizeReasoning(card, understanding, elements) {
  const counts = {};
  for (const e of elements) {
    counts[e.representationMode] = (counts[e.representationMode] || 0) + 1;
  }
  const pieces = [];
  pieces.push(
    `Prompt "${card.prompt}" was understood as domain ` +
    `"${understanding.detectedDomain.name}" with ` +
    `${understanding.understood.length} content items.`,
  );
  pieces.push(
    `Spatial mix: ${Object.entries(counts).map(([m, n]) => `${n}× ${m}`).join(", ")}.`,
  );
  if (counts.two_d_panel || counts.wall_dashboard) {
    pieces.push(
      "Textual and numeric content stayed as readable panels rather than " +
      "being forced into 3D widgets.",
    );
  }
  if (counts.exploded_view) {
    pieces.push(
      "Physical assemblies were rendered as exploded views aligned above " +
      "their target so part-to-position is obvious.",
    );
  }
  if (counts.diagnostic_overlay) {
    pieces.push(
      "Diagnoses were floated above the diagnosed part as commentary, not " +
      "stickers on it.",
    );
  }
  if (counts.guide_line) {
    pieces.push(
      "Guide lines visually link the exploded assembly to the real target.",
    );
  }
  if (counts.floor_timeline) {
    pieces.push(
      "Sequences were laid out as walkable floor paths instead of slide decks.",
    );
  }
  if (counts.floating_decision_card) {
    pieces.push(
      "Decisions were placed in the user's hand-reach zone for direct selection.",
    );
  }
  return pieces.join(" ");
}
