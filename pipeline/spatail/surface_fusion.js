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
// Objects-first (LIVE_BRAIN_SPEC §1.2): when the room carries tracked
// objects[] and the noun matches one of their device-fused labels, identity
// binds to that OBJECT (kind "object") before any surface logic runs.
//
// Pure geometry + plain data in/out, so it runs identically in the PC brain
// service today and could be ported to any client later. No deps.

// ── tiny vector helpers (world space, metres) ─────────────────────────────
const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const add = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
const scale = (a, s) => [a[0] * s, a[1] * s, a[2] * s];
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a, b) => [
  a[1] * b[2] - a[2] * b[1],
  a[2] * b[0] - a[0] * b[2],
  a[0] * b[1] - a[1] * b[0],
];
const len = (a) => Math.sqrt(dot(a, a));
const dist = (a, b) => len(sub(a, b));
const normalize = (a) => {
  const l = len(a);
  return l > 1e-9 ? scale(a, 1 / l) : [0, 0, 0];
};
const clamp01 = (v) => Math.min(Math.max(v, 0), 1);
const boxCenter = (b) => [b[0] + b[2] / 2, b[1] + b[3] / 2];
const isBox = (b) =>
  Array.isArray(b) && b.length === 4 && b.every(Number.isFinite) && b[2] > 0 && b[3] > 0;

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
// identification: { primary, detections?: [{label,confidence,box?}],
//                   parts?, frameSize?: [w,h] }
// room:           { surfaces: [{id, kind, polygon, normal, centroid, area}], boundingBox? }
// pose:           { position:[x,y,z], forward:[x,y,z], up?:[x,y,z] }   (optional but preferred)
// opts:           { fovLongEdgeDegrees? }   (camera view model — box grounding)
//
// Returns a labeledSurface, or null when nothing plausible is in view (→ the
// caller spawns a stand-in primitive instead). When the identification
// carries the primary detection's box, the box GROUNDS the fusion: the gaze
// ray runs through the box center instead of the camera forward (the
// near-but-not-on fix), object candidates are scored by projected-footprint
// IoU with the box, and every box-driven step lands in `decisionTrace`.
export function fuseIdentificationToSurface({ identification, room, pose, opts = {} }) {
  const surfaces = room?.surfaces || [];
  const label = identification?.primary || null;
  const trace = [];

  // Box grounding: the primary detection's normalized [x, y, w, h] box + the
  // camera view model turn "roughly where the camera points" into "exactly
  // where the VLM saw the thing".
  const primaryBox = identification?.detections?.[0]?.box ?? null;
  const view = cameraViewFromPose(pose, identification?.frameSize, opts);
  const sight = view && isBox(primaryBox) ? { view, box: primaryBox } : null;

  // Objects-first: a tracked object whose device-fused label matches the noun
  // owns the identity; surfaces only bind when no object matches.
  const scored = nounObjectScores(label, room?.objects, sight);
  const obj = scored.length ? scored[0].obj : null;
  if (obj) {
    if (sight && scored[0].iou != null) {
      const runnerUp = scored.slice(1).find((s) => s.iou != null);
      trace.push(
        `box: object ${obj.id} projected footprint IoU ` +
        `${scored[0].iou.toFixed(2)} with detection box` +
        (runnerUp
          ? ` (next candidate ${runnerUp.obj.id} at ${runnerUp.iou.toFixed(2)})`
          : ""),
      );
    }
    // The gaze ray through the box center pierces the object where the VLM
    // saw it — that world point rides along as hitPoint (null before boxes).
    let hitPoint = null;
    if (sight && obj.obb) {
      const [u, v] = boxCenter(primaryBox);
      const rayHit = rayObbIntersect(view.origin, rayThroughImagePoint(view, u, v), obj.obb);
      if (rayHit) {
        hitPoint = rayHit.point;
        trace.push(
          `box: ray through box center [${u.toFixed(2)}, ${v.toFixed(2)}] ` +
          `hits ${obj.id} at ${rayHit.t.toFixed(2)}m`,
        );
      }
    }
    const support = obj.supportSurfaceId
      ? surfaces.find((s) => s.id === obj.supportSurfaceId) || null
      : null;
    const vlmConf = identification?.detections?.[0]?.confidence ?? 0.5;
    return {
      objectId: obj.id,
      kind: "object",
      supportSurfaceId: obj.supportSurfaceId ?? null,
      surfaceId: obj.supportSurfaceId ?? null,
      label: obj.label,
      confidence: obj.confidence || vlmConf,
      matchReason: `noun matched object ${obj.label} (IoU-fused on device)`,
      hitPoint,
      geometry: support ? surfaceGeometry(support) : obbTopGeometry(obj.obb),
      surface: support,
      object: obj,
      decisionTrace: trace,
    };
  }

  if (surfaces.length === 0) return null;

  const wantKind = labelToSurfaceKind(label);

  // 1) Preferred: raycast and take the frontmost surface the ray actually
  //    pierces (inside its polygon). With a grounded box the ray runs through
  //    the BOX CENTER — where the VLM saw the object — and only falls back to
  //    the raw camera-forward ray when that refined ray misses everything.
  let hit = null;
  if (pose?.position && pose?.forward) {
    const origin = pose.position;
    const rawDir = normalize(pose.forward);
    const rays = [];
    if (sight) {
      const [u, v] = boxCenter(primaryBox);
      const refined = rayThroughImagePoint(view, u, v);
      const deg = (Math.acos(Math.min(1, Math.max(-1, dot(refined, rawDir)))) * 180) / Math.PI;
      rays.push({
        dir: refined,
        via: "box-refined ray",
        note: `box: gaze refined through box center [${u.toFixed(2)}, ${v.toFixed(2)}]` +
              ` (${deg.toFixed(1)}° off camera forward)`,
      });
    }
    rays.push({ dir: rawDir, via: "camera ray" });
    for (const ray of rays) {
      let best = null;
      for (const s of surfaces) {
        // The VLM noun gates the geometry: if we recognise the label's kind
        // ("table"), a ray that pierces a different kind (the floor behind an
        // absent table) does NOT count as looking at the table. Unknown nouns
        // (wantKind == null) accept any hit, since we have no prior.
        if (wantKind && s.kind !== wantKind) continue;
        const g = surfaceGeometry(s);
        const t = rayPlane(origin, ray.dir, g.centroid, g.normal);
        if (t == null || t <= 0) continue;
        const p = add(origin, scale(ray.dir, t));
        if (!pointInPolygon3D(p, g.polygon, g.normal)) continue;
        if (!best || t < best.t) best = { surface: s, geom: g, t, point: p };
      }
      if (best) {
        if (ray.note) trace.push(ray.note);
        hit = { ...best, reason: `${ray.via} pierced ${best.surface.id} at ${best.t.toFixed(2)}m` };
        break;
      }
      if (ray.note) {
        trace.push(`box: refined ray missed every ${wantKind ?? "candidate"} surface; ` +
                   `falling back to camera forward`);
      }
    }
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
    decisionTrace: trace,
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

// ── noun → tracked-object fuzzy match (objects-first binding) ──────────────
// Colors + materials the VLM prepends to nouns ("blue water bottle") that the
// device's object labels usually omit — stripped before containment matching.
const NOISE_ADJECTIVES = new Set([
  "red", "orange", "yellow", "green", "blue", "purple", "violet", "pink",
  "black", "white", "gray", "grey", "brown", "beige", "tan", "silver",
  "gold", "golden", "dark", "light", "pale", "bright", "clear",
  "transparent", "shiny", "matte",
  "plastic", "metal", "metallic", "wooden", "wood", "glass", "ceramic",
  "leather", "steel", "aluminum", "aluminium", "rubber", "foam", "paper",
  "cardboard", "fabric", "cloth", "stone", "marble", "chrome", "brass",
  "copper", "stainless",
]);

function normalizeNoun(text) {
  if (!text) return "";
  const words = String(text)
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, " ")
    .split(/\s+/)
    .filter(Boolean);
  const kept = words.filter((w) => !NOISE_ADJECTIVES.has(w));
  // an all-adjective noun ("glass") still deserves a match attempt
  return (kept.length ? kept : words).join(" ");
}

// Case-insensitive containment either way after adjective stripping; exact
// equality outranks containment, then the device's label confidence breaks
// ties. `sight` ({ view, box }) adds the VLM's spatial grounding: candidates
// earn BOX_IOU_WEIGHT × IoU between their projected OBB footprint and the
// primary detection box, so of two same-noun objects the brain binds the one
// the VLM actually boxed (the seen one beats the one behind you).
// LOCKSTEP: these semantics — containment match, exact > containment,
// confidence tie-break, box-IoU bonus — are MIRRORED in _binding_for()
// (pipeline/server/spatail_vision_engine.py). Change both together.
export const BOX_IOU_WEIGHT = 2.0;

export function nounObjectScores(label, objects, sight = null) {
  const noun = normalizeNoun(label);
  if (!noun) return [];
  const grounded = sight?.view && isBox(sight?.box) ? sight : null;
  const scores = [];
  for (const obj of objects || []) {
    const objLabel = normalizeNoun(obj?.label);
    if (!objLabel) continue;
    if (noun !== objLabel && !noun.includes(objLabel) && !objLabel.includes(noun)) continue;
    let iou = null;
    if (grounded && obj.obb) {
      const rect = projectObbToImageRect(grounded.view, obj.obb);
      iou = rect ? boxIoU(rect, grounded.box) : 0;
    }
    const score = (noun === objLabel ? 2 : 1) + (obj.confidence ?? 0)
      + (iou != null ? BOX_IOU_WEIGHT * iou : 0);
    scores.push({ obj, score, iou });
  }
  // stable sort keeps the earlier candidate on ties — same as the old
  // strictly-greater loop
  return scores.sort((a, b) => b.score - a.score);
}

export function matchNounToObject(label, objects, sight = null) {
  const scores = nounObjectScores(label, objects, sight);
  return scores.length ? scores[0].obj : null;
}

// Placement geometry for an object with no resolvable support surface: the
// OBB's top face (extents are full sizes; yaw about gravity-aligned +Y).
function obbTopGeometry(obb) {
  const c = obb?.center || [0, 0, 0];
  const e = obb?.extents || [0.2, 0.2, 0.2];
  const yaw = obb?.yaw || 0;
  const hx = e[0] / 2, hz = e[2] / 2;
  const top = c[1] + e[1] / 2;
  const cos = Math.cos(yaw), sin = Math.sin(yaw);
  const rot = (x, z) => [c[0] + x * cos + z * sin, top, c[2] - x * sin + z * cos];
  const polygon = [rot(-hx, -hz), rot(hx, -hz), rot(hx, hz), rot(-hx, hz)];
  return surfaceGeometry({ polygon, normal: [0, 1, 0], centroid: [c[0], top, c[2]] });
}

// ── camera view model: image space ↔ world space (box grounding) ──────────
// The phone streams frames `.oriented(.right)` (FrameStreamer.swift): the
// sensor-landscape buffer rotated into a portrait JPEG. Both that rotation
// and the pose's axes are DEVICE-FIXED, so the image axes map onto the pose
// exactly, however the phone is physically held:
//   image right (+u) = pose.up          (camera +Y column)
//   image down  (+v) = forward × up     (camera +X column)
// The wire carries no intrinsics, so the LONG image edge gets an assumed
// field of view (default 67°, ≈ the ARKit wide camera) and the short edge
// follows from the frame's aspect (identification.frameSize [w,h]; 3:4
// portrait assumed when absent — the FrameStreamer contract).
export const DEFAULT_LONG_EDGE_FOV_DEG = 67;

export function cameraViewFromPose(pose, frameSize, opts = {}) {
  if (!pose?.position || !pose?.forward) return null;
  const forward = normalize(pose.forward);
  if (len(forward) < 0.5) return null;
  let right;
  if (Array.isArray(pose.up) && pose.up.length >= 3 && len(pose.up) > 1e-6) {
    // device-fixed exact mapping — image right IS the pose's up vector
    right = pose.up;
  } else {
    // no up (synthetic/legacy pose): assume an upright portrait hold, so
    // image right is horizontal. Straight up/down gaze has no roll cue → null.
    right = cross(forward, [0, 1, 0]);
    if (len(right) < 1e-6) return null;
  }
  // orthogonalize against forward (cheap insurance for hand-authored poses)
  right = normalize(sub(right, scale(forward, dot(right, forward))));
  if (len(right) < 0.5) return null;
  const down = normalize(cross(forward, right));
  const fovLong = ((opts.fovLongEdgeDegrees ?? DEFAULT_LONG_EDGE_FOV_DEG) * Math.PI) / 180;
  const tanLong = Math.tan(fovLong / 2);
  const [fw, fh] =
    Array.isArray(frameSize) && frameSize[0] > 0 && frameSize[1] > 0
      ? frameSize
      : [3, 4];
  const longEdge = Math.max(fw, fh);
  return {
    origin: pose.position,
    forward,
    right,
    down,
    tanU: tanLong * (fw / longEdge),
    tanV: tanLong * (fh / longEdge),
  };
}

/** World-space unit ray direction through normalized image point (u, v). */
export function rayThroughImagePoint(view, u, v) {
  return normalize(add(
    view.forward,
    add(scale(view.right, (2 * u - 1) * view.tanU),
        scale(view.down, (2 * v - 1) * view.tanV)),
  ));
}

/** Normalized image [u, v] of a world point, or null when behind the camera. */
export function projectPointToImage(view, p) {
  const d = sub(p, view.origin);
  const z = dot(d, view.forward);
  if (z <= 1e-6) return null;
  return [
    0.5 + dot(d, view.right) / (z * view.tanU) / 2,
    0.5 + dot(d, view.down) / (z * view.tanV) / 2,
  ];
}

// The 8 world-space corners of an OBB (extents are full sizes; yaw about +Y —
// same convention as obbTopGeometry above).
function obbCorners(obb) {
  const c = obb?.center || [0, 0, 0];
  const e = obb?.extents || [0, 0, 0];
  const yaw = obb?.yaw || 0;
  const cos = Math.cos(yaw), sin = Math.sin(yaw);
  const corners = [];
  for (const sx of [-1, 1]) {
    for (const sy of [-1, 1]) {
      for (const sz of [-1, 1]) {
        const x = (sx * e[0]) / 2, z = (sz * e[2]) / 2;
        corners.push([
          c[0] + x * cos + z * sin,
          c[1] + (sy * e[1]) / 2,
          c[2] - x * sin + z * cos,
        ]);
      }
    }
  }
  return corners;
}

/**
 * The OBB's projected footprint in the image: the normalized [x, y, w, h]
 * bounding rect of its visible corners, clamped to the unit square. null when
 * the object is entirely behind the camera or projects to nothing on-screen.
 */
export function projectObbToImageRect(view, obb) {
  if (!view || !obb) return null;
  let minU = Infinity, minV = Infinity, maxU = -Infinity, maxV = -Infinity;
  let seen = 0;
  for (const corner of obbCorners(obb)) {
    const uv = projectPointToImage(view, corner);
    if (!uv) continue;
    seen += 1;
    if (uv[0] < minU) minU = uv[0];
    if (uv[1] < minV) minV = uv[1];
    if (uv[0] > maxU) maxU = uv[0];
    if (uv[1] > maxV) maxV = uv[1];
  }
  if (seen === 0) return null;
  const x = clamp01(minU), y = clamp01(minV);
  const w = clamp01(maxU) - x, h = clamp01(maxV) - y;
  if (w <= 0 || h <= 0) return null;
  return [x, y, w, h];
}

/** Intersection-over-union of two normalized [x, y, w, h] boxes. */
export function boxIoU(a, b) {
  if (!isBox(a) || !isBox(b)) return 0;
  const ix = Math.max(a[0], b[0]);
  const iy = Math.max(a[1], b[1]);
  const iw = Math.min(a[0] + a[2], b[0] + b[2]) - ix;
  const ih = Math.min(a[1] + a[3], b[1] + b[3]) - iy;
  if (iw <= 0 || ih <= 0) return 0;
  const inter = iw * ih;
  return inter / (a[2] * a[3] + b[2] * b[3] - inter);
}

/**
 * Ray → OBB intersection (slab test in the OBB's yaw frame). Returns
 * { t, point } for the entry point in front of the origin, or null on a miss.
 * An origin inside the box returns t = 0 at the origin.
 */
export function rayObbIntersect(origin, dir, obb) {
  if (!obb) return null;
  const c = obb.center || [0, 0, 0];
  const e = obb.extents || [0, 0, 0];
  const yaw = obb.yaw || 0;
  const cos = Math.cos(yaw), sin = Math.sin(yaw);
  // world → obb-local (inverse of the obbCorners rotation)
  const d = sub(origin, c);
  const o = [d[0] * cos - d[2] * sin, d[1], d[0] * sin + d[2] * cos];
  const v = [dir[0] * cos - dir[2] * sin, dir[1], dir[0] * sin + dir[2] * cos];
  let tmin = 0, tmax = Infinity;
  for (let axis = 0; axis < 3; axis++) {
    const half = (e[axis] || 0) / 2;
    if (Math.abs(v[axis]) < 1e-9) {
      if (o[axis] < -half || o[axis] > half) return null;
      continue;
    }
    let t1 = (-half - o[axis]) / v[axis];
    let t2 = (half - o[axis]) / v[axis];
    if (t1 > t2) [t1, t2] = [t2, t1];
    if (t1 > tmin) tmin = t1;
    if (t2 < tmax) tmax = t2;
    if (tmin > tmax) return null;
  }
  return { t: tmin, point: add(origin, scale(dir, tmin)) };
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
export const _geom = { sub, add, scale, dot, cross, len, dist, normalize, centroidOf, clamp01 };
