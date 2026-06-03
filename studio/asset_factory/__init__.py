"""SPATAIL Blender Asset Factory — Blender as an ASSET factory, not a scene builder.

Consumes an AssetRequestManifest (from the Representation Engine) and produces an
AssetDeliveryPackage: modular GLB assets + variants + animations + metadata
(bounding boxes, pivots, semantic roles). It ORCHESTRATES the existing headless
build driver (pipeline/blender/spatail_build_from_plan_driver.py) as a subprocess
— it never reimplements Blender code, and it never builds a final scene (layout,
sequencing and interaction belong to studio/runtime).

Like studio/representation, importing this package puts studio/ and studio/server/
on sys.path so the modules can import the rest of the subsystem and the studio
helpers with the repo's flat-import convention.
"""
import os as _os
import sys as _sys

_STUDIO = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))  # studio/
for _p in (_STUDIO, _os.path.join(_STUDIO, "server")):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
