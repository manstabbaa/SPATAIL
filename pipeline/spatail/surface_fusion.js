// surface_fusion.js — the "construction" step of the SPATAIL brain.
//
// Fuses the TWO visual inputs the device streams:
//   1. a VLM identification ("what am I looking at?" — table / keyboard / …)
//   2. the scanned room surfaces ("where is the geometry?" — planes/mesh with
//      per-surface classification), plus the camera 6-DoF pose.
//
// Output: a LABELED SURFACE — the scanned surface the user is actually looking
// at, tagged with the VLM's noun, with its edges and corners resolved in world
// space. That labeled primitive is what the placement layer reasons over
// ("put foam on the corners and edges of that scan").
//
// Pure geometry + plain data in/out, so it runs identically in the PC brain
// service today and could be ported to any client later. No deps.

// ── tiny vector helpers (world space, metres) ─────────────────────────────
const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const add = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
const scale = (a, s) => [a[0] * s, a[1] * s, a[2] * s];
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const len = (a) => Math.sqrt(dot(a, a));
const dist = (a, b) => len(sub(a, b));
const normalize = (a) => {
  const l = len(a);
  return l > 1e-9 ? scale(a, 1 / l) : [0, 0, 0];
};

// ── surface geometry: derive edges + corners from a surface polygon ───────
// A surface carries `polygon` (world-space boundary vertices) + `normal`.
// Edges are consecutive polygon segments; corners are the vertices.
export function surfaceGeometry(surface) {
  const poly = surface.polygon || [];
  const normal = normalize(surface.normal || [0, 1, 0]);
  const n = poly.length;
  const corners = poly.map((v, i) => ({ index: i, point: v }));
  const edges = [];
  let perimeter = 0;
  for (let i = 0; i < n; i++) {
    const from = poly[i];
    const to = poly[(i + 1) % n];
    const l = dist(from, to);
    perimeter += l;
    edges.push({
      index: i,
      from,
      to,
      length: l,
      midpoint: scale(add(from, to), 0.5),
    });
  }
  const centroid = surface.centroid || centroidOf(poly);
  // extent along the two in-plane axes (drop the normal-dominant axis)
  const extent = planarExtent(poly, normal);
  return { corners, edges, centroid, normal, perimeter, extent, polygon: poly };
}

// ── fuse a VLM identification onto the surface the camera is looking at ────
//
// identification: { primary, detections?: [{label,confidence}] }
// room:           { surfaces: [{id, kind, polygon, normal, centroid, area}], boundingBox? }
// pose:           { position:[x,y,z], forward:[x,y,z] }   (optional but preferred)
//
// Returns a labeledSurface, or null when nothing plausible is in view (→ the
// caller spawns a stand-in primitive instead).
export function fuseIdentificationToSurface({ identification, room, pose, opts = {} }) {
  const surfaces = room?.surfaces || [];
  if (surfaces.length === 0) return null;

  const label = identification?.primary || null;
  const wantKind = labelToSurfaceKind(label);

  // 1) Preferred: raycast the camera-forward ray and take the frontmost
  //    surface the ray actually pierces (inside its polygon).
  let hit = null;
  if (pose?.position && pose?.forward) {
    const origin = pose.position;
    const dir = normalize(pose.forward);
    let best = null;
    for (const s of surfaces) {
      // The VLM noun gates the geometry: if we recognise the label's kind
      // ("table"), a ray that pierces a different kind (the floor behind an
      // absent table) does NOT count as looking at the table. Unknown nouns
      // (wantKind == null) accept any hit, since we have no prior.
      if (wantKind && s.kind !== wantKind) continue;
      const g = surfaceGeometry(s);
      const t = rayPlane(origin, dir, g.centroid, g.normal);
      if (t == null || t <= 0) continue;
      const p = add(origin, scale(dir, t));
      if (!pointInPolygon3D(p, g.polygon, g.normal)) continue;
      if (!best || t < best.t) best = { surface: s, geom: g, t, point: p };
    }
    if (best) hit = { ...best, reason: `camera ray pierced ${best.surface.id} at ${best.t.toFixed(2)}m` };
  }

  // 2) Fallback: no ray hit (or no pose) — trust the VLM noun and bind to the
  //    largest surface whose classification matches ("I'm looking at the table"
  //    even if the ray grazed off the polygon edge).
  if (!hit && wantKind) {
    const matches = surfaces.filter((s) => s.kind === wantKind);
    if (matches.length) {
      const s = pickLargest(matches);
      hit = { surface: s, geom: surfaceGeometry(s), t: null, point: null,
              reason: `no ray hit; bound VLM label "${label}" to nearest ${wantKind}` };
    }
  }

  if (!hit) return null;

  // Confidence: VLM's own score, discounted if we fell back to label-binding.
  const vlmConf = identification?.detections?.[0]?.confidence ?? 0.5;
  const confidence = hit.t != null ? vlmConf : vlmConf * 0.7;

  return {
    surfaceId: hit.surface.id,
    kind: hit.surface.kind,
    label,                 // the VLM noun the user is looking at
    confidence,
    matchReason: hit.reason,
    hitPoint: hit.point,   // where the gaze ray met the surface (or null)
    geometry: hit.geom,    // corners + edges + centroid + normal + extent
    surface: hit.surface,  // pass the raw surface through for citations
  };
}

// ── label → surface kind priors ───────────────────────────────────────────
// The VLM answers in open vocabulary; the scan classifies in a closed set.
// This maps the common nouns onto the scanned surface kinds so the fallback
// can bind them. Unknown nouns return null (ray-hit is then the only path).
function labelToSurfaceKind(label) {
  if (!label) return null;
  const s = label.toLowerCase();
  if (/\b(table|desk|counter|workbench|nightstand|dresser|bench)\b/.test(s)) return "table";
  if (/\b(floor|ground|rug|carpet)\b/.test(s)) return "floor";
  if (/\b(wall|door|window|whiteboard)\b/.test(s)) return "wall";
  if (/\b(ceiling)\b/.test(s)) return "ceiling";
  if (/\b(chair|seat|stool|sofa|couch)\b/.test(s)) return "seat";
  return null;
}

// ── geometry primitives ───────────────────────────────────────────────────
function rayPlane(origin, dir, planePoint, planeNormal) {
  const denom = dot(dir, planeNormal);
  if (Math.abs(denom) < 1e-6) return null; // parallel
  return dot(sub(planePoint, origin), planeNormal) / denom;
}

// Point-in-polygon on the surface's own plane: project to the 2 axes with the
// smallest normal component, then run the even-odd crossing test.
function pointInPolygon3D(p, polygon, normal) {
  if (!polygon || polygon.length < 3) return false;
  const ax = dropAxis(normal);
  const px = p[ax[0]], py = p[ax[1]];
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const xi = polygon[i][ax[0]], yi = polygon[i][ax[1]];
    const xj = polygon[j][ax[0]], yj = polygon[j][ax[1]];
    const intersect = (yi > py) !== (yj > py) &&
      px < ((xj - xi) * (py - yi)) / (yj - yi + 1e-12) + xi;
    if (intersect) inside = !inside;
  }
  return inside;
}

function dropAxis(normal) {
  const a = [Math.abs(normal[0]), Math.abs(normal[1]), Math.abs(normal[2])];
  const drop = a.indexOf(Math.max(...a)); // the axis most aligned with normal
  return [0, 1, 2].filter((i) => i !== drop);
}

function planarExtent(polygon, normal) {
  if (!polygon?.length) return { u: 0, v: 0 };
  const [ua, va] = dropAxis(normal);
  const us = polygon.map((p) => p[ua]);
  const vs = polygon.map((p) => p[va]);
  return { u: Math.max(...us) - Math.min(...us), v: Math.max(...vs) - Math.min(...vs) };
}

function centroidOf(polygon) {
  if (!polygon?.length) return [0, 0, 0];
  const s = polygon.reduce((acc, v) => add(acc, v), [0, 0, 0]);
  return scale(s, 1 / polygon.length);
}

function pickLargest(surfaces) {
  return [...surfaces].sort(
    (a, b) => (b.area ?? b.areaMeters2 ?? 0) - (a.area ?? a.areaMeters2 ?? 0),
  )[0];
}

// exported for the placement layer / tests
export const _geom = { sub, add, scale, dot, len, dist, normalize, centroidOf };
