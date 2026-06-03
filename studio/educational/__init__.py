"""SPATAIL Educational Wrapper (backend) — read → select → spatial explanation.

Turns a selected concept from reading material into an EducationalExperiencePlan by
building a stronger prompt and running it through the existing Representation Engine →
Asset Factory → Runtime — never duplicating scene-building logic. The reader content
and per-concept recommendations come from the demo reader the user authored
(SPATAIL_Educational_Wrapper_Demo_Reader.pdf); the academic basis for the science
concepts is OpenStax (Concepts of Biology / University Physics / Astronomy, CC BY).

Like the other studio packages, importing this one puts studio/ and studio/server/
on sys.path for the flat-import convention.
"""
import os as _os
import sys as _sys

_STUDIO = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))  # studio/
for _p in (_STUDIO, _os.path.join(_STUDIO, "server")):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
