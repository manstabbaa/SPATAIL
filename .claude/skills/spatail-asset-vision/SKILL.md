---
name: spatail-asset-vision
description: Turn a Meshy (or any) 3D asset into a vision-validated, XR-ready SPATAIL asset. Use when an asset's orientation/scale/seating looks wrong ("the ball is positioned wrong", "it's not upright", "it's the wrong size", "the mesh isn't being checked in Blender"), or to ingest Meshy content correctly. Runs a headless-Blender EVIDENCE pass, has YOU visually review the contact sheet, then runs an APPLY pass that reorients + real-scales + repairs + exports + renders a verification image. Pulls in cached Meshy content by slug, an explicit .glb path, or a fresh Meshy generation.
---

# SPATAIL Asset Vision — vision-guided asset ingest

Headless Blender can MEASURE a mesh but cannot SEE it, so the old path guessed
orientation, scale, and front/back. This skill fixes that: **Blender produces visual
+ numeric evidence; YOU (the vision model) make the semantic decision; Blender then
executes it.** It runs at prep time and bakes the corrected transform into the export,
so the prompt-time placer pays zero extra latency.

Pipeline lives in `studio/vision/` (`vision_report.py` is the bpy-free contract).

## When to use
- The user says an asset is mis-placed / not upright / wrong size / "do it in Blender".
- Ingesting Meshy content (cached under `studio/out/meshy/<slug>/meshy/<slug>.glb`).
- Preparing a batch of raw AI-generated GLBs for XR.

## Steps

### 1. Evidence pass (Blender — no semantic decision)
```bash
python studio/vision/driver.py evidence --asset <slug|path|subject> [--subject "<real subject>"] [--generate]
```
- `--asset frog` resolves the cached Meshy GLB; a `.glb`/`.gltf` path is used directly;
  a subject + `--generate` pulls a FRESH model from Meshy (spends credits, needs keys).
- Output tree: `asset_output/<slug>/{imported,preview,reports,exports,logs}`.
- Produces `preview/contact_sheet.png` (labeled views + axis triad + measurement panel),
  `reports/asset_report.json` (numbers), `reports/visual_review_prompt.md` (the question).

### 2. Visual review (YOU — the semantic decision)
- **Read** `asset_output/<slug>/preview/contact_sheet.png`. Open individual
  `preview/iso_01.png`, `front.png`, `top.png`, `normal_preview.png` if you need detail.
- **Read** `reports/visual_review_prompt.md` — it states exactly what to decide and the
  axis convention (+X red, +Y green, +Z blue/up; the six ortho tiles are named by the
  FACE they show — front=-Y, back=+Y, right=+X, left=-X, top=+Z, bottom=-Z, +Z image-up).
- Decide, referencing those axes:
  - **identity** — what the object actually is.
  - **orientation** — which CURRENT axis is true **up** and which is the natural **front**.
    If the imported pose is already correct, `up=+Z, front=-Y, needs_rotation=false`.
  - **scale** — is the real-world size believable? best longest-dimension estimate in
    metres + a `placement_class` (table/floor/wall/ceiling/vehicle/handheld/educational_model).
  - **quality** — material_quality, any repair (recalc_normals_outside / remove_loose /
    merge_by_distance / fill_holes / flip_normals), xr_ready, visible defects.
- **Write** `asset_output/<slug>/reports/visual_review_result.json` exactly matching the
  schema in the prompt (`spatail-asset-vision/1`). Set `"reviewer"` to your model id.
- For a BATCH (many assets), delegate each review to the **`asset-vision-reviewer`**
  subagent (one per asset, in parallel) to keep this context clean — pass it the asset's
  contact-sheet + report + prompt paths and the output path to write.

### 3. Apply pass (Blender — executes your decision)
```bash
python studio/vision/driver.py apply --asset <slug|path> [--subject "..."] [--real-size w,h,d]
```
- Reorients (up→+Z, front→-Y), uniform-scales to the real longest dim (your estimate /
  `object_size` LLM / `--real-size`), runs the requested repairs, seats `center_bottom`,
  exports `exports/normalized.glb` + `normalized.usdz`, validates, and renders
  `preview/verify.png` (asset on a metric grid with green=up / cyan=front arrows + the
  baked facts annotated).

### 4. Confirm
- **Read** `asset_output/<slug>/preview/verify.png`. Check: upright on the grid, facing
  front, real size (read the grid-cell legend), validation OK.
- If it's wrong (tipped, wrong scale, bad normals), fix `visual_review_result.json` and
  re-run step 3. Report the final baked facts (size, class, rotation, repairs) to the user.

## Headless shortcut (no human in the loop)
`python studio/vision/driver.py all --asset <X> --gemini` runs evidence → **Gemini**
review → apply in one shot. Lower fidelity than your review; use for unattended batches.

## Hard rules
- The Blender passes MUST NOT make semantic decisions — that is your job, from the pack.
- Never let scale default to 0.4 m: every asset gets a real-world size (your estimate or
  `object_size`). Never ship an asset whose `verify.png` you did not look at.
