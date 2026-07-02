"""segment.py — the real book segment under test + the assets it names.

Source: OpenStax, *College Physics 2e*, Section 16.4 "The Simple Pendulum"
(openstax.org, CC BY 4.0). The opening paragraph names several concrete objects
spanning simple -> complex, which is ideal for stress-testing image-to-3D.
"""

CITATION = {
    "book": "College Physics 2e",
    "publisher": "OpenStax",
    "section": "16.4 The Simple Pendulum",
    "url": "https://openstax.org/books/college-physics-2e/pages/16-4-the-simple-pendulum",
    "license": "CC BY 4.0",
}

# Quoted (for attribution; not redistributed beyond fair academic use)
SEGMENT_TEXT = (
    "Pendulums are in common usage. Some have crucial uses, such as in clocks; "
    "some are for fun, such as a child's swing; and some are just there, such as "
    "the sinker on a fishing line. For small displacements, a pendulum is a simple "
    "harmonic oscillator. A simple pendulum is defined to have an object that has a "
    "small mass, also known as the pendulum bob, which is suspended from a light "
    "wire or string."
)

# Assets named by the segment, with Meshy-oriented descriptions + SPATAIL roles.
# complexity is the reason each was chosen: it spans the range image-to-3D must cover.
ASSETS = [
    {
        "id": "simple_pendulum",
        "name": "Simple Pendulum",
        "role": "primary_object",
        "complexity": "simple",
        "desc": ("a simple pendulum apparatus: a small polished brass spherical bob "
                 "suspended by a single thin taut vertical string from a horizontal "
                 "metal support arm mounted on a heavy black lab-stand base"),
        "scaleMeters": [0.25, 0.5, 0.25],
    },
    {
        "id": "pendulum_clock",
        "name": "Pendulum Clock",
        "role": "comparison_object",
        "complexity": "complex",
        "desc": ("an antique wooden grandfather pendulum clock: a tall narrow polished "
                 "wood case, a round white clock face with black roman numerals near the "
                 "top, and a long swinging brass pendulum with a round disc bob visible "
                 "behind a glass door"),
        "scaleMeters": [0.5, 1.8, 0.3],
    },
    {
        "id": "childs_swing",
        "name": "Child's Swing",
        "role": "comparison_object",
        "complexity": "medium",
        "desc": ("a child's playground swing: a single flat black rubber seat hung by "
                 "two metal chains from the horizontal top bar of a simple blue A-frame "
                 "swing set, standing on the ground"),
        "scaleMeters": [1.2, 1.6, 1.2],
    },
    {
        "id": "fishing_sinker",
        "name": "Fishing Sinker",
        "role": "comparison_object",
        "complexity": "small",
        "desc": ("a single teardrop-shaped grey lead fishing sinker weight with a small "
                 "metal eyelet loop at the narrow top, a short length of fishing line "
                 "threaded through the loop"),
        "scaleMeters": [0.03, 0.06, 0.03],
    },
]

BY_ID = {a["id"]: a for a in ASSETS}
