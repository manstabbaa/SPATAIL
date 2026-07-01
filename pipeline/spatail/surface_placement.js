// surface_placement.js — the "spatial concept" output of the SPATAIL brain.
//
// Given a LABELED SURFACE from surface_fusion.js (e.g. the table the user is
// looking at, with its edges + corners in world space) and a CONCEPT (what we
// want to place — e.g. foam padding for a newborn), decide the actual content
// and where it goes: strips hugging each edge, guards on each corner, and how
// MUCH is needed. Also handles the "no surface in view → spawn a stand-in the
// user positions" branch.
//
// Emits standard SpatialElements via buildSpatialElement, so the same viewer /
// iOS renderer consumes it with no special-casing beyond the two new
// surface_edge / surface_corner placement kinds.

import { buildContract, buildSpatialElement } from "./experience_contract.js";
import { _geom, surfaceGeometry } from "./surface_fusion.js";

const { add, sub, scale, normalize, dist } = _geom;

// Move a point toward the surface centroid by `d` metres (so padding hugs the
// real edge with a small inward inset instead of overhanging into empty space).
function insetToward(point, centroid, d) {
  if (!d) return point;
  const dir = normalize(sub(centroid, point));
  return add(point, scale(dir, d));
}

const round = (v, p = 3) => Math.round(v * 10 ** p) / 10 ** p;
const roundVec = (v, p = 3) => v.map((x) => round(x, p));

// ── distribute a concept along the edges + corners of a labeled surface ────
export function placeAlongEdgesAndCorners({ labeledSurface, concept }) {
  const geom = labeledSurface.geometry;
  const surfaceId = labeledSurface.surfaceId;
  const kind = labeledSurface.kind;
  const label = labeledSurface.label || kind;

  const unit = concept.unitLengthMeters ?? 0.5;
  const inset = concept.insetMeters ?? 0.0;
  const edgeDepth = concept.edgeDepthMeters ?? 0.04;
  const cornerSize = concept.cornerSizeMeters ?? 0.08;

  const elements = [];
  let totalStrips = 0;

  // one strip element per edge, carrying its own count of unit segments
  for (const edge of geom.edges) {
    const strips = Math.max(1, Math.ceil(edge.length / unit));
    totalStrips += strips;
    const from = roundVec(insetToward(edge.from, geom.centroid, inset));
    const to = roundVec(insetToward(edge.to, geom.centroid, inset));
    const mid = roundVec(insetToward(edge.midpoint, geom.centroid, inset));
    elements.push(buildSpatialElement({
      id: `${concept.id}_edge_${edge.index}`,
      title: `${concept.title} — edge ${edge.index}`,
      contentType: "physical_target",
      representationMode: "three_d_model",
      placement: {
        kind: "surface_edge",
        anchor: `edge:${surfaceId}:${edge.index}`,
        position: mid,
        from,
        to,
        surfaceRef: surfaceId,
        insetMeters: inset,
        count: strips,
        spanMeters: round(edge.length),
        sizeMeters: [round(edge.length), edgeDepth, edgeDepth],
      },
      anchorStrategy: "plane_anchor",
      scaleMode: "real_scale",
      fidelity: "ghost",
      attentionBehavior: "active_focus",
      priority: 60,
      whyThisRepresentation:
        "Foam is a real 3-D object that hugs the real edge, so it renders 1:1 " +
        "on the scanned surface — not a floating label.",
      whyThisPlacement:
        `Runs along the ${round(edge.length, 2)} m edge ${edge.index} of ` +
        `${surfaceId} (${label}) — ${strips} × ${unit} m foam strip(s). ` +
        `A newborn's head strikes the edge first, so the edge is the primary guard.`,
    }));
  }

  // one guard element per corner
  for (const corner of geom.corners) {
    const pos = roundVec(insetToward(corner.point, geom.centroid, Math.max(inset, cornerSize / 2)));
    elements.push(buildSpatialElement({
      id: `${concept.id}_corner_${corner.index}`,
      title: `${concept.title} — corner ${corner.index}`,
      contentType: "physical_target",
      representationMode: "three_d_model",
      placement: {
        kind: "surface_corner",
        anchor: `corner:${surfaceId}:${corner.index}`,
        position: pos,
        surfaceRef: surfaceId,
        insetMeters: inset,
        count: 1,
        sizeMeters: [cornerSize, cornerSize, cornerSize],
      },
      anchorStrategy: "plane_anchor",
      scaleMode: "real_scale",
      fidelity: "ghost",
      attentionBehavior: "active_focus",
      priority: 70,
      whyThisRepresentation:
        "A corner guard is a small 3-D cap seated on the real corner vertex.",
      whyThisPlacement:
        `Seated on corner ${corner.index} of ${surfaceId} (${label}) — corners ` +
        `are the sharpest impact point, so each gets a dedicated guard.`,
    }));
  }

  const perimeter = round(geom.perimeter, 2);
  const cornerCount = geom.corners.length;
  const summary = {
    mode: "anchored_to_surface",
    surfaceId,
    label,
    kind,
    confidence: round(labeledSurface.confidence ?? 0, 2),
    matchReason: labeledSurface.matchReason,
    perimeterMeters: perimeter,
    edgeStripsNeeded: totalStrips,
    unitLengthMeters: unit,
    cornerGuardsNeeded: cornerCount,
    shoppingLine:
      `~${perimeter} m of edge → ${totalStrips} × ${unit} m foam strips ` +
      `+ ${cornerCount} corner guards`,
  };
  return { spatialElements: elements, summary };
}

// ── no surface in view → spawn a stand-in the user positions ───────────────
export function spawnPrimitiveForConcept({ concept }) {
  const w = concept.spawnSizeMeters?.[0] ?? 1.2;
  const d = concept.spawnSizeMeters?.[2] ?? 0.8;
  const element = buildSpatialElement({
    id: `${concept.id}_standin`,
    title: `${concept.title} — table stand-in`,
    contentType: "physical_target",
    representationMode: "three_d_model",
    placement: {
      kind: "in_front_of_user",
      anchor: "user_front",
      position: [0, 0.75, -1.0],
      sizeMeters: [w, 0.02, d],
    },
    anchorStrategy: "user_relative",
    scaleMode: "real_scale",
    fidelity: "ghost",
    attentionBehavior: "guiding",
    priority: 40,
    whyThisRepresentation:
      "No real surface matched, so we show a table-sized slab the user can " +
      "grab and drop where the real table is.",
    whyThisPlacement:
      "Spawned ~1 m ahead at table height because the scan held no matching " +
      "surface — once the user positions it, the same edge/corner solve runs " +
      "against its footprint.",
  });
  return {
    spatialElements: [element],
    summary: {
      mode: "spawned_primitive",
      note: "No matching surface in the scan; spawned a positionable stand-in.",
      shoppingLine: "Position the stand-in, then re-run to size the padding.",
    },
  };
}

// ── top-level brain entrypoint: fuse → decide → contract ───────────────────
// (fusion is injected so this module has no import cycle with the caller)
export function planSurfaceExperience({ identification, room, pose, concept, fuse }) {
  const labeled = fuse({ identification, room, pose });
  const placed = labeled
    ? placeAlongEdgesAndCorners({ labeledSurface: labeled, concept })
    : spawnPrimitiveForConcept({ concept });

  const contract = buildContract({
    experienceId: `fusion_${concept.id}`,
    title: concept.title,
    sourcePrompt: concept.prompt || concept.title,
    detectedDomain: { name: "home_safety", confidence: "high", source: "fusion" },
    environmentAssumptions: { kind: "real_room", source: "device_scan" },
    spatialElements: placed.spatialElements,
    roomContract: room || null,
    explanation: {
      written: placed.summary.shoppingLine,
      intentSummary: concept.reason || concept.title,
    },
    reasoningSummary: labeled
      ? `Fused VLM label "${labeled.label}" onto ${labeled.surfaceId} ` +
        `(${labeled.matchReason}); ${placed.summary.shoppingLine}.`
      : `No surface matched the VLM label; spawned a stand-in.`,
  });

  return { contract, labeled, summary: placed.summary, mode: placed.summary.mode };
}

export { surfaceGeometry };
