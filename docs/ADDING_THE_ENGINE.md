# Adding the Mustang engine asset

The Mustang demo card already declares an `engine_bay` spatial element
(see [demos/mustang-service-card.json](../demos/mustang-service-card.json)).
It looks for an asset group whose folder name matches `car-engine`. When
none is found the demo falls back to a labeled placeholder box — honest
but boring. This doc is the short recipe for replacing the placeholder
with a real engine.

## TL;DR

```
1. Open mesin mobil ANDRI.SLDASM in SolidWorks (you'll also need the
   referenced .SLDPRT parts — the GrabCAD download must include them).
2. File → Save As → STEP AP214 (*.step). Save it anywhere.
3. Drop the .step into:    C:\SPATAIL_MAX\assets_raw\car-engine\
4. In Blender (5.1+, one-time):
     Edit → Preferences → Get Extensions → search "STEP" →
     install the "STEP Importer" extension.
5. From C:\SPATAIL_MAX:
     npm run spatail:ios
```

The pipeline will normalize your STEP into a GLB under
`assets_processed/` and bake its URL into the contract's
`requiredAssets[0].processedAssetPath`. The web Contract Studio loads
the GLB automatically; the iPhone AR app picks it up on next launch
because `npm run spatail:ios` also copies the new contract into the
iOS bundle's `Resources/` folder.

## Step-by-step

### 1. Export STEP from SolidWorks

The download you shared is a `.SLDASM` (a SolidWorks assembly — i.e.,
a list of references to `.SLDPRT` part files plus their transforms). The
geometry lives in the `.SLDPRT` files; the `.SLDASM` alone has no mesh.
Before exporting, confirm that the GrabCAD zip you downloaded actually
contained the `.SLDPRT` files — if SolidWorks reports "missing
references" when opening the assembly, the parts are missing and you'll
need to re-download from GrabCAD (the page usually has a separate
"Download all files" button) or grab the alternate STEP/STL the author
provided.

Once the assembly opens cleanly:

- `File → Save As`
- `Save as type: STEP AP214 (*.step;*.stp)`. AP214 carries colors;
  AP203 doesn't. Either works for SPATAIL.
- Save the file anywhere convenient.

If you don't have SolidWorks at hand, two free alternatives that *can*
read native SLDASM (when the parts are present):

- **CAD Exchanger Lab Web** — free upload-based converter, supports SW.
  Caveat: you're uploading the geometry to a third party.
- **Autodesk Fusion 360** — free personal license, opens SLDASM and
  exports STEP cleanly.

### 2. Drop the file at the right path

The Mustang card's `engine_bay` source has
`"assetGroupRef": "car-engine"`. The pipeline matches refs to folder
names under `assets_raw/` by token overlap. The simplest cohesive name:

```
C:\SPATAIL_MAX\assets_raw\car-engine\<anything>.step
```

The actual filename doesn't matter as long as the folder name shares the
token `engine` (and ideally `car`). The pipeline picks up the file via
`scanner.js` and routes it to Blender for normalization.

### 3. Enable STEP support in Blender

Blender 5.1 ships **without** the STEP importer enabled by default —
it's a separate extension on the Blender Extensions Platform. One-time
setup:

1. Open Blender.
2. `Edit → Preferences → Get Extensions`.
3. Search for `STEP`.
4. Install "STEP Importer" (or any equivalent CAD-format extension).
5. Close Blender.

The next time `npm run spatail` runs, Blender starts headlessly and
the pipeline's [analyze_asset.py](../blender_tools/analyze_asset.py)
attempts to enable the addon (it tries `io_import_step`,
`io_scene_step`, `bl_ext.user_default.step_importer`, and a few other
common module names). Once one resolves, STEP imports just work.

If your environment refuses to install the extension, the v0.2 fallback
is to pre-convert the STEP to GLB in any 3D tool you trust
(FreeCAD → mesh export, Fusion 360 → glTF, etc.) and drop the resulting
GLB directly into `assets_raw/car-engine/`. The pipeline pass-through
copies GLB without needing Blender.

### 4. Rebuild contracts + refresh iOS bundle

```bash
npm run spatail:ios
```

This runs the planner and copies the resulting contracts into
[SPATAILMobileAR/Resources/](../SPATAILMobileAR/Resources/), so the
iPhone app picks up the new engine on next build/run.

### 5. Verify

In the Contract Studio (http://localhost:5173/):

- Pick the Mustang experience.
- Find **Engine Bay (Mustang)** in the right-side spatial-elements list.
  It carries the `tabletop_model` / `table` / `plane_anchor` triplet.
- The 3D scene shows the engine GLB on the table where the placeholder
  box used to be. The bright translucent air-filter housing target sits
  on top of it; the exploded assembly is still aligned directly above.
- Click "view raw contract →" in the topbar — the
  `requiredAssets[0]` for `elem_engine_bay_*` should show:

  ```json
  {
    "id": "car-engine",
    "preferredSource": "cad_folder",
    "processedAssetPath": "/assets_processed/spatail__car_engine__<...>.glb",
    "importer": "wm.step_import"   // or whichever operator resolved
  }
  ```

If `processedAssetPath` is missing and you see
`normalizationStatus: "no_group"` or `"failed"`, the contract itself
tells you which step (folder match / Blender invocation) didn't run.

## Why this design

- **The contract is the source of truth.** Adding the engine didn't
  require any renderer changes — the planner now emits a `tabletop_model`
  element whose `requiredAssets` carry the resolved GLB URL, and every
  consumer (web studio, iPhone AR, future visionOS) loads from that URL.
- **The air-filter housing stays a `highlighted_target` with the bright
  translucent shader**, even when sitting on top of a real engine mesh.
  That contrast is the whole point: real geometry below, *spatially
  reasoned* highlight on top.
- **Failure mode is honest, not silent.** No engine in `assets_raw/`?
  Contract records `normalizationStatus: "no_group"` and the scene
  renders the placeholder box with the same label. You always know
  what's real and what's pending.
