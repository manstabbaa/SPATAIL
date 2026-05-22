# Cross-Machine Sync Workflow

Two machines, one product. The Windows machine owns the Blender pipeline + server. The Macbook owns the iOS player. The two must stay in lockstep without drift.

This doc is the manual for that.

---

## 1. Topology

```
                    ┌─────────────────────────┐
                    │   GitHub  (one repo)    │ ← single source of truth
                    └────────────┬────────────┘
                                 │
              ┌──────────────────┼──────────────────┐
              │                                     │
     ┌────────▼─────────┐                  ┌────────▼─────────┐
     │   Windows        │                  │   Macbook        │
     │   ─────────      │                  │   ─────────      │
     │   pipeline/      │                  │   ios/           │
     │   skills/        │                  │   (Xcode dev)    │
     │   docs/          │  ← both edit →   │   docs/          │
     │   tools/sync/    │                  │   tools/sync/    │
     │   bundles/       │                  │   bundles/       │
     └──────────────────┘                  └──────────────────┘
              │                                     │
              ▼                                     ▼
        Blender + server                        iOS device
```

**Rule of thumb:** if a change crosses the wire (anything in `docs/xr/`, `pipeline/spatail/experience_contract.js`, `pipeline/server/`, or `ios/`), it ships in a single git commit that touches both sides.

---

## 2. The three "boundary files" that must never drift

| File | Lives | Mirrored to | How |
|---|---|---|---|
| `pipeline/spatail/experience_contract.js` | JS | `ios/SpatailPlayer/Sources/SpatailPlayer/Contract/Vocab.swift` | Codegen: `npm run sync:swift-vocab` |
| `docs/xr/REALTIME_PROTOCOL.md` | Markdown | `ios/SpatailPlayer/Sources/SpatailPlayer/Session/SessionEvent.swift` + `pipeline/server/spatail_session_server.py` | Manual review; CI check via `npm run sync:check` |
| `docs/xr/IOS_BUNDLE_SPEC.md` | Markdown | `pipeline/blender/spatail_export_xr.py` + `ios/SpatailPlayer/Sources/SpatailPlayer/Bundle/Manifest.swift` | Manual review; schema version validated |

The schema versions (`0.5.0-spatail`, `0.5.0-spatail-bundle`) are the **canonical sync tokens**. If they match across files, the systems are compatible.

---

## 3. Codegen: the closed vocabs

The JS contract defines closed-enum vocabularies (`MECHANIC_KINDS`, `ANIMATION_PRIMITIVES`, etc.). iOS needs Swift enums with the **exact same string raw values**. Drift here = silent bugs.

Run on either machine:

```bash
npm run sync:swift-vocab
```

This reads `pipeline/spatail/experience_contract.js` and overwrites `ios/SpatailPlayer/Sources/SpatailPlayer/Contract/Vocab.swift`. The file starts with `// ⚠️ GENERATED — DO NOT EDIT BY HAND` so PR reviewers catch hand edits.

**Convention: never hand-edit `Vocab.swift`.** Add a new vocab entry in JS, run codegen, commit both files together.

---

## 4. Schema-version protocol

The `schemaVersion` field is the wire-compat handshake:

- Server emits `bundleSchemaVersion` in `session.ready`.
- iOS declares `supportedBundleSchemaVersions` in `session.start`.
- Mismatch → server replies with `error.bundle_schema_mismatch` and closes.

**To bump a schema:**

1. Edit `pipeline/spatail/experience_contract.js`:`SPATAIL_SCHEMA_VERSION` (e.g. `0.5.0-spatail` → `0.6.0-spatail`).
2. Run `npm run sync:swift-vocab` to regenerate `Vocab.swift`.
3. Update the version constant in `pipeline/server/spatail_session_server.py` and `ios/.../Manifest.swift`.
4. Add an entry to `docs/xr/SCHEMA_CHANGELOG.md` describing the breaking change.
5. PR title: `protocol: bump to 0.6.0` — anyone reviewing instantly knows it's a wire-touching change.

Older bundles must keep working until you explicitly drop a version from `supportedBundleSchemaVersions` in either side.

---

## 5. Branch / PR conventions

- **`main`** is always shippable on both sides. Never push raw.
- **Feature branches** are scoped to one side when possible: `windows/merge-tuning`, `ios/picker-ui`, `protocol/auth-flow`.
- **Cross-boundary branches** are prefixed `protocol/` and **must** touch both the protocol doc and at least one consumer on each side in the same PR.
- **CI hook**: `npm run sync:check` runs on PR. It verifies:
  - `Vocab.swift` was regenerated if the JS contract changed.
  - Schema version strings match across `experience_contract.js` / `spatail_session_server.py` / `Manifest.swift`.
  - All `MECHANIC_KINDS` listed in JS have a Swift case.

PR template (paste into description):

```markdown
### Cross-boundary checklist
- [ ] If protocol changed: `docs/xr/REALTIME_PROTOCOL.md` updated
- [ ] If vocab changed: `npm run sync:swift-vocab` was run
- [ ] If schema version bumped: both server + Manifest.swift updated
- [ ] Affected mechanic renderers have stub on the side that didn't lead
```

---

## 6. Test artefacts that flow between machines

| Artefact | Built on | Consumed on | Location |
|---|---|---|---|
| `f1_wheel_buttons.spatail` | Windows (`spatail_export_xr`) | Mac (iOS testbed) | `bundles/` in repo |
| `four_stroke_demo.spatail` | Windows | Mac | `bundles/` |
| Recorded `room.update` event traces | Mac (ARKit replay logs) | Windows (server testing) | `tests/fixtures/room_traces/` |
| Recorded `experience.delta` snapshots | Windows (orchestrator runs) | Mac (UI smoke tests) | `tests/fixtures/contracts/` |

`bundles/` is git-tracked at low resolution (the 3 MB wheel bundle is fine). For anything > 10 MB, use git-lfs or an S3 mirror; otherwise the repo bloats.

---

## 7. Daily flow (each morning)

**Windows:**
```bash
git pull
# work on pipeline / server
git add -p && git commit
git push
```

**Macbook:**
```bash
git pull
# work on iOS in Xcode
# if anyone bumped vocab/protocol, run:
npm run sync:check   # confirms my Swift code still matches the contract
git add -p && git commit
git push
```

When you change something cross-boundary on one machine, the other machine's CI guard will catch staleness on next pull — `npm run sync:check` returns non-zero with a diff hint.

---

## 8. When in doubt: where does X belong?

| Decision | Lives |
|---|---|
| Wire format (event names, payload fields) | `docs/xr/REALTIME_PROTOCOL.md` |
| Closed-vocab values (mechanic names, animation primitives) | `pipeline/spatail/experience_contract.js` |
| Bundle layout (file paths inside `.spatail`) | `docs/xr/IOS_BUNDLE_SPEC.md` |
| iOS Swift behavior | `ios/SpatailPlayer/Sources/...` |
| Blender behavior | `pipeline/blender/...` + `skills/...` |
| Server routing / orchestration | `pipeline/server/...` |
| **The reason something is structured this way** | `docs/xr/SYNC_WORKFLOW.md` (this file) |

If you find yourself editing two of the rows above for the same change, add a codegen tool to `tools/sync/` so the next change is automatic.
