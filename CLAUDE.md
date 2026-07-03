# SPATAIL — working instructions for Claude

## Build mentality (read this first)

**Build everything, now. No self-imposed limits.**

- Do **not** defer capabilities to "later", "next", "tomorrow", "phase 2", or "an optimization
  for the future". If a thing can be built, build it now — and build **all** of it, both/every
  part, not a slice. We are not waiting on another platform, another sprint, or another version.
- Do **not** place artificial scope rails on yourself or frame work as out of reach. There is no
  "the web viewer today and the native client tomorrow" — if both are needed, build both.
- When you catch yourself writing "we could later…", "this becomes a future step", or
  "left as a stub" — stop and just build it instead, unless there is a hard external blocker
  (a missing credential, a physical device, an explicit user decision). Name the blocker
  plainly; never use phasing as a substitute for effort.
- Ambition is the default. We can build whatever we want — match that energy. Ship complete,
  working, verified results, not a foundation with the hard parts postponed.

## What SPATAIL is

**SPATAIL is the layer where curiosity becomes spatial — ask anything, and the answer
takes form in your space.** Explanations, searches, inquiries, games: a Google result you
can stand inside. A genre-dense experience builder, not an app of demos.

The acceptance bar for the live stream is **the bottle-cap test**: point the phone at a
water bottle, ask about the cap, and the experience appears **on the cap** — locked,
stable, real scale.

## Project orientation

- The product spec is the **SPATAIL 2026 Master File** → `docs/spatail_2026_mf_pivot.md`
  (5 pillars: MATTER / SPACE / IDENTITY / INTERACTION / CREATION). **§6 (the 2026-07-02
  addendum) is canon** and wins wherever it conflicts with §2.
- The **product client is the iOS app `ios/Spatail/`** (xcodegen; bundle `dev.spatail.app`).
  iOS Swift is ACTIVE — the old "frozen reference" rule is dead. `ios/SpatailEngine/` is the
  tested, platform-agnostic contract-runtime package the app consumes. `xcode-select` points
  at CLT on this Mac, so prefix builds with `DEVELOPER_DIR=/Applications/Xcode.app`.
- The **PC brain stays live and is built ON, never rebuilt**: job server (`studio/server/`,
  :8787/8788), vision engine (`pipeline/server/`, ws :8798 / debug :8799), fusion brain
  (`pipeline/spatail/`), Blender spine (`studio/`), Meshy + vision QA (`studio/meshy/`,
  `studio/vision/`), asset factories, GLB library (`public/assets/`). Asset format is
  **glTF/GLB** (USDZ opt-in for iOS delivery).
- `webxr/` is the **PC dev surface**, not the product. It must fail the way the phone
  fails (strict asset mode) — it may never mask asset or contract errors.
- The **Live Spatial Brain** (TRACKED stream) is specced in `docs/xr/LIVE_BRAIN_SPEC.md` —
  normative for both sides of the wire. Three clocks (60 Hz ARKit form / 1–2 Hz on-device
  measurement / ~1 Hz VLM identity + parts) meet in the on-device ObjectRegistry. Its laws
  (latency split, threading, backpressure) are non-negotiable.
- **Verification-first: if you can't see what it saw, it doesn't ship.** Truth Overlay on
  device; persisted placement traces + 4-column attribution (Brain → Contract → Client →
  Asset) under `studio/out/traces/`, served by the job server.
- Standing project facts live in the auto-memory index (`MEMORY.md`); the active direction
  is `spatail_rebuild_direction_2026_07`.
