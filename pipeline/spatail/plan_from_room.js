#!/usr/bin/env node
// plan_from_room.js — the fusion brain's front door for REAL room scans.
//
// Takes a room (an iOS RoomContract JSON or an already-brain-shaped room),
// an optional camera pose, and an optional VLM identification, and runs the
// full fuse → decide pipeline. Prints {mode, summary, contract} JSON.
//
// Two invocation modes:
//   file  : node pipeline/spatail/plan_from_room.js <room.json> [--label table]
//           (manual verification against a saved device scan)
//   stdin : node pipeline/spatail/plan_from_room.js --stdin
//           reads {room, pose?, identification?, concept?} as one JSON blob —
//           this is how spatail_vision_engine.py invokes the brain live.
//
// Either mode: room may carry objects[] (LIVE_BRAIN_SPEC §1.2) — tracked
// object OBBs the device fused labels onto. They flow into fusion for
// objects-first binding; matches surface in the output as fused.objectId
// (+ supportSurfaceId) and, when the concept addresses an identified part,
// a top-level target: {objectId, part}.
//
// Box grounding (spec §1.6): when identification carries the primary
// detection's normalized box (+ optional frameSize), fusion refines the gaze
// ray through the box center and scores object candidates by projected IoU;
// a part-addressed target additionally gains anchor {point, method, partBox}
// from parts[].box. Every box-driven step lands in fused.decisionTrace.
// Input may carry camera: {fovLongEdgeDegrees} to override the view model's
// assumed long-edge FOV (default 67°; env SPATAIL_CAMERA_FOV_LONG).
//
// Defaults: concept = the newborn foam-padding slice. Pose, when absent and
// the room has a table, is synthesized standing 1.5 m back looking at the
// largest table (so file-mode runs work without hand-authoring a pose).

import { readFileSync } from "node:fs";
import {
  fuseIdentificationToSurface,
  cameraViewFromPose,
  rayThroughImagePoint,
  rayObbIntersect,
  _geom,
} from "./surface_fusion.js";
import { planSurfaceExperience } from "./surface_placement.js";
import { toBrainRoom, toBrainPose } from "./room_contract_adapter.js";

const { normalize, scale, add, clamp01 } = _geom;
const round3 = (v) => Math.round(v * 1000) / 1000;
const isBox = (b) =>
  Array.isArray(b) && b.length === 4 && b.every(Number.isFinite) && b[2] > 0 && b[3] > 0;

const DEFAULT_CONCEPT = {
  id: "foam_padding",
  title: "Foam edge padding",
  prompt: "foam padding for my table edges — I have a newborn",
  reason: "Soft-guard the impact edges + corners of the surface for a newborn.",
  unitLengthMeters: 0.5,
  edgeDepthMeters: 0.04,
  cornerSizeMeters: 0.08,
  insetMeters: 0.0,
};

// Part addressability (LIVE_BRAIN_SPEC §1.3/§3): when identity binds to an
// object, the identification carries parts, and the concept's text talks
// about one of them, the plan targets {objectId, part} so clients anchor to
// the part's resolved region instead of the whole object. With a grounded
// part box (§1.6) the target also carries anchor {point, method, partBox} —
// the brain's own world-space estimate of WHERE on the object the part is.
function partTarget(labeled, identification, concept, view, trace) {
  if (!labeled || !labeled.objectId) return null;
  const parts = identification?.parts;
  if (!Array.isArray(parts) || parts.length === 0) return null;
  const text = [concept.prompt, concept.title, concept.reason]
    .filter(Boolean).join(" ").toLowerCase();
  for (const part of parts) {
    const partLabel = String(part?.label || "").toLowerCase().trim();
    if (!partLabel) continue;
    const escaped = partLabel.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    if (new RegExp(`\\b${escaped}\\b`).test(text)) {
      const target = { objectId: labeled.objectId, part: part.label };
      const anchor = partAnchor(labeled, identification, part, view, trace);
      if (anchor) target.anchor = anchor;
      return target;
    }
  }
  return null;
}

// Bias the part anchor onto the part's SEEN location (spec §1.6), two rungs:
//  (a) part_box_ray      — the ray through the part box's center strikes the
//                          bound object's OBB where the part actually is;
//  (b) part_box_relative — ray unavailable/missed: map the part's position
//                          RELATIVE to the primary box onto the OBB
//                          (image-down → height: top of the primary box is
//                          the top of the OBB; image-right → lateral bias
//                          along the view's screen-right axis).
// Neither applies (no part box / no primary box / no OBB) → null, and the
// target stays the bare {objectId, part} the device resolves itself (§3).
function partAnchor(labeled, identification, part, view, trace) {
  const obb = labeled.object?.obb;
  const box = part?.box;
  if (!obb || !isBox(box)) return null;
  const pu = box[0] + box[2] / 2;
  const pv = box[1] + box[3] / 2;
  if (view) {
    const hit = rayObbIntersect(view.origin, rayThroughImagePoint(view, pu, pv), obb);
    if (hit) {
      trace.push(
        `box: part "${part.label}" anchored by the ray through its box center ` +
        `[${pu.toFixed(2)}, ${pv.toFixed(2)}] — hits the object at ${hit.t.toFixed(2)}m`,
      );
      return { point: hit.point.map(round3), method: "part_box_ray", partBox: box };
    }
  }
  const primary = identification?.detections?.[0]?.box;
  if (!isBox(primary)) return null;
  const relU = clamp01((pu - primary[0]) / primary[2]);
  const relV = clamp01((pv - primary[1]) / primary[3]);
  const c = obb.center || [0, 0, 0];
  const e = obb.extents || [0, 0, 0];
  let point = [c[0], c[1] + (0.5 - relV) * e[1], c[2]];
  if (view) {
    // screen-right projected into the horizontal plane; the (yawed) OBB
    // footprint's half-extent along it via the support function
    const lat = normalize([view.right[0], 0, view.right[2]]);
    const yaw = obb.yaw || 0;
    const ax = [Math.cos(yaw), 0, -Math.sin(yaw)]; // obb local +X in world
    const az = [Math.sin(yaw), 0, Math.cos(yaw)];  // obb local +Z in world
    const half = (e[0] / 2) * Math.abs(lat[0] * ax[0] + lat[2] * ax[2])
               + (e[2] / 2) * Math.abs(lat[0] * az[0] + lat[2] * az[2]);
    point = add(point, scale(lat, (relU - 0.5) * 2 * half));
  }
  trace.push(
    `box: part "${part.label}" anchored relative to the primary box ` +
    `(rel [${relU.toFixed(2)}, ${relV.toFixed(2)}] → ` +
    `${((0.5 - relV) * e[1]).toFixed(2)}m above the object's center)`,
  );
  return { point: point.map(round3), method: "part_box_relative", partBox: box };
}

function synthPoseLookingAtLargest(room, kind = "table") {
  const candidates = room.surfaces.filter((s) => s.kind === kind);
  const target = candidates.sort((a, b) => (b.area || 0) - (a.area || 0))[0];
  if (!target) return null;
  const c = target.centroid;
  const eye = [c[0], c[1] + 0.75, c[2] + 1.5]; // standing, 1.5 m back
  const f = [c[0] - eye[0], c[1] - eye[1], c[2] - eye[2]];
  const l = Math.hypot(f[0], f[1], f[2]) || 1;
  return { position: eye, forward: [f[0] / l, f[1] / l, f[2] / l] };
}

function main() {
  const args = process.argv.slice(2);
  let input;
  if (args.includes("--stdin")) {
    input = JSON.parse(readFileSync(0, "utf8"));
  } else {
    const file = args.find((a) => !a.startsWith("--"));
    if (!file) {
      console.error("usage: plan_from_room.js <room.json> [--label X] | --stdin");
      process.exit(2);
    }
    input = { room: JSON.parse(readFileSync(file, "utf8")) };
    const li = args.indexOf("--label");
    if (li >= 0 && args[li + 1]) {
      input.identification = {
        primary: args[li + 1],
        detections: [{ label: args[li + 1], confidence: 0.9 }],
      };
    }
  }

  const room = toBrainRoom(input.room);
  const objects = room?.objects || [];
  if (!room || (room.surfaces.length === 0 && objects.length === 0)) {
    console.error("plan_from_room: no usable surfaces or objects in room input");
    process.exit(2);
  }
  const pose =
    toBrainPose(input.pose) ||
    synthPoseLookingAtLargest(room) ||
    null;
  const identification = input.identification || null;
  // The live wire sends concept as the user's ask STRING (spec §1.2); the CLI
  // and tests send an object. Spreading a string would scatter it into
  // character keys and silently lose the ask — normalize it to {prompt}.
  const conceptIn = typeof input.concept === "string"
    ? { prompt: input.concept }
    : (input.concept || {});
  const concept = { ...DEFAULT_CONCEPT, ...conceptIn };

  // Camera view model knob (box grounding, spec §1.6): brainInput.camera
  // wins, then the engine-shared env, then the built-in 67° long-edge FOV.
  const camOpts = {};
  const fov = Number(input.camera?.fovLongEdgeDegrees ?? process.env.SPATAIL_CAMERA_FOV_LONG);
  if (Number.isFinite(fov) && fov > 10 && fov < 170) camOpts.fovLongEdgeDegrees = fov;

  const { contract, labeled, summary, mode } = planSurfaceExperience({
    identification, room, pose, concept,
    fuse: (args) => fuseIdentificationToSurface({ ...args, opts: camOpts }),
  });
  const view = cameraViewFromPose(pose, identification?.frameSize, camOpts);
  // decisionTrace: fusion's box-driven steps + the part-anchor steps below —
  // the attribution line for "did the box influence this plan?"
  const trace = [...(labeled?.decisionTrace ?? [])];
  const target = partTarget(labeled, identification, concept, view, trace);

  process.stdout.write(JSON.stringify({
    mode,
    summary,
    fused: labeled
      ? { surfaceId: labeled.surfaceId ?? null, kind: labeled.kind,
          label: labeled.label, confidence: labeled.confidence,
          matchReason: labeled.matchReason,
          ...(labeled.hitPoint ? { hitPoint: labeled.hitPoint.map(round3) } : {}),
          ...(labeled.objectId
            ? { objectId: labeled.objectId,
                supportSurfaceId: labeled.supportSurfaceId ?? null }
            : {}),
          ...(trace.length ? { decisionTrace: trace } : {}) }
      : null,
    ...(target ? { target } : {}),
    roomMeta: room.meta,
    contract,
  }));
}

main();
