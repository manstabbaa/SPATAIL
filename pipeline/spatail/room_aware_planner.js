// room_aware_planner.js
//
// Takes the placed spatial elements + an optional RoomContract, and:
//   1. Picks a real surface for every element whose placement.kind binds
//      to one (wall, table, floor, ceiling, object_anchored, above_target).
//   2. Writes that choice onto `element.resolvedSurface` and *augments*
//      `element.whyThisPlacement` with a one-line surface citation.
//   3. Initial fidelity = "ghost" for elements whose surface was just
//      resolved this pass; "draft" for elements that don't bind to a
//      surface (eg near_user); preserves "authored" / "committed" if
//      something upstream set them.
//
// No-op when no room is supplied — the existing default placements (and
// the planner's whyThisPlacement copy) stand untouched.

const KIND_BIND = {
  wall:              ["wall"],
  table:             ["table"],
  floor:             ["floor"],
  ceiling:           ["ceiling"],
  // Object-anchored & above_target ride on whichever surface the user's
  // target sits on. Closest table → fall back to floor.
  object_anchored:   ["table", "floor"],
  above_target:      ["table", "floor"],
  // Hand-reach / user-relative don't bind to a surface, but we still
  // sanity-clip them to inside the room's bbox.
  near_user:         [],
  in_front_of_user:  [],
  left_of_user:      ["wall"],
  right_of_user:     ["wall"],
  near_presenter:    [],
  room_center:       ["floor"],
};

export function applyRoomToElements(elements, room) {
  if (!room || !Array.isArray(room.surfaces) || room.surfaces.length === 0) {
    // Nothing to bind against — still set a reasonable fidelity default.
    for (const el of elements) {
      if (el.fidelity === undefined || el.fidelity === null) el.fidelity = "draft";
    }
    return { unresolved: elements.length, resolved: 0 };
  }

  // Pre-index by kind for cheap lookup.
  const byKind = new Map();
  for (const s of room.surfaces) {
    if (!byKind.has(s.kind)) byKind.set(s.kind, []);
    byKind.get(s.kind).push(s);
  }

  let resolved = 0, unresolved = 0;
  for (const el of elements) {
    if (el.fidelity === "authored") {
      // Don't second-guess the Blender authoring pass. Anything else —
      // even default "committed" from buildSpatialElement — is fair game
      // to re-anchor against a real room surface.
      continue;
    }
    const kind = el.placement?.kind;
    const candidates = expandCandidates(kind, byKind, room);
    if (candidates.length === 0) {
      el.fidelity = el.fidelity || "draft";
      unresolved += 1;
      continue;
    }
    // Pick the best candidate: largest area, then closest to the room
    // centroid (so the first wall the user sees gets used).
    const pick = pickBestSurface(candidates, room);
    el.resolvedSurface = {
      surfaceId: pick.id,
      kind: pick.kind,
      areaMeters2: pick.areaMeters2 ?? pick.area ?? null,
      normal: pick.normal,
      centroid: pick.centroid || centroidOf(pick.polygon),
      heightMeters: pick.heightMeters ?? null,
      facingUser: pick.facingUser ?? null,
      source: pick.source || "room_scan",
    };
    // Initial fidelity from the planner is "committed" — we have a real
    // surface to anchor the element to. The prompt-driven re-planner is
    // the path that emits "ghost"; that one starts at low confidence and
    // promotes elements as the planner re-runs.
    if (!el.fidelity || el.fidelity === "draft") el.fidelity = "committed";
    el.whyThisPlacement = appendSurfaceCitation(el.whyThisPlacement, pick);
    resolved += 1;
  }
  return { resolved, unresolved };
}

function expandCandidates(kind, byKind, room) {
  if (!kind) return [];
  const sequence = KIND_BIND[kind] || [];
  const out = [];
  for (const k of sequence) {
    const list = byKind.get(k) || [];
    out.push(...list);
  }
  // If the user picked a wall slot but the scan didn't classify any
  // walls (eg LiDAR missed a glass wall), fall through to the largest
  // vertical surface we did get.
  if (out.length === 0 && (kind === "wall" || kind.endsWith("_of_user"))) {
    const verticals = room.surfaces.filter((s) => Math.abs((s.normal?.[1] ?? 0)) < 0.3);
    out.push(...verticals);
  }
  return out;
}

function pickBestSurface(candidates, room) {
  const roomCenter = bboxCenter(room.boundingBox);
  return [...candidates].sort((a, b) => {
    const areaDiff = (b.area || 0) - (a.area || 0);
    if (Math.abs(areaDiff) > 0.5) return areaDiff;
    const da = dist(centroidOf(a.polygon), roomCenter);
    const db = dist(centroidOf(b.polygon), roomCenter);
    return da - db;
  })[0];
}

function centroidOf(polygon) {
  if (!Array.isArray(polygon) || polygon.length === 0) return [0, 0, 0];
  let sx = 0, sy = 0, sz = 0;
  for (const v of polygon) {
    sx += v[0] || 0; sy += v[1] || 0; sz += v[2] || 0;
  }
  const n = polygon.length;
  return [sx / n, sy / n, sz / n];
}

function bboxCenter(bb) {
  if (!bb?.min || !bb?.max) return [0, 0, 0];
  return [
    ((bb.min[0] || 0) + (bb.max[0] || 0)) / 2,
    ((bb.min[1] || 0) + (bb.max[1] || 0)) / 2,
    ((bb.min[2] || 0) + (bb.max[2] || 0)) / 2,
  ];
}

function dist(a, b) {
  const dx = a[0] - b[0], dy = a[1] - b[1], dz = a[2] - b[2];
  return Math.sqrt(dx * dx + dy * dy + dz * dz);
}

function appendSurfaceCitation(existing, surface) {
  const dims = surface.polygon?.length ? surfaceDimensions(surface.polygon) : null;
  const area = surface.areaMeters2 ?? surface.area;
  const areaStr = typeof area === "number" ? `, area ${area.toFixed(1)}m²` : "";
  const citation = `Resolved to ${surface.id} (${surface.kind}` +
    (dims ? `, ${dims.w.toFixed(1)}m × ${dims.h.toFixed(1)}m` : "") +
    `${areaStr}${surface.source ? `, ${surface.source}` : ""}).`;
  return existing ? `${existing} — ${citation}` : citation;
}

function surfaceDimensions(polygon) {
  let xs = [], ys = [], zs = [];
  for (const v of polygon) { xs.push(v[0]); ys.push(v[1]); zs.push(v[2]); }
  const dx = Math.max(...xs) - Math.min(...xs);
  const dy = Math.max(...ys) - Math.min(...ys);
  const dz = Math.max(...zs) - Math.min(...zs);
  // The two largest extents define the surface plane; the smallest is
  // the surface's thickness (≈0).
  const sorted = [dx, dy, dz].sort((a, b) => b - a);
  return { w: sorted[0], h: sorted[1] };
}
