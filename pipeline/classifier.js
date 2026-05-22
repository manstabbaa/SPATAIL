// Filename- and folder-based heuristics for guessing what an asset *is*.
// This is the v0.1 stand-in for real visual / geometric classification.
// It's deliberately small: a tiny scored keyword matcher.

import path from "node:path";

// Each domain has a keyword list. A token match scores 1; the highest
// total wins. Ties are broken by domain order.
const DOMAIN_KEYWORDS = {
  vehicle: [
    "car", "auto", "automobile", "automodell", "vehicle",
    "porsche", "ferrari", "yamaha", "honda", "ducati", "bmw", "audi",
    "motorcycle", "bike", "scooter", "truck", "van", "bus",
    "tdm", "rc", "drone", "quadcopter", "aircraft", "plane", "helicopter",
    "boat", "ship", "submarine",
  ],
  mechanical: [
    "bearing", "rulman", "engine", "gear", "valve", "pump", "motor",
    "machine", "mechanism", "piston", "cam", "shaft", "bolt", "nut",
    "screw", "bracket", "flange", "coupling", "assembly", "assem",
    "part", "component", "tool", "fixture", "mount",
  ],
  furniture: [
    "chair", "armchair", "stool", "bench", "sofa", "couch",
    "table", "desk", "shelf", "cabinet", "drawer", "wardrobe",
    "bed", "lamp", "pod",
  ],
  product: [
    "lego", "minifig", "figurine", "toy", "doll",
    "phone", "headphone", "earbud", "speaker", "camera",
    "watch", "ring", "bottle", "mug", "cup", "vase",
    "gadget", "device", "wearable",
  ],
  anatomy: [
    "skull", "bone", "skeleton", "heart", "brain", "lung",
    "kidney", "liver", "organ", "body", "anatomy", "tooth", "dental",
  ],
  architecture: [
    "building", "house", "room", "wall", "floor", "ceiling",
    "door", "window", "stair", "interior", "exterior", "facade",
    "bridge", "tower", "structure",
  ],
  environment: [
    "terrain", "landscape", "rock", "tree", "plant", "scene",
    "ground", "sky", "cloud",
  ],
};

// Role hints — these match individual file names within a multi-part asset.
const PART_HINTS = [
  "left", "right", "front", "back", "side", "top", "bottom", "inner",
  "outer", "upper", "lower", "head", "arm", "leg", "body", "connector",
  "part1", "part2", "part3", "part4", "part5",
];

const PRIMARY_HINTS = [
  "assembly", "assem", "main", "body", "full", "complete", "model",
];

function tokenize(s) {
  return s
    .toLowerCase()
    .replace(/[_\-.()/\\]+/g, " ")
    .split(/\s+/)
    .filter(Boolean);
}

export function inferDomain(textTokens) {
  let bestDomain = "unknown";
  let bestScore = 0;
  for (const [domain, words] of Object.entries(DOMAIN_KEYWORDS)) {
    let score = 0;
    for (const w of words) {
      if (textTokens.includes(w)) score += 1;
    }
    if (score > bestScore) {
      bestScore = score;
      bestDomain = domain;
    }
  }
  return { domain: bestDomain, score: bestScore };
}

function prettyName(filenameNoExt) {
  return filenameNoExt
    .replace(/[_\-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

// Decide each asset's role within the group:
//   - If a single file is in the group, it's primary_object.
//   - If many files share a folder, the largest by vertex count is primary
//     (we don't have vertex counts yet, so fall back to filename hints).
//   - Files whose name matches a PART_HINT are components.
export function inferRoles(assetsForGroup) {
  if (assetsForGroup.length === 1) {
    return [{ ...assetsForGroup[0], role: "primary_object" }];
  }

  let primaryIdx = -1;
  for (let i = 0; i < assetsForGroup.length; i++) {
    const tokens = tokenize(assetsForGroup[i].fileName);
    if (PRIMARY_HINTS.some((h) => tokens.includes(h))) {
      primaryIdx = i;
      break;
    }
  }
  if (primaryIdx === -1) {
    // Fall back: pick the file whose name has the *fewest* part-hints
    // (parts have explicit "left", "arm" etc.; the whole is generic).
    let bestScore = Infinity;
    for (let i = 0; i < assetsForGroup.length; i++) {
      const tokens = tokenize(assetsForGroup[i].fileName);
      const hits = PART_HINTS.filter((h) => tokens.includes(h)).length;
      if (hits < bestScore) {
        bestScore = hits;
        primaryIdx = i;
      }
    }
  }

  return assetsForGroup.map((a, i) => ({
    ...a,
    role: i === primaryIdx ? "primary_object" : "component",
  }));
}

// Groups belong together if they share the top-level folder under
// /assets_raw — e.g. a CAD assembly with eight STEP parts in one folder
// is one group, the standalone .glb sitting next to it is another.
export function groupAssets(assets) {
  const groups = new Map();
  for (const a of assets) {
    const top = a.relativePath.split(/[\\/]/)[0] || a.fileName;
    const isLooseFile = top === a.fileName;
    const key = isLooseFile ? `__loose__:${a.fileName}` : top;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(a);
  }
  return [...groups.entries()].map(([groupKey, items]) => ({
    groupKey,
    items,
  }));
}

export function classifyGroup(group) {
  const allTokens = new Set();
  for (const item of group.items) {
    for (const t of tokenize(item.relativePath)) allTokens.add(t);
    for (const t of tokenize(path.basename(item.fileName, item.extension))) {
      allTokens.add(t);
    }
  }
  const tokenArr = [...allTokens];
  const { domain, score } = inferDomain(tokenArr);

  const sceneName = prettyName(
    group.groupKey.replace(/^__loose__:/, "").replace(/\.snapshot\.\d+$/, ""),
  );

  return {
    groupKey: group.groupKey,
    sceneName,
    detectedDomain: domain,
    domainConfidence: score === 0 ? "low" : score >= 2 ? "high" : "medium",
    tokens: tokenArr,
  };
}

// representationMode is a hint to the viewer / Vision Pro runtime about
// how to *show* the asset. Defaults to "inspection" when unsure.
export function inferRepresentationMode(classification, items) {
  if (items.length > 1) return "exploded_view";
  if (classification.detectedDomain === "vehicle") return "inspection";
  if (classification.detectedDomain === "architecture") return "real_scale";
  return "inspection";
}

export function inferLikelyUseCase(classification) {
  switch (classification.detectedDomain) {
    case "mechanical":
      return "engineering review and part inspection";
    case "vehicle":
      return "design review and visual walkaround";
    case "furniture":
      return "spatial placement preview";
    case "product":
      return "product showcase and inspection";
    case "anatomy":
      return "educational anatomical inspection";
    case "architecture":
      return "architectural walkthrough";
    case "environment":
      return "environment preview";
    default:
      return "general 3D inspection";
  }
}
