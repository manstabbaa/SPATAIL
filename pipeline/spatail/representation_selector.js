// RepresentationSelector
//
// Maps an understood-source entry to a representation mode + a short
// reason. This is the layer that enforces the product principle:
//
//   "Do not force everything into 3D."
//
// The decision is structural, not aesthetic — it's driven by content type
// and by simple counts from the understanding pass (e.g. a single
// decisions block becomes floating cards; many disjoint events become a
// floor timeline).

export function selectRepresentation(entry, context) {
  switch (entry.contentType) {
    case "summary_panel":
      // Numeric facts grouped with peers escalate to wall dashboards.
      if (entry.sourceKind === "fact_bucket" && entry.payload.facts.length >= 5) {
        return {
          mode: "wall_dashboard",
          reason:
            "Multiple related facts read better as one wall dashboard than " +
            "a tower of small panels.",
        };
      }
      return {
        mode: "two_d_panel",
        reason:
          "Short readable text — a 2D panel keeps it scannable without " +
          "occupying physical workspace.",
      };

    case "numeric_summary":
      return {
        mode: "wall_dashboard",
        reason:
          "KPIs are most useful when several are visible at once — a wall " +
          "dashboard groups them at glanceable scale.",
      };

    case "list":
      return {
        mode: "two_d_panel",
        reason:
          "A list is read top-to-bottom — a vertical 2D panel beats any " +
          "3D arrangement.",
      };

    case "step_sequence":
      // Repair / service steps need to stay persistent while the user
      // works — keep them as a panel rather than burying them in the
      // physical scene. Standalone procedures (no target) walk to the
      // floor timeline so the user moves through them.
      if (entry._relatesToTargetId) {
        return {
          mode: "two_d_panel",
          reason:
            "Step-by-step guidance needs to stay persistent while the user " +
            "works on the target — render as a readable panel, not buried " +
            "in the 3D scene.",
        };
      }
      return {
        mode: "floor_timeline",
        reason:
          "A standalone procedure with no anchored object becomes a walkable " +
          "floor sequence — the user moves through it.",
      };

    case "timeline":
      return {
        mode: "floor_timeline",
        reason:
          "Events over time map naturally to a path on the floor the user " +
          "can walk along.",
      };

    case "decision_set":
      return {
        mode: "floating_decision_card",
        reason:
          "A small set of next-actions belongs in the user's hand-reach " +
          "zone, not on a wall — floating selectable cards.",
      };

    case "physical_target":
      return {
        mode: "highlighted_target",
        reason:
          "The user is acting on a real / simulated object — emphasize it " +
          "in place with a bright shader so the serviced part is unmistakable, " +
          "rather than describing it in text.",
      };

    case "assembly_explode":
      return {
        mode: "exploded_view",
        reason:
          "An assembly's parts and order are best shown spread apart and " +
          "labelled, aligned directly above the real target so the part-to-" +
          "position mapping is obvious without guessing.",
      };

    case "anchored_marker":
      return {
        mode: "anchored_callout",
        reason:
          "A physical interaction point (clip, screw, port) only makes sense " +
          "when pinned directly to the part it sits on.",
      };

    case "diagnostic_finding":
      return {
        mode: "diagnostic_overlay",
        reason:
          "A diagnosis explains *why* the user is here — float it above the " +
          "target so it reads as commentary on the physical part, not as " +
          "another anchored sticker.",
      };

    case "alignment_guide":
      return {
        mode: "guide_line",
        reason:
          "A line is the cheapest way to make 'this exploded part comes from " +
          "this real spot' obvious — draw it, don't describe it.",
      };

    case "airflow_streamlines":
      return {
        mode: "airflow_field",
        reason:
          "Airflow is invisible until you draw it — animated streamlines " +
          "tracing the body are the only way to show what the air actually " +
          "does in 3D, especially when comparing regimes (DRS closed vs open).",
      };

    case "process_model":
      return {
        mode: "tabletop_model",
        reason:
          "An inspectable process / system is best as a 3D model on a table — " +
          "the user can walk around it without it dominating the room.",
      };

    default:
      return {
        mode: "two_d_panel",
        reason:
          "Unknown content type — defaulting to a readable panel rather " +
          "than forcing a 3D representation.",
      };
  }
}
