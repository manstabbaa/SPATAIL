// ExperienceReasoning — public entry.
//
// Pure helpers that derive per-element reasoning fields (attentionBehavior,
// priority, fallbackGeometry, interactions, narration) and scene-level
// reasoning artifacts (attention plan, reasoning summary). Stable API,
// usable independently of the full planner.
//
// See pipeline/spatail/experience_reasoning.js for the implementation.

export {
  attentionBehaviorFor,
  priorityFor,
  fallbackGeometryFor,
  interactionsFor,
  narrationFor,
  buildAttentionPlan,
  summarizeReasoning,
} from "../../pipeline/spatail/experience_reasoning.js";
