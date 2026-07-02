/*
 * SPATAIL Developer Log — data
 * ---------------------------------------------------------------------------
 * This is the single source of truth for the log. It is plain JSON assigned
 * to a global, so the site opens straight from index.html (no server / no
 * CORS). Edit entries by hand to keep them understandable + summarized, OR
 * run `node scripts/gen-log.mjs` to pull new git commits in as `draft`
 * entries you then refine.
 *
 * Entry shape:
 * {
 *   id:        unique string (kebab-case)
 *   date:      "YYYY-MM-DD"
 *   title:     short headline
 *   category:  feature | fix | refactor | tooling | docs | direction
 *   status:    shipped | in-progress | draft
 *   area:      free-text grouping, e.g. "iOS / SPATAILMobileAR"
 *   summary:   one understandable sentence
 *   details:   [ "bullet", ... ]      // what changed
 *   why:       "why we did it"          // optional
 *   tags:      [ "ios", "ARKit", ... ]
 *   files:     [ "path", ... ]          // optional
 *   commits:   [ "abc1234", ... ]       // optional, short hashes
 * }
 *
 * Keep this file as valid JSON inside the array (no comments between items) so
 * the generator can read + rewrite it.
 */
window.SPATAIL_LOG = [
  {
    "id": "2026-06-26-developer-log",
    "date": "2026-06-26",
    "title": "SPATAIL Developer Log",
    "category": "tooling",
    "status": "shipped",
    "area": "Dev tools / mydevelopertools",
    "summary": "Built this log itself — a self-contained, on-brand site to track and summarize what we change.",
    "details": [
      "Static site in mydevelopertools/ — opens straight from index.html, zero dependencies.",
      "SPATAIL design system: the app's exact palette, the gradient wordmark, monospaced meta, category accent stripes.",
      "Curated entries plus scripts/gen-log.mjs to pull new git commits in as editable drafts.",
      "Filter by category, click a tag to filter, free-text search, expandable cards."
    ],
    "why": "So progress stays visible and readable instead of buried in raw git history.",
    "tags": ["web", "design-system", "tooling"],
    "files": ["mydevelopertools/index.html", "mydevelopertools/app.js", "mydevelopertools/styles.css"],
    "commits": []
  },
  {
    "id": "2026-06-26-perception-buildout",
    "date": "2026-06-26",
    "title": "Real detection, LiDAR depth & USDZ loading",
    "category": "feature",
    "status": "shipped",
    "area": "iOS / SPATAILMobileAR",
    "summary": "Filled in every stub of the perception pipeline so it works on a real device, not just with mocks.",
    "details": [
      "VisionDetectionService — REAL detection with no model file: Apple Vision saliency + image classification + animal/human detectors.",
      "DepthSampler — real LiDAR depth-map unprojection (camera intrinsics) with a surface-normal estimate, wired depth-first in the resolver.",
      "Runtime now loads real USDZ models asynchronously (bundle or Documents), fits + grounds them, and swaps out the placeholder.",
      "PerceptionNarrator (offline explanation copy) + AssetCatalogBuilder (matches detections to available models).",
      "Core ML path now loads a bundled ObjectDetector model automatically if present."
    ],
    "why": "The first pass proved the pipeline with a mock; this makes it actually perceive the world with zero extra setup.",
    "tags": ["ios", "Vision", "CoreML", "LiDAR", "RealityKit", "perception"],
    "files": [
      "SPATAILMobileAR/Perception/Detection/VisionDetectionService.swift",
      "SPATAILMobileAR/Perception/Spatial/DepthSampler.swift",
      "SPATAILMobileAR/Perception/Engine/PerceptionNarrator.swift"
    ],
    "commits": ["8278a1a"]
  },
  {
    "id": "2026-06-26-perception-pipeline",
    "date": "2026-06-26",
    "title": "Live perception → placement pipeline",
    "category": "feature",
    "status": "shipped",
    "area": "iOS / SPATAILMobileAR",
    "summary": "Wired live visual detection + spatial computing into the iOS app to place RealityKit content where it belongs.",
    "details": [
      "Four decoupled stages: Vision detects WHAT, ARKit resolves WHERE, SPATAIL decides WHAT APPEARS, RealityKit anchors it.",
      "Codable contracts (SpatailPerceptionFrame / SpatailPlacementPlan) that are pure and portable — no ARKit in the brain.",
      "Mock-first: a center-screen detection drives the whole loop so it can be validated before any model.",
      "Every placed object carries its reason — intent, target, anchor, scale, interaction (Master File behavior contract).",
      "Added as a separate Perception/ module + a 'Live Perception' demo card; existing AR code untouched."
    ],
    "why": "Move the app from a simulated base mesh to contextual placement driven by what the camera actually sees.",
    "tags": ["ios", "ARKit", "RealityKit", "perception", "architecture"],
    "files": ["SPATAILMobileAR/Perception/"],
    "commits": ["8278a1a"]
  },
  {
    "id": "2026-06-26-adversarial-review",
    "date": "2026-06-26",
    "title": "Two adversarial review passes",
    "category": "tooling",
    "status": "shipped",
    "area": "iOS / Quality",
    "summary": "Verified the new pipeline with multi-agent review since there's no Mac compiler on the authoring machine.",
    "details": [
      "Pass 1 (core pipeline): 7 findings, all fixed — incl. a Combine/RealityKit Cancellable ambiguity and an AnchorEntity transform bug that would teleport placements.",
      "Pass 2 (build-out): 5 findings, 0 confirmed — all dismissed as graceful degradation or already-handled.",
      "Each finding was independently, skeptically re-verified before being acted on."
    ],
    "why": "Catch the bugs a compiler would, when there isn't one in reach.",
    "tags": ["quality", "review", "ios"],
    "files": [],
    "commits": []
  },
  {
    "id": "2026-06-26-ios-unfrozen-direction",
    "date": "2026-06-26",
    "title": "iOS un-frozen for live perception",
    "category": "direction",
    "status": "shipped",
    "area": "Direction",
    "summary": "Reopened iOS for the perception work without abandoning the WebXR direction.",
    "details": [
      "The perception contracts are pure/portable, so the same packet can serve the visionOS / Android-XR / WebXR players later.",
      "iOS becomes a live testbed for the SPATAIL placement brain, not a competing surface."
    ],
    "why": "The task needed a real ARKit device path; keeping the contracts portable means it still feeds the long-term plan.",
    "tags": ["direction", "strategy"],
    "files": [],
    "commits": []
  },
  {
    "id": "2026-06-24-ar-placement-rewrite",
    "date": "2026-06-24",
    "title": "AR placement rewrite (plane anchor + gestures)",
    "category": "refactor",
    "status": "shipped",
    "area": "iOS / SpatailPlayer",
    "summary": "Rewrote AR placement using a plane anchor plus RealityKit gestures, matching AR Quick Look behavior.",
    "details": [
      "Plane-anchored content with native pinch / rotate / drag gestures.",
      "Replaces the earlier ad-hoc placement that fought the user."
    ],
    "why": "Make placing and handling objects feel like the system AR experience people already know.",
    "tags": ["ios", "ARKit", "RealityKit"],
    "files": [],
    "commits": ["a8fdb3d"]
  },
  {
    "id": "2026-06-24-scale-normalization",
    "date": "2026-06-24",
    "title": "Scene scale fixed from real bounds",
    "category": "fix",
    "status": "shipped",
    "area": "iOS / SpatailPlayer",
    "summary": "Objects were the wrong size — now scale is computed from actual visual bounds, not a misread manifest box.",
    "details": [
      "Compute scale from the entity's real visualBounds instead of assuming the manifest bbox was in meters.",
      "Earlier steps normalized scene scale from the manifest boundingBox and added scale diagnostics."
    ],
    "why": "Tabletop objects were spawning at the wrong size; grounding scale in real geometry fixes it.",
    "tags": ["ios", "RealityKit", "scale"],
    "files": [],
    "commits": ["e0a4bad", "3832875"]
  }
];
