# tools/sync — retired (2026-07-03 teardown)

This directory used to hold the SpatailPlayer-era cross-machine sync tooling:

- `gen_swift_vocab.mjs` — generated `ios/SpatailPlayer/.../Contract/Vocab.swift`
  (closed Swift enums) from `pipeline/spatail/experience_contract.js`.
- `check_protocol_sync.mjs` — CI guard for schema-version / vocab / bundle-spec
  drift between the JS contract, the generated Swift, and `docs/xr/`.
- `copy_demo_bundle.mjs` — copied `bundles/*.spatail` into the SpatailPlayer
  SwiftPM Resources.
- `git-sync.sh` / `git-push.sh` — the pre-pivot two-machine git ritual
  (stale since the studio pivot; plain `git` is the workflow now).

**All of it was retired with the SpatailPlayer lineage in the 2026-07-03
teardown** (the last commit carrying `ios/SpatailPlayer` is `6f34fdd`,
2026-07-02 — recover from git history if ever needed). The codegen was NOT
repointed at the new app on purpose: `ios/Spatail`'s hand-written Contracts
(`ios/Spatail/Sources/Contracts/`) deliberately decode contract vocabularies as
raw Strings so unknown values never break decoding, and the generated top-level
enum names (`Placement`, `ContentType`, `Fidelity`, …) collide with the app's
own domain types (e.g. `PlacementSolver.Placement`).

The `.spatail` format and protocol documentation remain in `docs/`
(`docs/xr/REALTIME_PROTOCOL.md`, `docs/xr/IOS_BUNDLE_SPEC.md`,
`docs/xr/LIVE_BRAIN_SPEC.md`); `pipeline/spatail/experience_contract.js` is
still the single source of truth for contract vocabulary values.
