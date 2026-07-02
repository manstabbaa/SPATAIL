"""SPATAIL AI Asset Factory — INGEST pipeline for AI-generated 3D assets.

Top-level, repo-rooted package (sibling to assets_raw/ and assets_processed/).
It ingests messy GLB/GLTF/OBJ/FBX/STL produced by AI 3D generators (Meshy, Tripo,
Rodin, Luma, …), then per asset group:

    import → inspect → conservative cleanup → fixed-bounds normalization →
    origin normalization → preview → GLB export → manifest/report → validation

It does NOT do XR/ARKit/RealityKit placement, scene interaction, or educational
runtime behaviour — SPATAIL handles placement downstream.

Not to be confused with studio/asset_factory/, which is the *generative* factory
(manifest → Blender-built primitives). This one is the *ingest/normalize* factory.

Module split (a hard rule): the worker manager and config/reports/validation are
bpy-free; everything that touches bpy lives in importers / inspect_asset / cleanup
/ normalize_bounds / export_asset / preview and runs only inside the Blender worker
(process_one_asset.py).
"""
