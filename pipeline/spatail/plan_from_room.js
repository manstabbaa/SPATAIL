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
// Defaults: concept = the newborn foam-padding slice. Pose, when absent and
// the room has a table, is synthesized standing 1.5 m back looking at the
// largest table (so file-mode runs work without hand-authoring a pose).

import { readFileSync } from "node:fs";
import { fuseIdentificationToSurface } from "./surface_fusion.js";
import { planSurfaceExperience } from "./surface_placement.js";
import { toBrainRoom, toBrainPose } from "./room_contract_adapter.js";

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
// the part's resolved region instead of the whole object.
function partTarget(labeled, identification, concept) {
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
      return { objectId: labeled.objectId, part: part.label };
    }
  }
  return null;
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
  const concept = { ...DEFAULT_CONCEPT, ...(input.concept || {}) };

  const { contract, labeled, summary, mode } = planSurfaceExperience({
    identification, room, pose, concept,
    fuse: fuseIdentificationToSurface,
  });
  const target = partTarget(labeled, identification, concept);

  process.stdout.write(JSON.stringify({
    mode,
    summary,
    fused: labeled
      ? { surfaceId: labeled.surfaceId ?? null, kind: labeled.kind,
          label: labeled.label, confidence: labeled.confidence,
          matchReason: labeled.matchReason,
          ...(labeled.objectId
            ? { objectId: labeled.objectId,
                supportSurfaceId: labeled.supportSurfaceId ?? null }
            : {}) }
      : null,
    ...(target ? { target } : {}),
    roomMeta: room.meta,
    contract,
  }));
}

main();
