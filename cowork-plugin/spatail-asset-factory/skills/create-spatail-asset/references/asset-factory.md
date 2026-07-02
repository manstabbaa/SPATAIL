# SPATAIL AI Asset Factory — reference

Detailed knowledge for the `create-spatail-asset` skill. Read on demand.

## What it does (and does not)

Ingests messy AI-generated 3D assets and normalizes them into predictable,
fixed-bounds GLB packages:

```
raw model → import → inspect → conservative cleanup → fixed-bounds normalize
→ origin normalize → preview → export GLB (+ USDZ) → manifest/report → validate
```

It does **not** do XR/ARKit placement, scene logic, or generate meshes from text.

## Outputs (per asset, in `assets_processed/<asset_id>/`)

- `<asset_id>.normalized.glb` — the normalized model (Y-up, metres)
- `<asset_id>.normalized.usdz` — RealityKit/AR Quick Look (only with `--export-usdz`)
- `preview.png` — 768² QA render
- `asset_manifest.json` — compact contract (status, bounds, scale, counts, files)
- `asset_report.json` — deep inspection + cleanup ops + validation + timings
- `worker_log.txt` — per-asset log
- plus `assets_processed/batch_report.json` and a console summary

## Global config — `asset_factory/config/default_asset_factory_config.json`

```json
{
  "target_bounds_m": { "x": 1.0, "y": 1.0, "z": 1.0 },
  "bounds_mode": "fit_longest_axis",
  "origin_mode": "bottom_center",
  "unit_system": "meters",
  "generate_preview": true,
  "generate_collision_proxy": false,
  "generate_lods": false,
  "export_usdz": false
}
```

### Per-asset override

Drop `assets_raw/<asset_id>/asset_factory_config.json` to override any key for that
asset only. Example (fit inside a box, centre the origin, also emit a collision proxy
and LODs):

```json
{
  "bounds_mode": "fit_inside_box",
  "origin_mode": "center",
  "generate_collision_proxy": true,
  "generate_lods": true,
  "export_usdz": true
}
```

### Bounds modes

| mode | behaviour |
|------|-----------|
| `fit_longest_axis` *(default)* | uniform scale; longest source dim = longest target dim (keeps proportions) |
| `fit_inside_box` | uniform scale by the most-constraining axis so it fits inside x/y/z |
| `stretch_to_box` | non-uniform scale to exactly fill x/y/z (can distort; not default) |

### Origin modes

| mode | behaviour |
|------|-----------|
| `bottom_center` *(default)* | x/y centred, bbox bottom on z=0 (rests on a surface) |
| `center` | bbox centre at the origin |

## Worker config — `asset_factory/config/worker_config.json`

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

`max_workers: "auto"` derives a conservative count from RAM (`<64GB→2, 64–127→4,
128–255→8, ≥256→12`). `--max-workers N` on the CLI always wins.

## Commands

```bash
# normalize one / all groups (USDZ for iOS)
python asset_factory/worker_manager.py --input assets_raw --output assets_processed --export-usdz [--only <id>]
python asset_factory/worker_manager.py --input assets_raw --output assets_processed --max-workers 8
python asset_factory/worker_manager.py --input assets_raw --asset-timeout-seconds 900

# single asset directly through Blender
blender --background --python asset_factory/process_one_asset.py -- --asset-id <id> --input assets_raw --output assets_processed --export-usdz

# live web viewer (3D + iOS AR Quick Look), serves assets_processed/
python asset_factory/web_viewer.py            # http://127.0.0.1:8790/  (and over Tailscale)

# publish into the SPATAIL app library under a subject
python asset_factory/publish_to_spatail.py --asset-id <id> --subject "a gear"
```

Blender path: set `BLENDER_EXE`, or it defaults to
`C:\Program Files\Blender Foundation\Blender 5.1\blender.exe`. Pass `--blender <path>`
to worker_manager to override.

## Troubleshooting

- **"Blender not found"** — install Blender 5.1 or set `BLENDER_EXE`.
- **One asset failed, batch continued** — open that asset's `asset_report.json`
  (`errors`) / `worker_log.txt`. Each asset runs in its own process, so failures are
  isolated; the batch report records them.
- **Final bounds exceed target** — only possible with a non-cubic target + `fit_longest_axis`;
  use `fit_inside_box` for non-cubic targets.
- **No USDZ** — re-run with `--export-usdz` (or set `"export_usdz": true`).
- **"I just see primitives" in the iOS app** — the installed app's modular/representation
  flows show placeholder boxes; use the live web viewer (loads the real GLB/USDZ) or
  publish the asset and use the (rebuilt) Asset Factory screen.

## Generating new meshes (NOT the factory)

The factory normalizes existing models. To create a brand-new mesh from a text prompt,
a generator must produce the raw model first, then hand it to the factory:

- **Meshy image→3D pipeline** (`studio/meshy/`, paused): needs a Meshy API key in
  `~/.spatail/secrets.env`. Produces a GLB the factory can ingest.
- **Local Blender object generation** (`studio/server` `/jobs` object mode / modular):
  builds a model in Blender headless — no external key.

If the user wants this, confirm which generator they want to use and that its
prerequisites (API key / Blender) are in place, generate the raw model, drop it into
`assets_raw/<id>/`, then run the factory as in the main skill.
