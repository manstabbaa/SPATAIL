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
  if (!room || room.surfaces.length === 0) {
    console.error("plan_from_room: no usable surfaces in room input");
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

  process.stdout.write(JSON.stringify({
    mode,
    summary,
    fused: labeled
      ? { surfaceId: labeled.surfaceId, kind: labeled.kind,
          label: labeled.label, confidence: labeled.confidence,
          matchReason: labeled.matchReason }
      : null,
    roomMeta: room.meta,
    contract,
  }));
}

main();
