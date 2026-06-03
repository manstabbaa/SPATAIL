"""SPATAIL Starter Asset Library — the front of the asset-resolution hierarchy.

A reusable catalog of common spatial primitives, educational objects, mechanical
components, UI elements, placeholders and animation behaviours, so the runtime can
assemble a decent spatial experience INSTANTLY and Blender generation only enhances,
never blocks. Resolution order:

  1. exact library asset (real GLB)
  2. close semantic library asset (real GLB)
  3. runtime primitive (enriched by the matched catalog entry's scale/shape)
  4. placeholder (shown immediately)
  5. generate in Blender -> cache back into the library

The catalog is metadata (studio/library/catalog.py) emitted to JSON manifests under
public/assets/spatail-library/manifests/. The registry (asset_library.py) loads those
and answers find_asset()/resolve(); the Blender Asset Factory consults it first.

Like the other studio packages, importing this one puts studio/ and studio/server/
on sys.path so modules can reach xr_design / experience_spec with the flat-import
convention.
"""
import os as _os
import sys as _sys

_STUDIO = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))  # studio/
for _p in (_STUDIO, _os.path.join(_STUDIO, "server")):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
