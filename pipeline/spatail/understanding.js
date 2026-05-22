// SpatialUnderstandingLayer
//
// Reads the normalized card (prompt + sources) and extracts the structured
// understanding the planner needs:
//   - detected domain
//   - per-source content type
//   - lists of physical objects, readable info, processes, timelines,
//     decisions, and the relationships between them
//
// This layer is intentionally explainable: every classification carries a
// short reason string. The reasons flow through to the contract so the
// viewer can show *why* a piece of content became, say, a 2D panel.

import { inferDomain } from "../classifier.js";

const CONTENT_TYPE_BY_KIND = {
  fact: "summary_panel",
  summary: "summary_panel",
  numeric_summary: "numeric_summary",
  list: "list",
  steps: "step_sequence",
  timeline: "timeline",
  decisions: "decision_set",
  diagnostic: "diagnostic_finding",   // floats above target, explains why
  process: "process_model",
  guide: "alignment_guide",           // line connecting two elements
  // object3d is split below — target vs assembly_explode vs anchored_marker
};

export function understandCard(card) {
  const detectedDomain = detectDomain(card);
  const understood = [];
  const factBuckets = new Map(); // group consecutive facts into one panel

  for (const src of card.sources) {
    if (src.kind === "fact") {
      // Bucket facts by their optional `group` field (default "_facts")
      const bucket = src.group || "_facts";
      if (!factBuckets.has(bucket)) {
        factBuckets.set(bucket, {
          kind: "fact_bucket",
          group: bucket,
          title: src.bucketTitle || prettyTitle(bucket),
          facts: [],
          placementHint: src.placementHint || null,
          _index: src._index,
        });
      }
      const b = factBuckets.get(bucket);
      b.facts.push({ key: src.key, value: src.value });
      // First seen placementHint wins.
      if (!b.placementHint && src.placementHint) b.placementHint = src.placementHint;
      continue;
    }
    understood.push(classifyOne(src));
  }

  for (const b of factBuckets.values()) {
    understood.push({
      sourceIndex: b._index,
      sourceKind: "fact_bucket",
      title: b.title,
      contentType: "summary_panel",
      payload: { facts: b.facts, placementHint: b.placementHint },
      reason:
        `Bucketed ${b.facts.length} facts into one readable panel — ` +
        "isolated numbers don't need their own spatial widget.",
    });
  }

  // Stable sort by original position so the planner can lay things out in
  // roughly the author's order.
  understood.sort((a, b) => (a.sourceIndex ?? 0) - (b.sourceIndex ?? 0));

  // Resolve target references: every assembly / callout / diagnostic /
  // guide gets linked to its declared physical_target via `targetRef`.
  const relationships = [];
  const targetsById = new Map();
  for (const u of understood) {
    if (u.contentType === "physical_target") {
      targetsById.set(u.targetId, u);
    }
  }
  for (const u of understood) {
    const targetRef = u.payload?.targetRef;
    if (!targetRef) continue;
    const target = targetsById.get(targetRef);
    if (!target) continue;
    let type = "relates_to";
    if (u.contentType === "assembly_explode")    type = "explains_parts_of";
    if (u.contentType === "diagnostic_finding")  type = "diagnoses";
    if (u.contentType === "anchored_marker")     type = "annotates";
    if (u.contentType === "step_sequence")       type = "guides_on";
    if (u.contentType === "alignment_guide")     type = "connects";
    relationships.push({ fromTitle: u.title, toTitle: target.title, type });
    u._relatesToTargetId = targetRef;
  }

  return {
    detectedDomain,
    understood,
    relationships,
    counts: {
      physicalTargets: understood.filter((u) => u.contentType === "physical_target").length,
      assemblies: understood.filter((u) => u.contentType === "assembly_explode").length,
      stepSequences: understood.filter((u) => u.contentType === "step_sequence").length,
      timelines: understood.filter((u) => u.contentType === "timeline").length,
      decisionSets: understood.filter((u) => u.contentType === "decision_set").length,
      processModels: understood.filter((u) => u.contentType === "process_model").length,
      diagnostics: understood.filter((u) => u.contentType === "diagnostic_finding").length,
      markers: understood.filter((u) => u.contentType === "anchored_marker").length,
      guides: understood.filter((u) => u.contentType === "alignment_guide").length,
    },
  };
}

function classifyOne(src) {
  if (src.kind === "object3d") {
    return classifyObject3d(src);
  }
  if (src.kind === "guide") {
    return classifyGuide(src);
  }
  if (src.kind === "airflow") {
    return classifyAirflow(src);
  }
  const contentType = CONTENT_TYPE_BY_KIND[src.kind];
  if (!contentType) {
    return {
      sourceIndex: src._index,
      sourceKind: src.kind,
      title: src.title || src.kind,
      contentType: "summary_panel",
      payload: src,
      reason: `Unknown source kind '${src.kind}' — falling back to readable panel.`,
    };
  }
  return {
    sourceIndex: src._index,
    sourceKind: src.kind,
    title: src.title || prettyTitle(src.kind),
    contentType,
    payload: stripMeta(src),
    reason: reasonFor(src.kind, src),
  };
}

function classifyObject3d(src) {
  // `role` decides whether this is a target, an explode-able assembly, an
  // anchored marker (clip / screw / port), or a process model.
  const role = src.role || "target";
  let contentType = "physical_target";
  let reason =
    "Real physical object — must be a 3D representation, not a paragraph of text.";
  if (role === "exploded_assembly" || role === "assembly") {
    contentType = "assembly_explode";
    reason =
      "An assembly is best understood as the parts pulled apart and " +
      "labelled, not as a paragraph.";
  } else if (role === "callout" || role === "marker") {
    contentType = "anchored_marker";
    reason =
      "A physical interaction point is best shown as a marker anchored " +
      "directly on the part itself.";
  } else if (role === "process" || role === "system") {
    contentType = "process_model";
    reason =
      "A system or process is best understood as an inspectable 3D " +
      "model the user can walk around.";
  }
  return {
    sourceIndex: src._index,
    sourceKind: src.kind,
    title: src.name || src.title || "Object",
    contentType,
    targetId: src.id || src.name,
    payload: {
      name: src.name,
      role,
      assetGroupRef: src.assetGroupRef || null,
      components: src.components || [],
      targetRef: src.targetRef || null,
      finding: src.finding || null,
      placementHint: src.placementHint || null,
      // Optional anchoring hints for the renderer's focus mode. Card
      // authors point a callout at a specific point on the hero with
      // `spotlightOnHero` (local coords in the hero's frame); the
      // renderer draws a leader line + a glowing dot at that point and
      // anchors the localized explode here. `mechanismKind` overrides
      // the automatic mechanism inference (so "DRS button" forces the
      // button mechanism, not a rotary even if the bbox would suggest one).
      spotlightOnHero: src.spotlightOnHero || null,
      mechanismKind: src.mechanismKind ?? null,
    },
    reason,
  };
}

function classifyAirflow(src) {
  // Airflow stays close to the source shape; the planner doesn't need to
  // restructure it. Streams + regime stay verbatim in payload.
  return {
    sourceIndex: src._index,
    sourceKind: src.kind,
    title: src.title || "Airflow",
    contentType: "airflow_streamlines",
    targetId: src.id || null,
    _relatesToTargetId: src.targetRef || null,
    payload: {
      regime: src.regime || "default",
      description: src.description || null,
      targetRef: src.targetRef || null,
      streams: Array.isArray(src.streams) ? src.streams : [],
    },
    reason:
      "Airflow over a hero is best shown as animated streamlines tracing " +
      "the geometry — the only way to see what air actually does in 3D.",
  };
}

function classifyGuide(src) {
  // A guide references two existing element ids that the planner already
  // knows about (via fromTargetRef / toTargetRef -> targetId resolution).
  return {
    sourceIndex: src._index,
    sourceKind: src.kind,
    title: src.title || "Alignment guide",
    contentType: "alignment_guide",
    targetId: src.id || null,
    payload: {
      fromTargetRef: src.fromTargetRef,
      toTargetRef: src.toTargetRef,
      purpose: src.purpose || null,
    },
    // The understanding layer uses `targetRef` as the *primary* anchor for
    // the relationship resolver above; guides have two endpoints, so we
    // pick `toTargetRef` (the "real" target the guide points down to) as
    // the primary, and stash `fromTargetRef` for the planner to wire up.
    _relatesToTargetId: src.toTargetRef,
    _fromTargetRef: src.fromTargetRef,
    reason:
      "A guide line makes 'this part comes from this real spot' obvious — " +
      "drawn, not described.",
  };
}

function reasonFor(kind, src) {
  switch (kind) {
    case "fact":
    case "summary":
      return "Short factual / textual content — readable 2D panel is faster to scan than any 3D widget.";
    case "numeric_summary":
      return "Numbers and KPIs are read, not manipulated — a 2D dashboard panel suits them.";
    case "list":
      return "Lists belong as text — a vertical readable panel is the right format.";
    case "steps":
      return `Ordered procedure (${(src.steps || []).length} steps) — sequence matters; keep as a persistent panel beside the user, not buried in the 3D scene.`;
    case "timeline":
      return "Events occur over time — a spatial sequence on the floor makes the progression walkable.";
    case "decisions":
      return "A small set of next-actions reads naturally as floating cards the user can pick from.";
    case "diagnostic":
      return "A diagnostic finding only makes sense pinned (in fact, floated) above the thing it diagnoses.";
    case "process":
      return "A system/process is best as a 3D model the user can inspect from any angle.";
    default:
      return "Defaulting to readable panel.";
  }
}

function detectDomain(card) {
  if (card.domainHint) {
    return { name: card.domainHint, confidence: "high", source: "card.domain" };
  }
  const tokens = new Set(
    tokenize(card.prompt + " " + card.title)
      .concat(card.sources.flatMap((s) => tokenize(JSON.stringify(s)))),
  );
  const { domain, score } = inferDomain([...tokens]);
  const isMaintenance = [...tokens].some((t) =>
    ["service", "servicing", "repair", "replace", "replacement", "filter",
     "maintenance", "diagnose"].includes(t),
  );
  if (domain === "vehicle" && isMaintenance) {
    return { name: "vehicle_maintenance", confidence: "high", source: "keyword" };
  }
  const corporateHits = ["kpi", "q1", "q2", "q3", "q4", "cost", "manufacturing",
    "revenue", "review", "earnings"].filter((k) => tokens.has(k)).length;
  if (corporateHits >= 2) {
    return { name: "corporate_review", confidence: "high", source: "keyword" };
  }
  return {
    name: domain === "unknown" ? "general" : domain,
    confidence: score >= 2 ? "high" : score >= 1 ? "medium" : "low",
    source: "classifier",
  };
}

function tokenize(s) {
  return String(s || "")
    .toLowerCase()
    .replace(/[_\-.()/\\,:?!"']+/g, " ")
    .split(/\s+/)
    .filter(Boolean);
}

function prettyTitle(s) {
  return String(s).replace(/[_-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function stripMeta(src) {
  const { _index, kind, ...rest } = src;
  return rest;
}
