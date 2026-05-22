// Assembles the SpatialSceneContract from inventory + Blender analyses.
// The contract is the shared source of truth for the web viewer today and
// the Vision Pro runtime later. Every field has a defined meaning, even
// when it's left empty in v0.1.

import { inferLikelyUseCase, inferRepresentationMode } from "./classifier.js";

const SCHEMA_VERSION = "0.1.0";

const DEFAULT_INTERACTION_BRICKS = [
  {
    id: "reset_view",
    type: "reset_view",
    target: "scene",
    trigger: "ui",
    behavior: "reset camera and object transforms",
    purpose: "return user to the default view",
  },
  {
    id: "highlight_primary",
    type: "highlight",
    target: "primary_object",
    trigger: "ui",
    behavior: "visually highlight the primary object",
    purpose: "help user identify the main asset",
  },
  {
    id: "isolate_primary",
    type: "isolate",
    target: "primary_object",
    trigger: "ui",
    behavior: "hide non-primary objects if present",
    purpose: "focus user attention",
  },
  {
    id: "explode_view",
    type: "explode",
    target: "all_components",
    trigger: "ui",
    behavior:
      "move components outward from center if multiple objects exist; otherwise no-op",
    purpose: "show part relationships",
  },
];

const DEFAULT_UI_ELEMENTS = [
  { id: "btn_reset_view", type: "button", label: "Reset View",
    action: "reset_view", target: "scene",
    visibleIn: ["viewer", "vision_pro"] },
  { id: "btn_highlight", type: "button", label: "Highlight",
    action: "highlight_primary", target: "primary_object",
    visibleIn: ["viewer", "vision_pro"] },
  { id: "btn_isolate", type: "button", label: "Isolate",
    action: "isolate_primary", target: "primary_object",
    visibleIn: ["viewer", "vision_pro"] },
  { id: "btn_explode", type: "button", label: "Explode",
    action: "explode_view", target: "all_components",
    visibleIn: ["viewer", "vision_pro"] },
];

function pickPrimary(assetEntries) {
  return assetEntries.find((a) => a.role === "primary_object") || assetEntries[0];
}

export function buildContract({ inventory, classification, analyses }) {
  const primary = pickPrimary(inventory);
  const detectedDomain = classification.detectedDomain;
  const representationMode = inferRepresentationMode(classification, inventory);

  return {
    sceneName: classification.sceneName,
    version: SCHEMA_VERSION,
    createdAt: new Date().toISOString(),

    assets: inventory.map((a) => ({
      id: a.id,
      fileName: a.fileName,
      fileType: a.extension.replace(/^\./, ""),
      sourcePath: a.relativePath,
      processedPath: a.processedPath || null,
      detectedObjectName: a.detectedObjectName,
      role: a.role,
      status: a.status,
    })),

    assetAnalysis: analyses.map((an) => ({
      assetId: an.assetId,
      importer: an.importer || null,
      status: an.status,
      reason: an.reason || null,
      objectCount: an.metrics?.objectCount ?? null,
      vertexCount: an.metrics?.vertexCount ?? null,
      faceCount: an.metrics?.faceCount ?? null,
      dimensionsMeters: an.metrics?.dimensionsMeters ?? null,
      bbox: an.metrics?.bbox ?? null,
    })),

    spatialUnderstanding: {
      detectedDomain,
      domainConfidence: classification.domainConfidence,
      primaryObject: primary ? primary.detectedObjectName : "",
      likelyUseCase: inferLikelyUseCase(classification),
      representationMode,
    },

    placement: {
      anchorType: representationMode === "real_scale" ? "floor" : "table",
      positionStrategy: "in_front_of_user",
      scaleMode: representationMode === "real_scale" ? "true_scale" : "fit_to_volume",
      safeDistanceMeters: 1.5,
      facesUser: true,
    },

    orientationRules: {
      // glTF / Blender export uses +Y up by convention.
      upAxis: "+Y",
      forwardAxis: "-Z",
      autoCorrect: true,
      notes:
        "STEP/STP imports may arrive with +Z up; the viewer auto-frames so this " +
        "mostly self-corrects. Tighter handling lands in v0.2.",
    },

    interactionBricks: DEFAULT_INTERACTION_BRICKS,
    uiElements: DEFAULT_UI_ELEMENTS,

    storySequence: [
      {
        id: "step_overview",
        title: "Overview",
        description: `Inspect the ${classification.sceneName.toLowerCase()}.`,
        suggestedAction: "highlight_primary",
      },
      {
        id: "step_explore",
        title: "Explore",
        description:
          inventory.length > 1
            ? "Use Explode to see how the parts fit together."
            : "Orbit the model to see it from any angle.",
        suggestedAction: inventory.length > 1 ? "explode_view" : "reset_view",
      },
    ],

    validationRules: [
      { id: "must_have_loadable_asset",
        rule: "at least one asset must have status='processed'",
        severity: "error" },
      { id: "warn_no_primary",
        rule: "scene should mark exactly one asset as primary_object",
        severity: "warn" },
    ],
  };
}
