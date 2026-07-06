# PERCEPTION V3 — the Entity Layer

Perception v2 (the Form Engine, `LIVE_BRAIN_SPEC` §3 + `Sources/Perception/Form/`)
measures *things*. V3 makes those things **entities**: one registry object per
physical thing at every scale (a couch, not five couch fragments), with real
form (seat + backrest + armrests, not one dumb box), rich identity ("keyboard,
blue keys, English QWERTY" — never "machine"), and placement affordances
(edges, centers, seats) the brain can target.

Field evidence this answers (founder session 2026-07-05, Truth Overlay
screenshots): two `couch · 0.95` objects of ~1.4 m each on one ~2.5 m couch that
can never merge (gates are bottle-scale); `Textile / Bedding / Wood Processed`
taxonomy junk surfacing as identity; every box stuck at `3 % arc` because
fragmentation splits the fused clouds; ghost boxes minted during motion-blurred
pans; a laundry pile on the couch that must become a *child* of the couch, not
a merge candidate.

Design north star, unchanged: **semantics pick the shape model, fused depth
supplies the dimensions.** Provenance stays honest end-to-end
(`measured | prior | roomplan`). Two canonized traps still hold: never *measure*
from `ARMeshAnchor` (grouping/extent evidence only), and transparent/glossy
breaks LiDAR.

---

## 0. The three relations (the heart of v3)

Two registry objects can relate in exactly three ways. Order of evaluation
matters — support runs FIRST so children are never merge candidates:

1. **supported-by (parent/child)** — a small object resting on / contained in a
   larger one (mug on table, laundry pile on couch). Child keeps its own id,
   OBB, identity, dossier; gains `parentId`. Never merged into the parent.
   Test: ≥ 70 % of the child's XZ footprint inside the parent's, child volume
   ≤ 25 % of the parent's, child bottom within the parent's vertical span
   (or within 6 cm above its top face), and NOT same-class.
2. **same-entity (merge)** — two observations of one physical thing. V2 gates
   (XZ IoU ≥ 0.4, or centers within max(0.12 m, 0.5 × smaller mean extent))
   stay for small objects; v3 adds the **class-scaled** branch: when either
   object's class (label or hint) has a scale prior, the center gate grows to
   the class gate, and **same-class adjacency** merges two fragments whose XZ
   footprints are within 15 % of the class length of touching (vertical
   overlap ≥ 25 % of the smaller). Two half-couches merge; stacked shelf
   levels still don't (vertical-overlap guard).
3. **part-of (assembly)** — named primitives inside one entity's form
   (§3 below). Never separate registry objects.

## 1. Class scale table (FormPriors extension)

`FormPriors.furniture` maps class tokens → canonical
`{length, depth, height}` (m) + association gate + assembly template:

| class tokens                | L×D×H (m)        | gate (m) | template |
|-----------------------------|------------------|----------|----------|
| couch, sofa, loveseat       | 2.0 × 0.9 × 0.8  | 1.2      | seating  |
| armchair                    | 0.9 × 0.9 × 0.8  | 0.6      | seating  |
| chair, stool                | 0.5 × 0.5 × 0.9  | 0.45     | seating  |
| bed, mattress               | 2.0 × 1.6 × 0.6  | 1.4      | slab     |
| table, desk                 | 1.4 × 0.8 × 0.75 | 0.9      | slabTop  |
| dresser, cabinet, shelf     | 1.0 × 0.5 × 1.2  | 0.7      | box      |
| tv, television, monitor     | 1.0 × 0.1 × 0.6  | 0.6      | box      |
| keyboard                    | 0.36 × 0.13 × 0.03 | 0.25   | box      |
| laptop                      | 0.32 × 0.22 × 0.02 | 0.25   | box      |
| rug, carpet                 | 2.0 × 1.5 × 0.02 | 1.2      | slab     |

(Guidance values — tune against the replay fixtures, keep the shape.)
`scaleClass(for:)` returns `.small | .furniture`; furniture selects the coarse
voxel tier and the assembly fitter.

## 2. Native furniture evidence

- **Mesh clusters** (`MeshClassificationClusters`): connected components of
  `.seat` / `.table` / `.bed` classified mesh faces, world-space XZ footprints,
  refreshed ≤ 0.5 Hz off-main. Evidence role ONLY: two objects whose centers
  fall in the same cluster count as same-class-adjacent for the merge pass, and
  a cluster footprint may EXTEND a merge gate — dimensions still come from
  fused depth. (Canon: never measure from ARMeshAnchor.)
- **RoomPlan** (`RoomPlanService`, iOS 17 custom-`ARSession` sharing the hub
  session, gated on `RoomCaptureSession.isSupported` + Settings toggle):
  `CapturedRoom.objects` (sofa/table/bed/chair/storage/television/…) ingest as
  entities with `label` (semantic), `form.source == .roomplan`, OBB from
  RoomPlan dims/transform. Matching an existing object: adopt the label, snap
  extents toward RoomPlan dims unless a fresh `.measured` assembly exists
  (measured beats prior beats roomplan for geometry; roomplan beats nothing for
  *identity* — it IS a semantic label). Runtime honesty: if starting RoomPlan
  degrades the shared session (smoothed depth stops arriving), stop it and note
  it in the Truth Overlay.

## 3. Compound form — `ObjectForm.assembly`

`ObjectForm.Kind` gains `.assembly`; `ObjectForm` gains additive
`primitives: [FormPrimitive]` where `FormPrimitive = {name, obb}` (world-space
`OrientedBox`, parent-yaw-aligned). `dimensions` keeps overall
`{width, depth, height}`. `Source` gains `.roomplan`.

`FormFitter.fitAssembly(points, template, …)` — pure math, harness-run:

- **seating** (couch/sofa/armchair/chair): overall yaw-OBB → local frame →
  height histogram → seat level = strongest horizontal density band in the
  25–70 % height range; backrest = rear-depth strip rising above seat level;
  armrests = lateral end strips above seat level (emitted only when point
  density supports them). Primitives: `seat`, `backrest`, `armrest_left`,
  `armrest_right`. Sanity: seat top 0.25–0.7 m above base, else single box.
- **slabTop** (table/desk): `top` slab at the dominant high band + `base`
  under-volume.
- **slab / box**: single primitive, same as v2 box fit.
- Point budget: assembly needs ≥ 600 fused points and ≥ 0.5 m footprint.

Furniture cloud tier (`FormPointCloud(tier: .furniture)`): 2 cm voxels,
30 k cap, reset-jump = max(0.6 m, 0.5 × class length) — half-couch detections
legitimately report centers ~0.7 m apart and must not reset the cloud.

## 4. Affordances (why v3 exists)

`RegistryCoherence.affordanceSlice(name, of: object, cameraPosition:)` resolves
placement regions **form-aware first**: with an assembly, `seat` / `seat_top` →
top 20 % of the seat slab; `backrest_top`, `armrest_left_top`,
`armrest_right_top` likewise; `front_edge` → front 15 % strip of the seat top;
`center` → seat-top center patch. Without an assembly it falls back to the v2
`namedSlice` 20 %/30 % OBB slices. Ask lexicon grows: armrest, cushion, seat,
edge, corner, middle/center. The placement solver keeps consuming
`PlacementTarget.part` — regions just got real.

## 5. Semantic depth — labels, hints, dossier

- **Label kinds.** `Detection2D.labelKind: .semantic | .hint` (additive).
  Apple `VNClassifyImageRequest` per-box results are **hints** unless the
  identifier passes the curated display allowlist (couch, chair, table,
  keyboard, laptop, tv, bottle, cup, book, plant, …) at conf ≥ 0.5. Junk/parent
  taxonomy ids (`machine`, `textile`, `bedding`, `wood_processed`, `device`,
  `furniture`, …) are NEVER displayable. Hints ride
  `SpatailObject.classHint` (device-local): they condition FormPriors,
  merge gates, and templates — they never render, never ride the wire as
  `label`, never win ask bindings.
- **Dossier.** `SpatailObject.attributes: ObjectAttributes?` (wire-additive):
  `{colors[], materials[], textContent[], language, brand, state}`. Merge:
  arrays union (bounded 6), scalars overwrite when fresher; device-local
  `attributesUpdatedAt`. The dossier is what ask-parsing reads first and what
  persistent spatial memory will save.
- **Identity flow.** VLM stays the deep-identity source. Ambient full-frame
  identify (~1 Hz) continues. NEW: **focus passes** (§7) round-robin the
  stalest display-worthy entity every ~5 s with a hi-res crop, so every
  established entity gets deep identity + attributes, not just the frame's
  primary.

## 6. Timestamp-true placement — KeyframeStore

`KeyframeStore` (ring, memory-budgeted ~60 s at ~2 Hz): per keyframe —
`timestamp` (ARKit uptime), camera transform + intrinsics + image resolution,
depth map + confidence (native 256×192, Float32/UInt8 copies), JPEG at ≤ 1920 px
long edge (the hi-res crop source), sharpness/motion score. Keyframes prefer
sharp, low-motion frames.

- **Binding.** Every VLM result binds to its keyframe by `frameTimestamp`
  (epoch→uptime via the streamer's sent-frame log, as today):
  `projector(for:)` uses the keyframe pose (no 0.75 s staleness cap, no
  live-camera fallback while panning); part boxes resolve against the
  keyframe's OWN depth+pose (`KeyframeGeometry` — pure math, harness-run),
  falling back to the live-frame resolver only when no keyframe covers the
  timestamp.
- **Motion gate.** The 1.5 Hz detect tick skips frames with angular velocity
  > 0.6 rad/s, translational velocity > 0.8 m/s (from the pose ring), or
  tracking ≠ normal — blurred frames mint ghosts and poison identity crops.
- **Visibility-aware expiry.** Expiry clocks only tick while an object's
  center projects into the current frustum: looking away never expires the
  couch; a ghost that IS in view and keeps missing detections dies on the
  fast clock (`missedWhileVisible ≥ 4` → unlabeled expiry even for labeled
  objects with no form).

## 7. Question-driven perception

`AskPlanner` (pure): prompt + registry → `EvidenceSpec {target, part?,
wantedAttributes, needsGeometry}`. Attribute keywords → colors/text/language/
brand/state/material; geometry keywords (edge/center/corner/on the) →
`needsGeometry`.

Ask flow becomes: resolve target (unchanged) → if the dossier already answers
(fresh < 60 s) skip perception → else **focus pass** on the target with the
question riding along (≤ 3.5 s wait, then proceed regardless) → brain ask as
today. If the target is too small in every recent keyframe (< 96 px min side)
surface guidance honestly: "Move closer to the <label> so I can read it."

Focus passes preempt the ambient round-robin (one in flight total, latest-wins
per object).

## 8. Wire additions (all additive; old peers ignore them)

Phone → PC:
- `room.update.objects[]` (SpatailObject Codable) gains `parentId`,
  `attributes`, and `form` gains `kind: "assembly"`, `primitives[]`,
  `source: "roomplan"`.
- NEW `vision.focus` (text JSON):
  `{type, payload: {requestId, objectId, question?, wanted?: [attr], jpegBase64,
  frameTimestamp}}` — a hi-res crop of ONE object + what's wanted from it.

PC → phone:
- `vision.identification.payload` gains `attributes` (of the primary).
- NEW `vision.focus.result`:
  `{requestId, objectId, label?, confidence?, attributes?, answer?,
  parts?: [{label, box?, confidence}], latencyMs, model}` — boxes normalized to
  the CROP's pixel space, converted phone-side back through the crop rect.

Engine prompt: ambient identify additionally requests
`attributes {colors, materials, text_content, language, brand, state}` for the
primary; the focus prompt is question-conditioned and identity-first. Box
normalization (pixel xyxy → normalized xywh) is unchanged.

## 9. Truth Overlay — entity-first

Default mode collapses to entities: one chip per display-worthy PARENT
(children roll up as `+N on it`), boxes drawn for labeled/selected entities
(assembly primitives when present), surfaces dimmed behind the existing
legend toggle. The full debug firehose stays one tap away (mode toggle).
Provenance lines stay: `measured · 41 % arc`, `prior`, `roomplan`.

## 10. Verification

- **Harness** (`ios/Spatail/Harness`, spec §0 law 5): new sections — coherence
  v3 (half-couches merge; laundry → child; shelf levels stay apart; class
  gates), assembly fitter (synthetic couch cloud → 4 primitives at the right
  heights), affordance slices, dossier merge, KeyframeGeometry
  back-projection round-trips, motion-gate math. `Harness/run.sh` is the one
  command.
- **Engine**: `pipeline/server/test_vision_engine_parse.py` — attribute/focus
  parsing, box normalization, prompt-shape guards (plain `python3`, no deps).
- **On-device acceptance = the founder's two scenes**: (1) pan the couch →
  ONE chip, assembly primitives tracing seat/back/arms, laundry as a child;
  (2) "what's on my keyboard?" → focus pass → "keyboard — blue keys, English
  QWERTY layout" with provenance.
