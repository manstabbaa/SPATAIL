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
    "id": "2026-07-05-brain-consumes-boxes",
    "date": "2026-07-05",
    "title": "The fusion brain CONSUMES the VLM's boxes — gaze, object binding, and part anchors are box-grounded",
    "category": "feature",
    "status": "in-progress",
    "area": "PC brain / fusion",
    "summary": "surface_fusion.js / plan_from_room.js now read identification boxes: the placement ray runs through the detection box center (not bare camera forward), same-noun objects are disambiguated by projected-footprint IoU, part boxes bias the anchor onto the part's world position, and every box-driven step lands in fused.decisionTrace — the consumption half of the near-but-not-on fix, on-branch behind the same founder gate as the producer half.",
    "details": [
      "Camera view model with NO new wire fields: frames are streamed .oriented(.right) (device-fixed portrait), so image right = pose.up and image down = forward × up EXACTLY, however the phone is held; the long image edge gets an assumed 67° FOV (SPATAIL_CAMERA_FOV_LONG; brainInput.camera pins it into every trace) and the short edge follows from identification.frameSize's aspect.",
      "Box-refined gaze: the surface ray casts through the primary box's center and only falls back to camera forward when the refined ray misses — the fix for 'camera ray pierced the table 40 cm from the bottle'.",
      "Box-grounded object binding: label-matched room.objects earn BOX_IOU_WEIGHT (2.0) × IoU(projected OBB rect, detection box); the seen bottle now beats the confident-but-behind-you bottle. Mirrored bit-for-bit into the engine's _binding_for replan gate (rects agree to 15 decimals across JS/Python), so §1.4 lockstep holds.",
      "Part anchor bias: a part-addressed target gains anchor {point, method, partBox} — ray through the part box striking the bound OBB (part_box_ray), else the part's position relative to the primary box mapped onto the OBB (part_box_relative). Object bindings also gain fused.hitPoint.",
      "Found + fixed live-wire bug: room.update sends concept as the user's ask STRING, and plan_from_room.js spread it into character keys — a part-addressed target could NEVER fire from the phone's Ask flow. Strings now normalize to {prompt}.",
      "Attribution: every box-driven step is a fused.decisionTrace line riding the experience.delta; LIVE_BRAIN_SPEC gains §1.6 documenting all additive contract fields (frameSize + camera in brainInput, hitPoint/decisionTrace/anchor in the plan).",
      "Verified: 38-check test_box_grounding.mjs + 27-check legacy suite green; 13-check Python gate mirror green; full-loop engine e2e over the WS uplink (debugIdentification, no VLM) shows the delta carrying decisionTrace + a cap anchor at y 0.94 on a 0.96-top bottle; ALL 782 archived brainInput traces replay with identical bindings (0 regressions), and the 10 box-carrying ones now emit decisionTrace — the real air-conditioner trace binds with projected IoU 0.69 and a box-ray hitPoint exactly on the OBB top face, validating the assumed-FOV camera model against live data."
    ],
    "why": "The producer half (2026-07-04) made boxes exist on the wire; the brain still placed by 'camera ray pierced <surface>' — the confirmed brain-side root cause of experiences landing near-but-not-on the identified object (the water-bottle-cap sessions). This is the half that makes the grounding steer placement.",
    "tags": ["fusion-brain", "grounding", "boxes", "placement", "decision-trace", "bottle-cap-test", "lockstep"],
    "files": [
      "pipeline/spatail/surface_fusion.js",
      "pipeline/spatail/plan_from_room.js",
      "pipeline/spatail/room_contract_adapter.js",
      "pipeline/server/spatail_vision_engine.py",
      "pipeline/spatail/tests/test_box_grounding.mjs",
      "pipeline/server/tests/test_box_binding_gate.py",
      "docs/xr/LIVE_BRAIN_SPEC.md"
    ]
  },
  {
    "id": "2026-07-04-vlm-box-normalization",
    "date": "2026-07-04",
    "title": "Vision engine stops throwing away the VLM's boxes — pixel grounding survives to the wire",
    "category": "fix",
    "status": "in-progress",
    "area": "PC brain / vision engine",
    "summary": "_clean_box dropped every pixel-space box the VLM returned (766/766 live plans had box: null), so the fusion brain never got pixel grounding; the engine now reads the frame size from the JPEG header and normalizes boxes to the wire contract — built and e2e-verified on a branch, deployment held for founder go-ahead.",
    "details": [
      "Root cause: grounding VLMs (qwen2.5vl) echo absolute pixel corner coordinates no matter what space the prompt asks for, and _clean_box dropped any box with values > 1.5 — identification.box and parts[].box were null in essentially every plan on 2026-07-03/04.",
      "_image_size(): dependency-free JPEG SOF / PNG IHDR header parse gives (width, height) for every uplinked frame; identify() threads it into _parse_identify_json → _clean_box / _clean_parts.",
      "Pixel boxes now normalize to the wire's 0..1 [x, y, w, h] (origin top-left — exactly what WireMessages.swift and the :8799 overlay already assumed); already-normalized replies pass through; results clamp to the unit square; degenerate boxes drop.",
      "The identify prompt now asks for pixel [x1, y1, x2, y2] — qwen's native grounding format (a warm 3b echoed xyxy even when asked for xywh); the parser reads corners first with an xywh fallback when the corner reading is geometrically impossible.",
      "IdentifyResult carries frameSize on the wire for attribution; the :8799 debug overlay draws parts[].box dashed so part-level grounding (the bottle-cap case) is visible over Parsec.",
      "Verified: 34 parser/header unit checks; end-to-end on an isolated test instance (:18798/:18799 — live engine untouched): synthetic bottle frame over the WS uplink → box [0.4069, 0.2875, 0.275, 0.5229] non-null in vision.identification and state.json (raw VLM reply was pixel xyxy [293, 276, 491, 778]), room.update → experience.delta, and the plan trace carries the box through brainInput.identification.",
      "NOT deployed to the live engine: the founder is mid-diagnosis on placement attribution and a silent behavior change would contaminate live-session evidence. Open follow-up: surface_fusion.js / plan_from_room.js still never READ boxes — teaching the brain to use the grounding (ray-through-box instead of bare camera ray) is the next step."
    ],
    "why": "Without boxes every plan fell back to 'camera ray pierced <surface>' — the leading brain-side explanation for experiences landing near-but-not-on the identified object (the water-bottle-cap sessions).",
    "tags": ["vision-engine", "vlm", "grounding", "qwen2.5vl", "traces", "bottle-cap-test"],
    "files": ["pipeline/server/spatail_vision_engine.py"],
    "commits": ["3676843"]
  },
  {
    "id": "2026-07-03-pc-bringup-rebuild",
    "date": "2026-07-03",
    "title": "PC brain live on the rebuild — the setting loop verified end to end",
    "category": "tooling",
    "status": "shipped",
    "area": "PC brain / stack",
    "summary": "Brought C:\\SPATAIL_MAX up on the one-app/one-engine/one-brain rebuild branch and proved the whole live loop on real hardware — gated replans, Blender-5.1 asset QA, staged garage generation through Meshy, library registration, webxr hot-swap, and 4-column placement attribution.",
    "details": [
      "Checked out claude/nice-wilbur-28fc94 on the canonical checkout; all three sanity suites green (settings SELFTEST, parity 5 fixtures, object-binding).",
      "Autostart swept: only stale pointers lived in tools/start_spatail_servers.ps1 (engineexplainer :5174/:5175, root viewer :5173 — removed); a leftover :5174 static server was killed; the panel's saved 90s VLM timeout pinned to the spec's 8s.",
      "Vision engine verified live: replan gating held 90s of car/aircraft-wheel label flapping to ONE delta at dwell 2 (15 deltas at SPATAIL_REPLAN_DWELL=1), pose.update never replans, objects-first noun binding over the wire, 16 plan traces under studio/out/traces/vision/.",
      "Asset QA first run (rubber duck): weld + shade-smooth-by-angle on Blender 5.1 with zero bpy exceptions, QA render + report beside the library export, normalizeQA stamped into job metadata, seam-free result.",
      "Setting system: use_llm:false returns the deterministic garage (engine_bay on the hood-open sedan) with REAL generationJobIds; both props ran Meshy, the car registered as sedan_car_with_its_hood_open and later /modular runs resolve it straight from the library.",
      "webxr viewer: honest materializing fields hot-swapped to the generated sedan + tool cart with live per-element Meshy progress; placement report POSTed and the /traces/view page filled all four columns (Brain decisionTrace verbatim).",
      "Seven findings reported (Gemini-bespoke vs template default, resolver over-match on 'engine', viewer label leak, transient artifact 503, empty parts[] from 3b, panel defaults, misleading swap toast) — fixed same-day on the Mac in b3a9329.",
      "Phone endpoints ready: mansourspc.tail922496.ts.net — vision ws :8798, job server :8788."
    ],
    "why": "The rebuild landed compile-checked from the Mac; the PC is where the brain actually runs, so every new wire behavior had to be proven against real Ollama, Blender, Meshy, and a browser before the phone's live loop goes on air.",
    "tags": ["windows", "vision-engine", "setting-system", "meshy", "webxr", "traces", "bring-up"],
    "files": ["tools/start_spatail_servers.ps1", "public/assets/spatail-library/manifests/generated.json", "studio/out/traces/vision/"],
    "commits": ["56d850c", "b3a9329"]
  },
  {
    "id": "2026-07-02-repo-consolidation",
    "date": "2026-07-02",
    "title": "One branch to rule them all — repo consolidated to main",
    "category": "tooling",
    "status": "shipped",
    "area": "Repo / git",
    "summary": "Merged every line of work (PC + Mac) into main and deleted all other branches and worktrees — clean slate.",
    "details": [
      "studio-pivot (81 commits + uncommitted PC tree: Spatail app rename, WebXR viewer, SpatailEngine, MF-pivot docs, CLAUDE.md) merged into main.",
      "claude/interesting-goldwasser (vision engine, surface fusion, Engine Viewer, Brain Panel — 11 commits) and claude/inspiring-davinci (iOS perception pipeline) merged in.",
      "Merge policy: studio-pivot's old 'retire off-direction modules' deletions were NOT allowed to kill the revived fusion-brain world — SPATAILMobileAR/, ios/SpatailPlayer/, pipeline/, viewer/ all survive as they stood on main.",
      "mydevelopertools/ (this site) was never committed anywhere — landed on main; gitignored Meshy library bakes + .env preserved through the merge.",
      "All merged branches deleted locally and on origin; origin now has exactly one branch: main."
    ],
    "why": "Work had spread across 7 branches and 5 worktrees on two machines; a single up-to-date main removes the where-is-what tax.",
    "tags": ["git", "housekeeping", "merge"],
    "files": [],
    "commits": ["8546ba9", "4a39528", "e3398ea", "4996284"]
  },
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
