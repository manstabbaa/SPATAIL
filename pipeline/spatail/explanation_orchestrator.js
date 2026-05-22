// explanation_orchestrator.js — the four-stage pipeline.
//
//   1. analyze    — read the prompt, produce a *written* explanation
//                   (intent + what an answer needs to cover)
//   2. decompose  — pick the visual idioms (mechanics) that carry it
//                   best, with a one-line reason each
//   3. build      — resolve each mechanic to its concrete output. For
//                   v0.5 this means picking spatialElement seeds and
//                   per-mechanic params; the downstream planner takes
//                   the seeds and runs them through placement etc.
//   4. compose    — assign a presentation layout. We hard-code
//                   `stage_in_front` for now; room-aware composition
//                   is a wrapper that lands later.
//
// Today's analyze + decompose are RULE-BASED with card overrides.
// The same call surface accepts an LLM-driven implementation later
// (drop in a stage that returns the same shape). Cards that author
// `explanation` and `mechanics` directly bypass the rule stages —
// useful while we curate the demos.
//
// The orchestrator never imports the existing element generator. It
// produces seeds (mechanic outputs); experience_planner takes the
// seeds and runs them through understanding / placement / animation
// as before. Cards are the bridge: a card built from mechanics looks
// identical, downstream, to a hand-authored card.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { MECHANIC_KINDS } from "./experience_contract.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SPECS_DIR = path.resolve(__dirname, "..", "..", "spec", "mechanics", "v1");

// ----- spec registry -------------------------------------------------------

const specCache = new Map();

function loadSpec(kind) {
  if (specCache.has(kind)) return specCache.get(kind);
  const file = path.join(SPECS_DIR, `${kind}.json`);
  if (!fs.existsSync(file)) {
    specCache.set(kind, null);
    return null;
  }
  const spec = JSON.parse(fs.readFileSync(file, "utf-8"));
  specCache.set(kind, spec);
  return spec;
}

/** Resolve a chosen mechanic kind to the actual renderable one. Placeholder
 *  mechanics route to their `placeholderFor` target; if the spec is missing
 *  entirely, we emit a `placeholder_mechanic` so the gap is visible. */
export function resolveMechanicKind(kind) {
  const spec = loadSpec(kind);
  if (!spec) return { kind: "placeholder_mechanic", reason: `no spec for ${kind}` };
  if (spec.status === "shipped") return { kind, reason: "shipped" };
  if (spec.status === "placeholder" && spec.placeholderFor) {
    return { kind: spec.placeholderFor, reason: `${kind} -> ${spec.placeholderFor}` };
  }
  return { kind, reason: spec.status };
}

// ----- Stage 1: analyze ----------------------------------------------------

const RULE_BASED_INTENT = [
  // Keyword → (written, intentSummary). First match wins.
  { keywords: ["how does", "how do", "how it works"],
    writer: (p) => ({
      intentSummary: "Explain a mechanism's working principle.",
      written:
        `The prompt "${p}" is asking for an explanation of how a mechanism works ` +
        `from the inside. A good answer reveals interior structure (so the ` +
        `mechanism is visible, not just its outline), shows the moving parts in ` +
        `motion (so the user can see the timing relationships), and names the ` +
        `parts in place (so the user can tie the motion to the vocabulary).`,
    }) },
  { keywords: ["reassemble", "rebuild", "put back together", "walk me through reassembling"],
    writer: (p) => ({
      intentSummary: "Guide the user through an assembly procedure.",
      written:
        `The prompt "${p}" is asking for a guided assembly. A good answer is a ` +
        `step-by-step sequence that previews the next part as a ghost, commits ` +
        `it into place when the user advances, labels each part with its name ` +
        `+ function, and finishes with a confirming side-by-side of the ` +
        `before / after states.`,
    }) },
  { keywords: ["why did", "what drove", "why are", "spike", "increase", "decrease"],
    writer: (p) => ({
      intentSummary: "Explain a causal change in a system or metric.",
      written:
        `The prompt "${p}" is asking for a causal explanation of a measured ` +
        `change. A good answer leads with the change as a number (so the magnitude ` +
        `is unambiguous), traces it through the process that produced it (so the ` +
        `user can see WHERE in the system the change happened), shows the ` +
        `events on a timeline (so the user can see WHEN), and ends with a small ` +
        `set of options for what to do next.`,
    }) },
];

const DEFAULT_INTENT = (p) => ({
  intentSummary: "Make the prompt's subject visible and inspectable.",
  written:
    `The prompt "${p}" doesn't fit a recognised explanation pattern, so we ` +
    `fall back to the highest-leverage default: render the subject as a 3D ` +
    `object the user can inspect, with labelled callouts on its salient ` +
    `features and a short written explanation alongside.`,
});

export function analyzePrompt({ prompt, override }) {
  if (override?.written) return override;
  const lower = (prompt || "").toLowerCase();
  for (const rule of RULE_BASED_INTENT) {
    if (rule.keywords.some((kw) => lower.includes(kw))) return rule.writer(prompt);
  }
  return DEFAULT_INTENT(prompt);
}

// ----- Stage 2: decompose --------------------------------------------------

// Hand-rolled mapping from common intents to a starter set of mechanics.
// This is the "explainer's reach-for-the-shelf" step. LLM-driven decomposition
// drops in here later — same output shape: list of { kind, target, params, why }.
const DECOMP_RULES = [
  {
    when: (intent) => intent.intentSummary.startsWith("Explain a mechanism"),
    pick: (ctx) => [
      mech("ghosted_internal", ctx.heroId,
           { shellOpacity: 0.18 },
           "Reveal the interior so the moving parts are visible."),
      mech("cross_section", ctx.heroId,
           { axis: "z", offset: 0.0 },
           "Cut the body open along the working axis so the user can see the section."),
      mech("process_animation", ctx.heroId,
           { loop: true },
           "Loop the moving parts so the timing relationships read at a glance."),
      mech("annotated_callouts", ctx.heroId,
           { items: ctx.calloutItems || [] },
           "Pin the part names so the user can tie what they see to the vocabulary."),
    ],
  },
  {
    when: (intent) => intent.intentSummary.startsWith("Guide the user through an assembly"),
    pick: (ctx) => [
      mech("assembly_sequence", ctx.heroId,
           { steps: ctx.assemblySteps || [] },
           "Build the assembly step-by-step so the user can follow along."),
      mech("annotated_callouts", ctx.heroId,
           { items: ctx.calloutItems || [] },
           "Label each part with name + function as it is committed."),
      mech("before_after", ctx.heroId, {},
           "Confirm the procedure landed by comparing the start and end states."),
    ],
  },
  {
    when: (intent) => intent.intentSummary.startsWith("Explain a causal change"),
    pick: (ctx) => [
      mech("metric_dashboard", null,
           { kpis: ctx.kpis || [] },
           "Lead with the metric so the magnitude of the change is unambiguous."),
      mech("flow_diagram", null,
           { nodes: ctx.flowNodes || [], edges: ctx.flowEdges || [],
             highlightNodeId: ctx.flowHighlight || null },
           "Trace the change through the process so the user can see WHERE."),
      mech("timeline", null,
           { events: ctx.events || [] },
           "Place the events on time so the user can see WHEN."),
      mech("comparison_grid", null,
           { columns: ctx.options || [] },
           "End with the choice the user actually has to make."),
    ],
  },
];

function mech(kind, target, params, why) {
  return { kind, target: target || null, params: params || {}, why };
}

export function decomposeIntoMechanics({ intent, override, context }) {
  if (Array.isArray(override) && override.length > 0) {
    return override.map((m) => ({ ...m }));
  }
  for (const rule of DECOMP_RULES) {
    if (rule.when(intent)) return rule.pick(context || {});
  }
  // Default: render the subject + label it.
  return [
    mech("highlighted_region", context?.heroId || null, {}, "Make the subject salient."),
    mech("annotated_callouts", context?.heroId || null, {}, "Label its features."),
  ];
}

// ----- Stage 3: build ------------------------------------------------------
//
// Resolve each chosen mechanic to its concrete output. For v0.5 we:
//   1. resolve placeholder mechanics to their shipped fallback
//   2. assign each a stable id
//   3. return the list as the contract's `mechanics[]`
//
// The mechanic-to-spatialElement mapping happens in the planner — the
// orchestrator is intentionally not the place where Three.js / RealityKit
// nodes get composed.

export function buildMechanics({ chosen, context }) {
  const out = [];
  for (let i = 0; i < chosen.length; i++) {
    const m = chosen[i];
    const resolved = resolveMechanicKind(m.kind);
    out.push({
      id: `mech.${i + 1}.${m.kind}`,
      kind: resolved.kind,
      requestedKind: m.kind,
      target: m.target,
      anchorsOn: m.anchorsOn || null,
      params: m.params || {},
      why: m.why,
      placeholderRouted: resolved.kind !== m.kind,
      placeholderReason: resolved.kind !== m.kind ? resolved.reason : null,
    });
  }
  return out;
}

// ----- Stage 4: compose ----------------------------------------------------

export function composePresentation({ mechanics, override }) {
  if (override?.layout) return override;
  return {
    layout: "stage_in_front",
    ordering: mechanics.map((m) => m.id),
    note: "Default flat stage 1.5m ahead of the user. Room-aware composition is a wrapper for later.",
  };
}

// ----- One-shot: run all four stages ---------------------------------------

export function runOrchestrator({ prompt, card }) {
  const explanation = analyzePrompt({
    prompt,
    override: card?.explanation,
  });
  const context = extractContext(card);
  const chosen = decomposeIntoMechanics({
    intent: explanation,
    override: card?.mechanics,
    context,
  });
  const mechanics = buildMechanics({ chosen, context });
  const presentation = composePresentation({
    mechanics,
    override: card?.presentation,
  });
  return { explanation, mechanics, presentation };
}

/** Pull commonly-needed bits out of the card so the rule-based stages
 *  have something to work with. LLM-driven stages will pull from the
 *  same surface so the substitution stays clean. */
function extractContext(card) {
  if (!card) return {};
  const sources = card.sources || [];
  const hero = sources.find((s) => s.kind === "object3d" && s.role === "target")
    || sources.find((s) => s.kind === "object3d");
  const callouts = sources
    .filter((s) => s.kind === "object3d" && s.role === "callout")
    .map((s) => ({
      id: s.id || s.name,
      label: s.name,
      finding: s.finding,
      spotlightOnHero: s.spotlightOnHero || null,
    }));
  const kpis = sources.find((s) => s.kind === "numeric_summary")?.kpis || [];
  const events = sources.find((s) => s.kind === "timeline")?.events || [];
  return {
    heroId: hero?.id || hero?.name || null,
    calloutItems: callouts,
    kpis,
    events,
    assemblySteps: card.assemblySteps || [],
    flowNodes: card.flowNodes || [],
    flowEdges: card.flowEdges || [],
    flowHighlight: card.flowHighlight || null,
    options: card.options || [],
  };
}
