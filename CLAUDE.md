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

## Project orientation

- The product spec is the **SPATAIL 2026 Master File** → `docs/spatail_2026_mf_pivot.md`
  (5 pillars: MATTER / SPACE / IDENTITY / INTERACTION / CREATION).
- The client/dev surface is the **WebXR viewer** in `webxr/` — runs on PC and immersive on
  headsets from the same code. It consumes the scene contract the PC brain emits.
- Keep the PC brain live (job server, Blender spine, Claude + Gemini, Meshy, asset factories,
  GLB library). Asset format is **glTF/GLB**. iOS/visionOS Swift is frozen reference only.
- Standing project facts and current direction live in the auto-memory index
  (`MEMORY.md`); the active direction is `studio_mf_2026_pivot.md`.
