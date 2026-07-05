// room_contract_adapter.js — closes the loop between the DEVICE's real room
// scan and the fusion brain.
//
// iOS persists what it scanned as a RoomContract (ios/Spatail/Sources/
// Contracts/WireMessages.swift, schema "0.4.0-spatail-room"): surfaces with
// {id, kind, polygon[[x,y,z]], normal, area, height?, source}. The brain
// (surface_fusion.js / room_aware_planner.js) wants the same surfaces plus a
// centroid and `heightMeters`. This adapter normalizes either shape into the
// canonical brain room — so the SAME fusion code runs on a synthetic test
// room, a saved device scan, or a live room.update off the wire.

import { _geom } from "./surface_fusion.js";

const { centroidOf } = _geom;

/** True when the JSON looks like an iOS RoomContract (vs already brain-shaped). */
export function isIOSRoomContract(json) {
  return Boolean(json && (json.roomId || (json.schemaVersion || "").includes("room")));
}

/**
 * Normalize an iOS RoomContract — or an already-brain-shaped room — into the
 * canonical brain room: { surfaces:[{id, kind, polygon, normal, centroid,
 * area, heightMeters, source, confidence?, boundary?}], objects, boundingBox,
 * meta }.
 */
export function toBrainRoom(json) {
  if (!json) return null;
  const src = Array.isArray(json.surfaces) ? json.surfaces : [];
  const surfaces = src
    .filter((s) => Array.isArray(s.polygon) && s.polygon.length >= 3)
    .map((s, i) => ({
      id: s.id || `surface_${i}`,
      kind: s.kind || "unknown",
      polygon: s.polygon.map((v) => [num(v[0]), num(v[1]), num(v[2])]),
      normal: normalOf(s),
      centroid: s.centroid || centroidOf(s.polygon),
      area: num(s.area ?? s.areaMeters2, 0),
      heightMeters: s.heightMeters ?? s.height ?? null,
      source: s.source || "room_scan",
      // additive optional fields (LIVE_BRAIN_SPEC §1.2): classification
      // confidence and a concave boundary polygon ride through when the
      // phone sends them; absent otherwise (JSON drops undefined — and NOT
      // num(v, undefined), whose default param would coerce absent to 0).
      confidence: Number.isFinite(s.confidence) ? s.confidence : undefined,
      boundary: Array.isArray(s.boundary) ? s.boundary : undefined,
    }));

  // room.update objects[] (LIVE_BRAIN_SPEC §1.2) — tracked-object OBBs the
  // device IoU-fused labels onto. Optional; absent on older room payloads.
  const objects = (Array.isArray(json.objects) ? json.objects : [])
    .filter((o) => o && o.id != null)
    .map((o) => ({
      id: String(o.id),
      label: o.label ?? null,
      confidence: num(o.confidence, 0),
      obb: o.obb
        ? {
            center: (o.obb.center || [0, 0, 0]).map((v) => num(v)),
            extents: (o.obb.extents || [0, 0, 0]).map((v) => num(v)),
            yaw: num(o.obb.yaw, 0),
          }
        : null,
      supportSurfaceId: o.supportSurfaceId ?? null,
      lastSeenAt: o.lastSeenAt ?? null,
    }));

  return {
    surfaces,
    objects,
    boundingBox: json.boundingBox || bboxOf(surfaces),
    meta: isIOSRoomContract(json)
      ? {
          roomId: json.roomId || null,
          capturedAt: json.capturedAt || null,
          device: json.device || null,
          lidar: json.lidar ?? null,
          preferredWallId: json.preferredWallId || null,
          preferredTableId: json.preferredTableId || null,
          schemaVersion: json.schemaVersion || null,
        }
      : json.meta || null,
  };
}

/** Pull a pose out of a protocol userPose / pose payload, if present. */
export function toBrainPose(p) {
  if (!p || !Array.isArray(p.position) || !Array.isArray(p.forward)) return null;
  const pose = {
    position: p.position.map((v) => num(v)),
    forward: p.forward.map((v) => num(v)),
  };
  // Device-fixed up (spec §1.1 pose.update) — the box-grounding camera view
  // model maps image axes exactly with it (image right = up). Optional;
  // absent on legacy/synthetic poses, where an upright hold is assumed.
  if (Array.isArray(p.up) && p.up.length >= 3) pose.up = p.up.map((v) => num(v));
  return pose;
}

function normalOf(s) {
  const n = s.normal;
  if (Array.isArray(n) && n.length >= 3) return [num(n[0]), num(n[1], 1), num(n[2])];
  return [0, 1, 0];
}

function bboxOf(surfaces) {
  let lo = [Infinity, Infinity, Infinity];
  let hi = [-Infinity, -Infinity, -Infinity];
  for (const s of surfaces) {
    for (const v of s.polygon) {
      for (let a = 0; a < 3; a++) {
        if (v[a] < lo[a]) lo[a] = v[a];
        if (v[a] > hi[a]) hi[a] = v[a];
      }
    }
  }
  if (!Number.isFinite(lo[0])) { lo = [0, 0, 0]; hi = [0, 0, 0]; }
  return { min: lo, max: hi };
}

function num(v, fallback = 0) {
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}
