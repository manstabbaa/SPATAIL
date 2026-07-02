# SPATAIL 2026 — Master File → Build Pivot

> Source: `SPATAIL 2026 MF.pdf` (47 slides). This document distills the Master File,
> states the pivot, and gives a **per-page, 3-sentence build plan** for every content
> slide of the SYSTEM. It supersedes prior direction notes where they conflict.

---

## 1. What the Master File actually says

**Vision (p2):** *"A future where ideas exist as experiences that can be explored, manipulated, and shared in the world around us."*

**Mission / Product (p3–p6):** SPATAIL turns **information into spatial understanding** — it takes concepts, documents, objects, and real-world tasks and decides **what** to show, **where** it appears, **how** it's scaled, and **how** you interact, so digital content becomes meaningful in physical space. The product loop is **IDEA → BUILD → PRESENT**. Purpose: *"make spatial computing useful, understandable, and actionable."*

**Manifesto (p7):** ideas should live beyond flat slides/PDFs/renders; the platform is **brand-adaptive** (it should feel like the customer's brand, not ours) and **technology disappears** — the idea stays in focus.

**The SYSTEM = 5 pillars (p8–p9):**

| Pillar | Sub-topics | One-line meaning |
|---|---|---|
| **MATTER** | Why it appears · What appears · How it appears | The *intent*, the *content/taxonomy*, and the *materialization* of what shows up |
| **SPACE** | Mapping · Placement · Spatial takeover | Scan the room, then place content by *meaning* (attach/overlay/surround/compare/stage) |
| **IDENTITY** | Brand graphic guide · Render style guides · Personalization | The look — brand tokens + art direction (3D pastel / Tarka), customer-skinnable |
| **INTERACTION** | Interaction rules · Object behaviours · Gestures | Gesture→intent→behaviour; objects carry an interaction contract; genre-agnostic |
| **CREATION** | Pipeline · Craft · Build | How experiences get made (the studio pipeline) |

> In *this* version of the MF, **MATTER, SPACE, INTERACTION** are richly developed;
> **IDENTITY** and **CREATION** are present as section dividers but not yet fleshed out.
> We treat IDENTITY (brand) and CREATION (pipeline) as **owned by us to define**, with
> IDENTITY's brand tokens supplied by the user later (matches Scene Contract `brand: pending`).

---

## 2. The pivot decision

The MF is **not** a new codebase — it's a **re-frame of what we already have** plus one front-end change.

### 2a. The one real change: the client/dev surface becomes **WebXR**
"Start with the iOS-as-visionOS **web** viewer." We make a **browser-based, visionOS-style spatial viewer (WebXR + Three.js)** the new primary client and dev surface. It:
- runs on **PC today** (flat 3D preview in any browser — this is the dev tool),
- goes **immersive on headsets** later (WebXR runs in the Quest / Vision Pro / Android XR browsers),
- consumes the **same scene contract** the PC brain already emits (`schemas/spatialExperienceContract.schema.json`, whose own description says *"Read by the web viewer today and the visionOS player tomorrow"*).

This **de-risks** the native Android XR work (memory: `studio_android_xr_pivot`): WebXR is genuinely cross-platform, so one runtime previews on PC now and ships to headsets later, and the native Kotlin/Jetpack-XR client becomes an optimization, not a prerequisite.

### 2b. Keep-live (the whole PC brain + asset pipeline — already format-agnostic, GLB-first)
- **Job server / API machine:** `studio/server/job_server.py` (Tailscale spine, `/modular`, `/jobs`, `/assets/...`, `/factory/*`, `/health`).
- **The brain (Claude + Gemini):** `studio/server/llm_author.py` (Claude writes/repairs Blender-Python), `studio/director/llm.py` (Gemini sequencer), `studio/director/{composer,experience,vision,scene_contract}.py` (the experience/scene contract — **the core IP**), `studio/representation/` (prompt→experience planner).
- **Placement Design System:** `studio/spatail/{design_system,placement_solver,room_model,object_size}.py` (intent/constraints, not coords).
- **Asset artist (Meshy + Gemini):** `studio/meshy/*` (image→3D, normalized **GLB**).
- **Vision-guided ingest (Gemini):** `studio/vision/*` (reorient/real-scale/repair + verify).
- **Asset factory:** `asset_factory/*` (raw GLB/OBJ/FBX/STL → fixed-bounds GLB).
- **Blender scene construction:** `studio/blender/build_studio.py` (+ `realworld.py`, `motion.py`, `spatial_placer.py`).
- **Served library:** `public/assets/spatail-library/**` (~18 categories of GLB) + manifests + contract schemas.

### 2c. Retarget (small, surgical — drop the Apple format coupling)
- **USDZ companion exports** become opt-in; **GLB is the deliverable** (`studio/blender/build_studio.py`, `asset_factory/export_asset.py`, `studio/meshy/meshy_normalize.py`).
- **Publish/register staging** (`asset_factory/publish_to_spatail.py`, `studio/meshy/present.py`) registers the **GLB** for the web/XR client instead of USDZ.
- **Scene Contract consumer** retargets from the Swift MR runtime to the **WebXR viewer** (and later the Kotlin backend). The contract itself is unchanged.

### 2d. Freeze as reference (Apple client code — do not build on, don't delete)
- `ios/Spatail/**` (Swift/RealityKit app, iOS 27 object tracking), `ios/SpatailEngine/**` (its ECS design is the blueprint for the WebXR/Kotlin runtime, but the Swift code is frozen), `visionos_export/`, `docs/apple-*`, `tools/mac/`.
- **Parked/legacy (per `docs/LEGACY.md`):** `engineexplainer/`, `studio/asset_factory/` (procedural), `studio/library/{catalog,builders}.py`, `pipeline/blender/*` (except `spatail_live_blender.py`), `studio/mechanics/`, `studio/educational/`.

### 2e. The pipeline, restated to match the MF
```
PROMPT / PHOTO / VIDEO
   │
   ▼  MATTER · "Why it appears"  →  Purpose Layer (intent classifier)        [director brain: Gemini/Claude]
   │      experienceType (taxonomy of 12) + goal + "why"
   ▼  MATTER · "What appears"    →  asset selection / generation              [Meshy + library + asset_factory]
   │      GLB assets + asset-package manifest (parts/sockets/clips)
   ▼  MATTER · "How it appears"  →  materialize (spawn-in / loading state)    [viewer shaders]
   │
   ▼  SPACE  · Mapping           →  RoomModel (scan)                          [ARCore/ARKit/WebXR; studio/spatail/room_model.py]
   ▼  SPACE  · Placement         →  placement INTENT (attach/overlay/         [studio/spatail/placement_solver.py]
   │                                surround/compare/stage), not coords
   ▼  IDENTITY                   →  brand tokens + render style               [brand layer — user-supplied tokens]
   ▼  INTERACTION                →  per-object behavior contract +            [scene_contract.logic + viewer runtime]
   │                                gesture→intent map
   ▼  CREATION                   →  build + serve                            [job_server + Blender + library]
   │
   ▼  StudioSceneContract  ───────►  WebXR VIEWER (PC now / headset later)
```

---

## 3. Per-page build plan (SYSTEM content slides)

> Scope per request: **excludes** the title (p1), the front-matter "main" pages (p2–p7),
> the SYSTEM contents (p8–p9) and every single-word section divider
> (p10, p11, p14, p17, p20, p21, p25, p30, p31, p37, p44). Each entry below is the
> 3-sentence plan for **how I build that slide's capability into the pipeline.**

### MATTER — why / what / how it appears

**p12 — PURPOSE LAYER (intent before anything).** Build the intent classifier as the pipeline's first stage by reusing the director brain (`studio/director/composer.py` + Gemini `director/llm.py` / Claude `server/llm_author.py`): a natural-language prompt (plus an optional photo via `director/vision.py`) maps to an intent record `{goal, experienceType, why}`. I formalize the slide's five examples ("explain a V8" → understand parts, "show this chair" → scale/fit, "PDF → XR" → make abstract concrete, "replace an air filter" → guide a task, "compare three TVs" → judge size) into an `intent`/`experienceType` enum that every downstream stage reads off `understanding`. The WebXR viewer renders this "why it appears" reason on each identified element, so the Purpose Layer is visible and debuggable rather than implicit.

**p13 — DIRECTORS STORY (narrative spine).** Keep the director as the story layer: `studio/director/experience.py` + `scene_contract.py` already emit an ordered `logic.sequence` / `attentionPlan` of beats, which is exactly the "journey/quest map" the slide pictures. Each beat names its assets and a placement intent, so an experience is an authored scene graph, not a flat slideshow. The viewer plays the sequence as a step-through attention track, letting me author and inspect the story beat-by-beat on PC before it reaches a headset.

**p15 — SPATAIL TAXONOMY (12 experience types).** Encode the twelve types (Presentation, Lesson, Guide, Simulation, Game, Sandbox, Comparison, Inspection, Story, Training, Overlay, Dashboard) as a first-class `experienceType` taxonomy the Purpose Layer selects and the composer specializes on. Each type binds to a default representation-mode + interaction-format set (reusing the v0.2 contract's `representationMode`/`interaction` enums — e.g. Inspection → `exploded_view`, Overlay → `diagnostic_overlay`, Dashboard → `wall_dashboard`). The 3D-pastel / Peter-Tarka art direction becomes the default IDENTITY style token applied at generation (Meshy style refs) and in the viewer's render settings.

**p16 — TAXONOMY FLEXIBILITY (soft, not rigid).** Treat the taxonomy as a *prior*, not a hard switch: the classifier returns a weighted blend over experience types and the composer interpolates (e.g. "Guide + Simulation") instead of locking one archetype. This lives in the director's planning step as a confidence/weight field on `understanding.experienceType`, keeping generation extensible to novel forms. The viewer shows the chosen blend so I can see *why* an experience came out the way it did and nudge it.

**p18 — MATTER APPEARING (loading state that lives in the room).** Build a placeholder/loading representation (particle or fiber/flow-field shader) shown while an asset is still generating, reusing the existing `studio/runtime/progressive_loader.py` notion of a staged swap. The viewer ships a `MatterAppearing` material that animates a coalescing field at the object's reserved bounds and is replaced by the final GLB on completion. Because the placeholder occupies the real placement and bounds, it can already respond to the surroundings before the mesh is ready — matching the slide.

**p19 — SPAWNING IN (voxel/particle materialize).** Implement a "spawn-in" transition: when the final GLB resolves, dissolve it in from a voxel/particle swarm sampled across its bounding volume (GPU points → mesh reveal). This is a viewer-side effect keyed off the contract's `attentionBehavior`/load event, with the SPATAIL wordmark available as a decal per the brand layer. It reuses our clean pastel style so educational/explanatory assets materialize consistently.

### SPACE — mapping & placement

**p22 — SCANNING: APPLE (iOS).** Keep ARKit/RoomPlan + LiDAR as one **producer** of the semantic `RoomModel` (surfaces, obstacles, user pose, light) — our `studio/spatail/room_model.py` already models exactly this. For the WebXR surface, the equivalent producer is WebXR plane/mesh detection (and on PC, a mock/loaded room). The point is one normalized `RoomModel` schema with swappable scanners, so iOS data (when used) and WebXR data feed the same placement solver.

**p23 — SCAN: APPLE (visionOS).** The visionOS capability list (world tracking, plane detection, scene reconstruction, room tracking, image anchoring, hand tracking) becomes the **capability interface** every scanner backend must satisfy. visionOS stays a *reference* backend (frozen client), not a build target. The WebXR runtime implements the same interface against WebXR Device API features, so the contract above it never changes.

**p24 — SCAN: ANDROID XR.** Android XR (Jetpack XR / ARCore / OpenXR) is the native headset backend behind the same capability interface as p23. We build it later as an optimization; today the **WebXR browser on Android XR** gives us the same scan features without native code. This keeps a single cross-platform `RoomModel` so nothing above the scanner cares which OS produced it.

**p26 — Placement is reference, not coordinates.** This is already our Placement Design System: `studio/spatail/placement_solver.py` resolves **intent** ("attach / overlay / surround / compare / stage") against the live `RoomModel` rather than spawning at fixed coords. I extend the classifier to answer the slide's four questions (what is the user looking at? what object/space do they mean? real-world or standalone? attach/overlay/surround/compare/stage?) and write the result into the contract's `placement.designSystem`. The viewer renders the resolved placement and exposes the chosen relation in the identify overlay, so placement decisions are inspectable.

**p27 — Placement follows the job (baby-proofing).** Add a **functional-placement** mode where content anchors to the *install site* (table edges, corners) instead of a centered preview, driven by measured geometry. I compute edge length / corner count / surface type from the `RoomModel` and emit per-region anchors plus a quantity/BOM, then map quantities to purchasable products (a new `commerce` block on the element). The viewer draws per-edge/per-corner overlays with dimension callouts and a BOM summary card, reproducing the slide's annotated table.

**p28 — Placement requires recognition + reconstruction.** Build the recognition path: camera/photo → Gemini/Claude vision (`studio/director/vision.py`, `studio/vision/`) identifies the specific real component (e.g. intake manifold), and we **trace** its shape/location to overlay a registered highlight rather than a generic label. The output is a `highlightRegion` (silhouette/mask + transform) bound to the recognized part, which our master-material highlight system already supports (memory: `studio_master_material`). In the viewer this shows as a glowing registered overlay conforming to the part — and it is the **same code path the live-video-feed idea feeds** (see §5).

**p29 — UI placement follows the locus of control.** Encode a `locus` field per UI element — `object-local` / `body-local` / `world-local` — so controls attach to the site of meaning (a lamp's panel pins to the lamp; a gesture HUD pins to the hand). The placement solver already distinguishes anchor types; I add the locus rule and a per-object control-panel generator (style/material/brightness/color) driven by the object's affordance contract. The viewer renders object-local panels pinned in 3D and body-local panels following a tracked hand/controller.

### INTERACTION — rules, object behaviours, gestures

**p32 — Interaction as meaning, not input.** Model interaction as a **semantic layer**: a recognized gesture (pinch/point/grab/pull/swipe) carries no fixed action; the active experience assigns its meaning at runtime. I add an `interactionMap` to the contract's `logic` that binds abstract intents to handlers per experience, decoupled from raw input. The viewer's input layer emits canonical events (`select/move/scale/...`) that the experience's map routes — so one pinch can shoot, select, or pull apart an engine depending on context.

**p33 — Gestures as mappable game logic.** Make gesture→action **data-driven and genre-aware**: when the Purpose Layer picks "shooter," the composer emits the genre's interaction grammar (finger-gun → fire event → projectile + hit detection + replication), not just assets. These bindings are authored into `logic.triggers`, so a gesture "becomes a trigger because the experience requires it." The viewer runs a small trigger VM that executes these bindings, keeping the system genre-agnostic.

**p34 — Native input, custom interaction layer.** Build the device-agnostic input pipeline the slide diagrams: `native input → SPATAIL input abstraction → experience interaction map → spatial behaviour`. The abstraction is a thin adapter set (WebXR controllers/hands, gaze+pinch, touch, voice; later Vision Pro / Quest / XREAL / Android XR), each normalizing to the same canonical intents. This means "shoot" can come from a hand gesture on one device and a trigger on another while the experience logic stays identical.

**p35 — Objects carry interaction affordances.** Every generated object arrives with an **interaction contract** (what it can do, what you can do to it, how it reacts) — door: open/close/lock/peek; engine: rotate/explode/isolate/label/animate; ball: throw/bounce/catch; panel: select/scroll/filter; weapon: aim/fire/reload. We generate this affordance metadata alongside geometry (extending the asset-package manifest and the asset brief), so behavior ships with the mesh. The viewer reads affordances to spawn the right runtime handlers and the per-object action ring shown on the slide.

**p36 — Interaction format matches the experience.** The chosen `experienceType` selects the **interaction format**: presentation → sequencing/reveal; game → fast input/feedback/scoring/rules; design tool → comparison/scale/manipulation; education → guidance/labels/replay. This is a lookup the composer applies so the same prompt yields different interaction styles ("how gravity works" → simulation; "compare these chairs" → placement+scale; "make this a shooter" → gesture game logic). Interaction becomes the explicit bridge from intent to experience type, recorded on the contract.

> **Framing (corrected): aliveness is EXISTENCE, not motion.** An object is alive by being
> authentically *what it is, in context* — its behaviour is **dictated by its nature
> crossed with the user's intent**, and **stillness is as alive as motion**. A chair being
> evaluated for fit is alive by sitting there real, grounded, at true scale, STILL; a heart
> by beating; an engine by running; a planet by turning. So the runtime classifies each
> object's **nature-in-context** — `still / mechanical / vital / pendular / celestial /
> organic / creature` — and lets that dictate behaviour. It is NOT a generic breathe/bob,
> and NOT a control panel of verbs (those survive only as optional nudges). Implemented as
> `natureFor` + `updateLife` in `webxr/viewer.js`, mirrored in `ios/SpatailViewer`.
> Verified: chair/TV→still (motionless presence), heart→vital (beat), pendulum→pendular
> (swing), planet→celestial (turn+radiance), V8→mechanical (runs).
> **Proper home: the brain's `understanding` should emit the nature** so the runtime just
> expresses it — the keyword classifier here is the on-device stand-in.

**p38 — Objects have purpose (a living thing, not a mesh).** An object becomes meaningful when it answers a human intention, so it arrives *alive* — vitality (subtle breathing, idle drift) and presence the moment it materializes, before any interaction. Its different states (placed, exploded, ghost/X-ray, named parts) are **expressions of that life surfaced by intent and attention**, not a manual state-switcher: looking at it wakes it; the contract's intent sets its character. Our master material + region/anchor system (`studio_master_material`, `studio_story_first_pipeline`) supplies the emissive/highlight/part hooks the life layer drives.

**p39 — Behavior IS meaning (always alive).** The object is never static: its breathing, idle motion, and the way it turns toward you and brightens when you attend to it — that ongoing life *is* the meaning the slide names. Intent tunes its vitality (energy → breathe/idle/glow amplitude), and baked clips (`studio/blender/motion.py`, the asset-package clip list) ride on top for nature-specific motion (a heart beats, a plant sways and grows). `updateLife` runs this each frame for every object; the awareness response (face + glow + halo) is what makes it read as *living* rather than merely animated.

**p40 — The object serves the question (roles).** Route the user's question to an object **role** — recognize / explain / repair / compare — over a single reusable asset. The director already does intent→plan; I add a role resolver so "fix my car" → repair (numbered steps + tool callouts + exploded parts), "what is this" → recognize (vision highlight), "which is better" → compare (side-by-side at real scale). The viewer renders each role from the same GLB, so one engine asset teaches, repairs, or compares without regeneration.

**p41 — Intent classification (explain/compare/simulate/guide/place/transform).** Implement intent classification as the explicit first pipeline stage returning one (or a blend) of explain / compare / simulate / guide / place / transform. Each intent has a generator recipe: explain → annotated exploded diagram; simulate → physics with motion viz; guide → directional-arrow steps; place → ground-plane anchor; transform → redesign variant. This is the same classifier as p12/p36, surfaced here at the object level and stored on the element so the viewer can show which intent produced the current view.

**p42 — Behavior contract (per object).** Give every placed object a structured **behavior contract**: `{intent, anchor, scale, position, interaction, state, comfortRules}` — this is precisely our Scene Contract `placement` + element fields, plus an added `comfort` block. The on-device/in-viewer solver enforces anchor/scale/position and comfort (viewing distance/height) per object, and serializes interaction/state. The viewer renders the contract as the on-object UI badges the slide shows, making each object's rules inspectable.

**p43 — Adaptive runtime (a living thing that keeps responding).** Once placed, the entity keeps *living* and adapting — it tracks you, faces you, and brightens when attended; its idle vitality and intent-expression continue without input, and the explicit verbs (highlight / explode / isolate-a-subpart / resize) are nudges layered on a thing that is already alive. This is the living-entity layer + the runtime trigger VM applying the behavior contract over time (`studio/runtime/interaction_orchestrator.py`). The viewer drives all of it per-frame from the same contract — e.g. attend to one organ in a skeleton and it wakes, glows, and turns while the rest fall quiet.

**p45 — Gesture must respect the OS.** Map each platform's native gesture system to one unified **spatial intent** (Vision Pro eye+pinch, XREAL ray/phone, iPhone AR tap-drag, Quest controller ray/trigger → "Move Object"), rather than inventing gestures. This is the input-abstraction layer (p34) with per-platform adapters that defer to OS conventions. The WebXR viewer implements the hands/controllers/gaze adapters now; native adapters slot in behind the same intent interface later.

**p46 — Gesture must match the spatial response.** Enforce `Gesture → Intent → native spatial response`, where the response style is chosen by intent: variations → spatial UI panels; physics → simulation; mechanical → exploded/moving parts; comparison → side-by-side; guidance → anchored steps; editing → direct manipulation. This is a response-selector in the runtime keyed off the resolved intent + experienceType. The viewer dispatches the gesture to the matching response so a single pinch "feels intelligent" in context.

**p47 — SPATAIL translates gesture into intent.** Make universal **intent** the contract and the gesture local: the experience defines intent (select/move/compare/isolate/scale/explain/assemble/guide/transform); the OS defines the native gesture; the runtime picks the behaviour; the object responds in real time. This is the synthesis of p32–p46 — one `intent` vocabulary, per-platform gesture adapters, a runtime response-selector — and it's what makes one experience portable across PC-WebXR, headset-WebXR, and native clients. The viewer is the first concrete consumer, proving the gesture→intent→response loop on PC before any headset build.

---

## 4. The dev tools (built + verified)

All live in **`webxr/`** as one app. Built and verified in-browser (engine + solar-system
scenes, 14.9k triangles drawn, all behaviors exercised):

1. **WebXR content-creation viewer (PC).** A visionOS-style spatial viewer (Three.js + WebXR) that loads a **SPATAIL scene contract** + its GLBs and renders the room-scale scene; runs flat-3D on PC and immersive on a headset browser (`Enter XR`). The new dev/preview surface for everything the brain emits.
2. **Identify / Hitbox overlay (the button).** Draws, for every identified element, its **AABB hitbox**, **label**, **intent/why** (`whyThisRepresentation`/`whyThisPlacement`), **anchor strategy**, and **affordances** — a live view of MATTER + SPACE + INTERACTION for debugging what the system thinks is in the scene.
3. **Object-local control panels (MF p29/p35).** Affordance buttons pinned to each object that drive **real** behaviors — rotate, isolate (dim the rest), explode (parts fly from the centroid), scale, animate (GLB clip), reset.
4. **Spawn-in materialization (MF p18/p19).** Assets coalesce from a voxel/particle field as they load.
5. **Story track (MF p13).** Steps the attention plan, narrating each beat.
6. **Live video-intelligence (the side idea — built, see §5).**
7. **Live from the brain.** `✦ Generate` POSTs a prompt to `job_server /modular`, and an adapter projects the brain's v0.5/v0.6 contract into the web scene — **resolving placement intent (anchor/layout/footprints) into coordinates** with a layout solver, mapping `semanticActions`→affordances, and loading the resolved library GLB cross-origin. Verified: "the planet earth" → real `earth.glb` rendered from the live brain.

See `webxr/README.md` for run instructions and the web scene format (a thin projection of `spatialExperienceContract`).

---

## 5. Live running intelligence from a video feed (built)

Built as a *detection source* that feeds the identify/hitbox overlay — not a separate system. The MF demands this in three places: **p28** (recognize + reconstruct the real part), **p40 RECOGNIZE**, and the **Overlay** experience type (p15, "add intelligence to the real world"). It is a real-time **producer of identified elements** for the SPACE/MATTER recognition path.

**What's built (`webxr/live/detector_server.py` + the viewer's Live cam):**
- **Source:** webcam frames captured in the browser (WebXR passthrough on a headset).
- **Brain:** **Gemini** (`gemini-2.5-flash`) open-vocabulary "what + where", reusing the REST + strict-JSON pattern from `studio/director/vision.py` / `studio/vision/review_gemini.py`. Verified: on a rendered engine frame it returned `engine block 95%, crankshaft 95%, camshaft 95%, piston 95%, spark plug 95%…` with boxes; on the frog render it found the frog, its eyes and legs.
- **Output:** `detections[] = {label, confidence, intent_hint, bbox, position, sizeMeters}` — the **same shape** the viewer's overlay consumes, so live detections and authored elements render through one path. CORS + the browser→detector→Gemini round-trip are verified end-to-end.
- **Closing the loop:** a confirmed detection is a placement-recognition target (p28) — the system can trace/highlight the part, attach a control panel (p29), or spawn a guided repair (p40).

This turns the "Identify/Hitbox" tool into the **product's perception layer**, reusing the Gemini infra we keep live.
