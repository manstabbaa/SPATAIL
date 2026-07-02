# SPATAIL AI Asset Factory

Production pipeline that ingests **AI-generated 3D assets** (Meshy / Tripo / Rodin /
Luma / …) and turns them into predictable, fixed-bounds GLB packages SPATAIL can
consume later.

```
raw/generated 3D asset
  → Blender import → inspection → conservative cleanup
  → fixed-bounds normalization → origin normalization
  → preview → GLB export → manifest/report → validation
```

**Non-goal:** this does *not* do XR / ARKit / Vision Pro / RealityKit placement,
scene interaction, or educational runtime behaviour. SPATAIL handles placement
downstream. (It is also distinct from `studio/asset_factory/`, which *generates*
assets from plans — this one *ingests + normalizes* existing assets.)

## Core rule

Every exported asset fits inside the configured fixed bounds volume and uses the
configured origin mode (`bottom_center` by default).

## Layout

```
assets_raw/<group>/...            # drop raw asset folders here (one folder per asset)
assets_processed/<group>/         # outputs (git-ignored, regenerable)
  <group>.normalized.glb
  asset_manifest.json
  asset_report.json
  preview.png
  worker_log.txt
assets_processed/batch_report.json
asset_factory/
  worker_manager.py     # batch manager (normal Python, NO bpy)
  process_one_asset.py  # single-asset worker (runs INSIDE Blender)
  config.py reports.py validation.py            # bpy-free shared code
  importers.py inspect_asset.py cleanup.py      # bpy-only (Blender) modules
  normalize_bounds.py export_asset.py preview.py
  config/  cache/scratch/  reports/  previews/  workers/  logs/
```

## Run it

Batch (recommended) — spawns one isolated Blender process per asset, in parallel:

```bash
python asset_factory/worker_manager.py --input assets_raw --output assets_processed
python asset_factory/worker_manager.py --input assets_raw --output assets_processed --max-workers 8
python asset_factory/worker_manager.py --input assets_raw --asset-timeout-seconds 900
```

Single asset (direct Blender invocation):

```bash
blender --background --python asset_factory/process_one_asset.py -- \
    --asset-id v8_engine --input assets_raw --output assets_processed
```

`BLENDER_EXE` (env) or `--blender` overrides the Blender path; the default is the
repo's pinned `C:\Program Files\Blender Foundation\Blender 5.1\blender.exe`.

## Config

Global: `asset_factory/config/default_asset_factory_config.json`

```json
{
  "target_bounds_m": { "x": 1.0, "y": 1.0, "z": 1.0 },
  "bounds_mode": "fit_longest_axis",
  "origin_mode": "bottom_center",
  "unit_system": "meters",
  "generate_preview": true,
  "generate_collision_proxy": false,
  "generate_lods": false
}
```

Per-asset override (wins key-by-key): `assets_raw/<group>/asset_factory_config.json`.

Worker/runtime: `asset_factory/config/worker_config.json`

```json
{
  "max_workers": "auto",
  "asset_timeout_seconds": 600,
  "preview_engine": "workbench",
  "preview_resolution": 768,
  "use_gpu_for_preview": true,
  "copy_assets_to_local_scratch": true,
  "scratch_dir": "asset_factory/cache/scratch"
}
```

`max_workers: "auto"` derives a conservative count from system RAM
(`<64GB→2, 64–127→4, 128–255→8, ≥256→12`); set a positive integer to pin it.
`--max-workers` on the CLI always wins.

### Bounds modes

| mode | behaviour |
|------|-----------|
| `fit_longest_axis` *(default)* | uniform scale; largest source dim = largest target dim (preserves proportions) |
| `fit_inside_box` | uniform scale by the most-constraining axis so it fits inside x/y/z |
| `stretch_to_box` | non-uniform scale to exactly fill x/y/z (can distort; not default) |

### Origin modes

| mode | behaviour |
|------|-----------|
| `bottom_center` *(default)* | x/y centred on origin, bbox bottom on z=0 |
| `center` | bbox centre at the world origin |

## Test a processed asset on your phone (SPATAIL iOS app)

iOS can't render GLB, so the app uses **USDZ**. To view a normalized asset in the
Spatail app **with no Xcode rebuild**, publish it into the SPATAIL library
and load it via the app's existing **Representation** flow:

```bash
# 1. process the asset (assets_raw/<id>/ -> assets_processed/<id>/)
python asset_factory/worker_manager.py --input assets_raw --output assets_processed --only <id>

# 2. publish: builds USDZ if needed, copies GLB+USDZ into the served library,
#    registers it under a SUBJECT so the engine resolves it as a library hit
python asset_factory/publish_to_spatail.py --asset-id <id> --subject "a coffee mug"

# 3. start the server the phone talks to (Tailscale-reachable on 0.0.0.0:8787)
python studio/server/job_server.py
```

Then on the phone: **Generate something new → Server →** set the printed Tailscale
URL (e.g. `http://<pc>.<tailnet>.ts.net:8787`) **→ mode "Representation" →** type the
**subject → Represent**. The app downloads the USDZ and renders your normalized
asset in AR (the Representation flow runs *dry* — no Blender wait).

Notes:
* `publish_to_spatail.py` registers into `public/assets/spatail-library/<category>/`
  + `manifests/generated.json` (default category `ingested`). It prints a
  verification line and the exact phone steps. Resolution is by subject tags, so
  type a prompt containing the subject words.
* `--export-usdz` (worker flag) or `"export_usdz": true` (config) makes the factory
  emit `<id>.normalized.usdz` alongside the GLB on a normal run.

## Crash isolation & robustness

* One Blender process **per asset** — a crash/hang/bad-import fails only that asset.
* Hard per-asset timeout (`--asset-timeout-seconds`).
* The batch continues on failure; every asset (success or fail) gets a report.
* `assets_processed/batch_report.json` + a clear console summary are always written.
* `asset_factory/logs/worker_manager.log` records manager-level events.
