# assets_raw/ — drop raw AI-generated assets here

Each **subfolder is one asset group**. Drop the files an AI 3D generator (Meshy /
Tripo / Rodin / Luma / …) gave you into a folder named after the asset:

```
assets_raw/
  v8_engine/
    model.glb            # or .gltf / .obj(+.mtl) / .fbx / .stl
    textures/
  chair/
    chair.obj
    chair.mtl
    textures/
  my_asset/
    asset_factory_config.json   # optional per-asset overrides (see below)
```

Then process everything with one command:

```bash
python asset_factory/worker_manager.py --input assets_raw --output assets_processed
```

Outputs land in `assets_processed/<group>/` (git-ignored, regenerable):
`<group>.normalized.glb`, `asset_manifest.json`, `asset_report.json`,
`preview.png`, `worker_log.txt`, plus `assets_processed/batch_report.json`.

## Per-asset config override (optional)

Drop an `asset_factory_config.json` in a group folder to override the global
defaults (`asset_factory/config/default_asset_factory_config.json`) for that asset
only — for example:

```json
{
  "bounds_mode": "fit_inside_box",
  "origin_mode": "center",
  "generate_collision_proxy": true,
  "generate_lods": true
}
```

See `asset_factory/README.md` for the full pipeline, config reference, and bounds/
origin mode tables.
