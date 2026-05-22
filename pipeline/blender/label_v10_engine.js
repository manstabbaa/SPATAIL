// label_v10_engine.js — visual-inspection labelling pass for the
// V10 engine OBJ. The four part TYPES (crank / head / pin / rod) were
// identified from the iso renders in the prior turn. This script
// applies the per-instance semantic block by computing cylinder index
// (1..5, by Z order along the crank axis) and bank (A=-X, B=+X).
//
// Output: enriched parts.json with full semantic blocks.

import fs from "node:fs";
const PATH = "C:/SPATAIL_MAX/assets_processed/segmented/v10_engine/v10_engine.parts.json";
const data = JSON.parse(fs.readFileSync(PATH, "utf-8"));

// ---- Map crank Z values → cylinder index 1..5 (front-to-back) ------------
const cranks = data.parts.filter((p) => p.id === "crank" || p.id.startsWith("crank:"));
// Sort by Z descending — the front of the engine has the largest +Z.
cranks.sort((a, b) => b.centroidWorld[2] - a.centroidWorld[2]);
const crankToCyl = new Map();
cranks.forEach((c, i) => crankToCyl.set(c.id, i + 1));

// Nearest-crank lookup for a given Z coordinate.
function cylinderForZ(z) {
  let best = cranks[0], bestD = Infinity;
  for (const c of cranks) {
    const d = Math.abs(c.centroidWorld[2] - z);
    if (d < bestD) { bestD = d; best = c; }
  }
  return crankToCyl.get(best.id);
}

function bankForX(x) {
  return x < 0 ? "A" : "B";
}

// ---- Apply labels --------------------------------------------------------
const REGEX = {
  crank: /^crank(:|$)/,
  head:  /^head(\s*\(1\))?(:|$)/,
  pin:   /^pin(\s*\(1\))?(:|$)/,
  rod:   /^rod(\s*\(1\))?(:|$)/,
};

let counts = { crank: 0, piston: 0, pin: 0, rod: 0 };

for (const p of data.parts) {
  const [x, y, z] = p.centroidWorld;
  const cyl = cylinderForZ(z);
  const bank = bankForX(x);

  if (REGEX.crank.test(p.id)) {
    const idx = crankToCyl.get(p.id);
    p.semantic = {
      name: `Crank throw #${idx} (journal + counterweight)`,
      role: "rotational input to powertrain",
      function:
        "One throw of the V10 crankshaft. The cylindrical post is the rod journal — " +
        "the eye of two connecting rods (one per bank) rides on it. The wider " +
        "section is the counterweight that balances the rotating + reciprocating mass.",
      cylinderIndex: idx,
      // Each crank throw mates with one rod from each bank.
      connectedTo: [`rod (bank A, cyl ${idx})`, `rod (bank B, cyl ${idx})`],
      motionDOF: "rotate_z (crankshaft axis runs along world +Z)",
      agentNotes:
        "Iso render shows a cylindrical post on a wider curved mass — canonical " +
        "throw silhouette. 5 instances along the crank axis at Z ≈ 15.5/3.5/-8.5/-20.5/-32.5 cm.",
    };
    counts.crank += 1;
  } else if (REGEX.head.test(p.id)) {
    p.semantic = {
      name: `Piston, cylinder ${cyl}, bank ${bank}`,
      role: "primary actuator",
      function:
        "Translates combustion pressure into linear motion along the cylinder axis. " +
        "Bank A is the -X bank, bank B is the +X bank. Combustion happens above the " +
        "piston crown; force is transmitted down through the wrist pin into the conrod.",
      cylinderIndex: cyl,
      bank,
      connectedTo: [`pin (bank ${bank}, cyl ${cyl})`, `rod (bank ${bank}, cyl ${cyl})`],
      motionDOF: "translate along cylinder axis (radial to crank, angled per bank)",
      agentNotes:
        "Author named the parts 'head' but the iso render is a piston body — short " +
        "cylinder with a wrist-pin notch on the side. 10 instances (5 per bank).",
    };
    counts.piston += 1;
  } else if (REGEX.pin.test(p.id)) {
    p.semantic = {
      name: `Wrist pin, cylinder ${cyl}, bank ${bank}`,
      role: "pivot link",
      function:
        "Slips through the piston's wrist-pin bore and the small-end eye of the " +
        "conrod. Lets the conrod pivot as the crank rotates while transmitting axial " +
        "force from piston to rod. Friction-controlled by piston-pin clips in the real article.",
      cylinderIndex: cyl,
      bank,
      connectedTo: [`piston (bank ${bank}, cyl ${cyl})`, `rod small-end (bank ${bank}, cyl ${cyl})`],
      motionDOF: "translates with piston; rotates relative to rod (1 DOF pivot)",
      agentNotes:
        "Long thin cylinder, 6 mm × 57 mm — canonical wrist-pin proportions. " +
        "9 instances in the OBJ (10th is missing — likely a CAD-author oversight).",
    };
    counts.pin += 1;
  } else if (REGEX.rod.test(p.id)) {
    p.semantic = {
      name: `Connecting rod, cylinder ${cyl}, bank ${bank}`,
      role: "force transmission linkage",
      function:
        "Converts the piston's linear motion into rotation at the crank journal. " +
        "Small end pivots on the wrist pin; big end (the circular eye) rides on the " +
        "crank journal. Tilts a few degrees as the crank rotates — this is what makes " +
        "the V-engine pack two banks of cylinders against a single crank.",
      cylinderIndex: cyl,
      bank,
      connectedTo: [
        `pin (bank ${bank}, cyl ${cyl})`,
        `piston (bank ${bank}, cyl ${cyl})`,
        `crank throw ${cyl}`,
      ],
      motionDOF:
        "small end translates+swings with piston; big end orbits the crank journal " +
        "(rotates about crank axis, offset by throw radius)",
      agentNotes:
        "Iso shows long arm + circular big-end eye — canonical I-beam conrod silhouette. " +
        "10 instances (5 per bank).",
    };
    counts.rod += 1;
  } else {
    p.semantic.agentNotes = "Unrecognised name pattern; left unlabelled.";
  }
}

data.labelledAt = new Date().toISOString();
data.labelledBy = "agent_visual_inspection_v10";
data.partTypeCounts = counts;
data.connectivitySummary =
  "V10 layout: 5 crank throws × 2 banks (A=-X, B=+X) = 10 cylinders. " +
  "Each cylinder is one (piston, pin, rod, crank-throw) tuple. " +
  "Connectivity is computed by cylinder index + bank, derived from world-Z proximity " +
  "to the nearest crank throw and the sign of world-X.";

fs.writeFileSync(PATH, JSON.stringify(data, null, 2));
console.log(`Labelled ${counts.crank + counts.piston + counts.pin + counts.rod} of ${data.partCount} parts`);
console.log("Counts:", counts);
