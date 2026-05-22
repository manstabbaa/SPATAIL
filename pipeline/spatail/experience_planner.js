// SpatialExperiencePlanner
//
// Orchestrates the four upstream layers into a single contract:
//
//   ingested card
//     -> understanding (per-source content types + relationships)
//     -> representation selection (per element)
//     -> placement (per element)
//     -> contract assembly
//
// The planner is the only place that knows about *element ids* and *order*.
// The other layers operate on single items; the planner gives every
// element its id, sequences the attention plan, and reconciles
// element-to-element references (e.g. "exploded view above target id X",
// "guide line from element A to element B").

import { understandCard } from "./understanding.js";
import { selectRepresentation } from "./representation_selector.js";
import { createLayoutState, placeElement, ROOM_DIMENSIONS } from "./placement_engine.js";
import { buildSpatialElement, buildContract } from "./experience_contract.js";
import {
  attentionBehaviorFor,
  priorityFor,
  fallbackGeometryFor,
  interactionsFor,
  buildAttentionPlan,
  summarizeReasoning,
} from "./experience_reasoning.js";
import { buildAnimationLayer } from "./animations_planner.js";
import { loadAuthoredAnimations, pickAuthoredFor, mergeAuthored, patchHeroGLB }
  from "./authored_animations.js";
import { applyRoomToElements } from "./room_aware_planner.js";
import { runOrchestrator } from "./explanation_orchestrator.js";

export function planExperience(card, {
  probedAssetGroups = [],
  normalizedAssets = new Map(),
  authoredAnimations = [],
  roomContract = null,
} = {}) {
  const understanding = understandCard(card);

  // First pass: create seeds with ids so later layers can reference each other.
  const elementSeeds = understanding.understood.map((u, i) => {
    const repr = selectRepresentation(u, understanding);
    return {
      id: idFor(u, i),
      title: u.title,
      contentType: u.contentType,
      representationMode: repr.mode,
      whyThisRepresentation: repr.reason,
      sourceContent: u.payload,
      sourceKind: u.sourceKind,
      _understandingEntry: u,
      _relatesToTargetSourceId: u._relatesToTargetId || null,
      _fromTargetSourceId: u._fromTargetRef || null,
    };
  });

  // Map source-level targetId -> elementId for cross-element resolution.
  const sourceIdToElementId = new Map();
  for (const seed of elementSeeds) {
    const targetId = seed._understandingEntry.targetId;
    if (targetId) sourceIdToElementId.set(targetId, seed.id);
  }
  for (const seed of elementSeeds) {
    if (seed._relatesToTargetSourceId) {
      seed._relatesToTargetElementId =
        sourceIdToElementId.get(seed._relatesToTargetSourceId) || null;
    }
    if (seed._fromTargetSourceId) {
      seed._fromTargetElementId =
        sourceIdToElementId.get(seed._fromTargetSourceId) || null;
    }
  }

  // Guide lines need explicit { fromElementId, toElementId } in their
  // sourceContent so the placement engine + viewer can draw them.
  for (const seed of elementSeeds) {
    if (seed.representationMode === "guide_line") {
      seed.sourceContent = {
        ...(seed.sourceContent || {}),
        fromElementId: seed._fromTargetElementId,
        toElementId: seed._relatesToTargetElementId,
      };
    }
  }

  // Second pass: place each element. Targets / process models first so
  // above_target / object_anchored / guide_line elements can resolve a
  // real position. Guide lines absolutely last — they need both endpoints
  // already placed.
  const sortedForPlacement = [...elementSeeds].sort(
    (a, b) => placementPriority(a) - placementPriority(b),
  );

  const layout = createLayoutState();
  const placedById = new Map();
  const placed = [];
  for (const seed of sortedForPlacement) {
    const placement = placeElement(seed, layout, { elementsById: placedById });
    const element = buildSpatialElement({
      id: seed.id,
      title: seed.title,
      contentType: seed.contentType,
      representationMode: seed.representationMode,
      placement: placement.placement,
      anchorStrategy: placement.anchorStrategy,
      scaleMode: placement.scaleMode,
      priority: priorityFor(seed),
      sourceContent: seed.sourceContent,
      requiredAssets: requiredAssetsFor(seed, probedAssetGroups, normalizedAssets),
      fallbackGeometry: fallbackGeometryFor(seed.representationMode),
      interactions: interactionsFor(seed),
      attentionBehavior: attentionBehaviorFor(seed),
      whyThisRepresentation: seed.whyThisRepresentation,
      whyThisPlacement: placement.whyPlacement,
    });
    placedById.set(seed.id, element);
    placed.push(element);
  }

  // Restore author's original order for the output.
  const elementOrder = new Map(elementSeeds.map((s, i) => [s.id, i]));
  placed.sort((a, b) => elementOrder.get(a.id) - elementOrder.get(b.id));

  const relationships = buildRelationships(elementSeeds);
  const attentionPlan = buildAttentionPlan(placed);
  const interactionPlan = buildInteractionPlan(placed);
  const assetRequirements = aggregateAssetRequirements(placed, probedAssetGroups);
  const reasoningSummary = summarizeReasoning(card, understanding, placed);
  const environmentAssumptions = inferEnvironment(card, understanding);

  // v0.3 — declarative animation layer. Built from the already-placed
  // elements so it can read targets, attention priorities, and reasoning
  // without re-classifying anything. Mutates `placed[]` in place to set
  // each element's `defaultAnimation` + `respondsTo`.
  let layer = buildAnimationLayer({
    elements: placed,
    attentionPlan,
    experienceId: card.id,
  });

  // Blender-authored animations win. For every card source with an
  // assetGroupRef, see if /assets_processed/animations/ has an authored
  // bundle whose assetId / targetElementId / meta best matches the card —
  // if yes, splice it in (additive for anims / interactions, replacing
  // for matching sequences).
  for (const src of card.sources || []) {
    if (!src.assetGroupRef) continue;
    const haystacks = [
      src.assetGroupRef,
      src.id,
      src.name,
      ...(placed
        .filter((e) => e.requiredAssets?.some((r) => r.id === src.assetGroupRef))
        .map((e) => e.id + " " + e.title)),
    ];
    const bundle = pickAuthoredFor(haystacks, authoredAnimations);
    if (bundle) {
      layer = mergeAuthored(layer, bundle);
      patchHeroGLB(placed, bundle);
      // Authored elements survive the room-aware re-pass as-is.
      for (const e of placed) {
        if (bundle.animations?.meta?.targetElementId === e.id) {
          e.fidelity = "authored";
        }
      }
    }
  }

  // v0.4 — room-aware resolution. If the iOS app passed its captured
  // RoomContract through, bind each element's placement.kind to a real
  // surface and record the choice as `resolvedSurface` on the element.
  // No-op when no room is supplied.
  if (roomContract) {
    const result = applyRoomToElements(placed, roomContract);
    console.log(`[planner] room-aware: ${result.resolved} resolved, ` +
                `${result.unresolved} unresolved against ${roomContract.surfaces?.length || 0} surfaces`);
  }

  const {
    animations,
    interactions: triggerInteractions,
    sequences,
    defaultSequenceId,
  } = layer;

  // v0.5 — four-stage explanation orchestrator. Runs before buildContract
  // so its output (explanation + mechanics + presentation) ships in the
  // contract alongside everything else. Cards that author these blocks
  // skip the rule-based stages; cards that don't get a defensible
  // default. Mechanics are a parallel render track to spatialElements —
  // the viewer reads contract.mechanics[] for shipped mechanic renderers
  // and continues rendering spatialElements as before.
  const orch = runOrchestrator({ prompt: card.prompt, card });

  // Source-id → element-id rewrite. The orchestrator speaks the card
  // author's source ids (steering_wheel, engine_block, …); the placed
  // elements carry generated ids (elem_steering_wheel_4). Match by
  // substring on the element id (which embeds the source id slug) and
  // by exact title match (which preserves the source's display name).
  const sourceToElement = new Map();
  const slug = (s) => String(s).toLowerCase().replace(/[^a-z0-9]+/g, "_");
  for (const src of card.sources || []) {
    if (!src.id && !src.name) continue;
    const wanted = slug(src.id || src.name);
    const hit = placed.find((e) => e.id.includes(wanted)
                                || (e.title && slug(e.title) === wanted));
    if (hit) {
      if (src.id) sourceToElement.set(src.id, hit.id);
      if (src.name) sourceToElement.set(src.name, hit.id);
    }
  }
  for (const m of orch.mechanics) {
    if (m.target && sourceToElement.has(m.target)) {
      m._sourceTarget = m.target;
      m.target = sourceToElement.get(m.target);
    }
    if (m.anchorsOn && sourceToElement.has(m.anchorsOn)) {
      m._sourceAnchorsOn = m.anchorsOn;
      m.anchorsOn = sourceToElement.get(m.anchorsOn);
    }
  }
  console.log(
    `[planner] orchestrator: ${orch.mechanics.length} mechanic(s) ` +
    `(${orch.mechanics.map((m) => m.kind).join(", ") || "—"}) ` +
    `· layout: ${orch.presentation?.layout || "—"}`,
  );

  return buildContract({
    experienceId: card.id,
    title: card.title,
    sourcePrompt: card.prompt,
    sourceInputs: card.sources.map((s) => ({
      kind: s.kind,
      key: s.key ?? null,
      title: s.title ?? s.name ?? null,
    })),
    sourceFiles: card.fileSources || [],
    detectedDomain: understanding.detectedDomain,
    environmentAssumptions,
    spatialElements: placed,
    relationships,
    interactionPlan,
    attentionPlan,
    assetRequirements,
    reasoningSummary,
    animations,
    interactions: triggerInteractions,
    sequences,
    explanation: orch.explanation,
    mechanics: orch.mechanics,
    presentation: orch.presentation,
    defaultSequenceId,
    roomContract,
  });
}

// --------------------------------------------------------------------------
// Helpers
// --------------------------------------------------------------------------

function idFor(u, i) {
  const base = (u.targetId || u.title || u.contentType || `elem_${i}`)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 40);
  return `elem_${base}_${i}`;
}

function placementPriority(seed) {
  // Lower number = placed first. Targets first so other elements can
  // anchor to them; guide lines last so both endpoints exist.
  switch (seed.contentType) {
    case "physical_target":    return 0;
    case "process_model":      return 0;
    case "tabletop_model":     return 1;
    case "assembly_explode":   return 5;
    case "anchored_marker":    return 6;
    case "diagnostic_finding": return 6;
    case "alignment_guide":    return 9;  // needs both endpoints placed
    default:                   return 3;
  }
}

function requiredAssetsFor(seed, probedAssetGroups, normalizedAssets) {
  const ref = seed.sourceContent?.assetGroupRef;
  if (!ref) return [];
  const reqs = [
    {
      id: ref,
      preferredSource: "cad_folder",
      hint: ref,
      fallback: "placeholder_box",
    },
  ];
  if (probedAssetGroups?.length) {
    // Score every probed group by how many tokens the ref shares with
    // (groupKey + sceneName). Pick the highest score, NOT the first
    // match — otherwise `f1-car` resolves to `car-engine` because both
    // contain the token "car", even though `f1-car` is an exact match.
    const refTokens = new Set(
      String(ref).toLowerCase().split(/[^a-z0-9]+/).filter(Boolean),
    );
    let bestHit = null, bestScore = 0;
    for (const g of probedAssetGroups) {
      const hay = `${g.groupKey} ${g.sceneName}`.toLowerCase()
        .split(/[^a-z0-9]+/).filter(Boolean);
      let score = 0;
      for (const t of hay) if (refTokens.has(t)) score += 1;
      if (score > bestScore) { bestScore = score; bestHit = g; }
    }
    if (bestHit) {
      reqs[0].resolvedAssetGroup = {
        groupKey: bestHit.groupKey,
        items: bestHit.items,
      };
    }
  }
  // Bake the normalized GLB path so the renderer can load real geometry
  // without re-resolving filenames. We publish a project-relative URL
  // (forward-slash, leading "/") that both the web viewer and a future
  // visionOS bundler can consume directly.
  const normalized = normalizedAssets?.get(ref);
  if (normalized?.status === "ok" && normalized.processedPath) {
    const rel = String(normalized.processedPath)
      .replace(/\\/g, "/")
      .split("/assets_processed/").pop();
    reqs[0].processedAssetPath = `/assets_processed/${rel}`;
    reqs[0].importer = normalized.importer || null;
  } else if (normalized && normalized.status !== "ok") {
    reqs[0].normalizationStatus = normalized.status;
    reqs[0].normalizationReason = normalized.reason || null;
  }
  return reqs;
}

function tokensOverlap(a, b) {
  const ta = new Set(String(a).toLowerCase().split(/[^a-z0-9]+/).filter(Boolean));
  const tb = new Set(String(b).toLowerCase().split(/[^a-z0-9]+/).filter(Boolean));
  for (const t of ta) if (tb.has(t)) return true;
  return false;
}

function buildRelationships(seeds) {
  const rels = [];
  for (const s of seeds) {
    if (s._relatesToTargetElementId) {
      let type = "relates_to";
      if (s.contentType === "assembly_explode")    type = "aligned_above";
      if (s.contentType === "diagnostic_finding")  type = "diagnoses";
      if (s.contentType === "anchored_marker")     type = "attached_to";
      if (s.contentType === "step_sequence")       type = "controls_attention_for";
      if (s.contentType === "alignment_guide")     type = "connects";
      rels.push({
        from: s.id,
        to: s._relatesToTargetElementId,
        type,
        note: noteFor(type),
      });
    }
    // Guide lines emit a SECOND relationship for the from-endpoint so the
    // graph is symmetric.
    if (s.contentType === "alignment_guide" && s._fromTargetElementId) {
      rels.push({
        from: s.id,
        to: s._fromTargetElementId,
        type: "connects",
        note: "Other endpoint of this alignment guide.",
      });
    }
  }
  return rels;
}

function noteFor(type) {
  switch (type) {
    case "aligned_above":
      return "Exploded view sits aligned directly above the target — vertical only, no tilt.";
    case "diagnoses":
      return "Finding is floated above the diagnosed part as commentary.";
    case "attached_to":
      return "Marker (clip / screw / port) is anchored on the target part itself.";
    case "controls_attention_for":
      return "These steps drive the attention plan for the target.";
    case "connects":
      return "Guide line visually connects two spatial elements.";
    default:
      return "Related element.";
  }
}

function buildInteractionPlan(elements) {
  // Promote per-element interactions to scene-level entries with
  // qualified ids — the viewer's brick dispatcher switches on type.
  const interactions = [];
  for (const e of elements) {
    for (const i of e.interactions || []) {
      interactions.push({
        id: `${e.id}::${i.id}`,
        elementId: e.id,
        type: i.type,
        behavior: i.behavior,
        trigger: "ui",
      });
    }
  }
  // Scene-wide always-available interactions.
  interactions.push({
    id: "scene::reset_view",
    elementId: null,
    type: "reset_view",
    behavior: "restore camera and every element's original state",
    trigger: "ui",
  });
  interactions.push({
    id: "scene::next_step",
    elementId: null,
    type: "next_step",
    behavior: "advance the attention plan by one step",
    trigger: "ui",
  });
  interactions.push({
    id: "scene::previous_step",
    elementId: null,
    type: "previous_step",
    behavior: "step the attention plan back by one",
    trigger: "ui",
  });
  return { interactions };
}

function aggregateAssetRequirements(elements, probedAssetGroups) {
  const reqs = [];
  const seen = new Set();
  for (const e of elements) {
    for (const r of e.requiredAssets || []) {
      if (seen.has(r.id)) continue;
      seen.add(r.id);
      reqs.push(r);
    }
  }
  if (probedAssetGroups?.length) {
    const used = new Set(reqs.map((r) => r.id));
    for (const g of probedAssetGroups) {
      if (!used.has(g.groupKey)) {
        reqs.push({
          id: g.groupKey,
          preferredSource: "cad_folder",
          fallback: "placeholder_box",
          unused: true,
          note: "Detected in /assets_raw but not referenced by this card.",
        });
      }
    }
  }
  return reqs;
}

function inferEnvironment(card, understanding) {
  if (card.environmentHint) {
    return { ...card.environmentHint, source: "card.environment" };
  }
  const d = understanding.detectedDomain.name;
  if (d === "vehicle_maintenance") {
    return {
      kind: "garage",
      surfaces: ["floor", "table", "left_of_user", "right_of_user"],
      anchorObject: "vehicle",
      roomDimensionsMeters: ROOM_DIMENSIONS,
      source: "inferred",
    };
  }
  if (d === "corporate_review") {
    return {
      kind: "boardroom",
      surfaces: ["floor", "table", "wall", "near_user"],
      anchorObject: "table",
      roomDimensionsMeters: ROOM_DIMENSIONS,
      source: "inferred",
    };
  }
  return {
    kind: "generic_workspace",
    surfaces: ["floor", "table", "wall"],
    anchorObject: "table",
    roomDimensionsMeters: ROOM_DIMENSIONS,
    source: "default",
  };
}
