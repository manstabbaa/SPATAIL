"""run_parity.py — golden-fixture parity harness for the Placement Design System.

Solves every fixtures/*.json with studio/spatail/placement_solver.py and asserts the
plan matches the baked expectation within tolerance. The SAME fixtures are the
contract the Swift PlacementSolver must satisfy (an XCTest parses the identical
JSON — see README.md for the schema and the Swift field mapping), so a green run
here + a green run there means authoring previews and on-device placement agree.

Stdlib only. Usage:  python3 run_parity.py [fixture_name ...]
Exit 0 = all pass; 1 = any mismatch (each deviation printed).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SPATAIL = _HERE.parent
if str(_SPATAIL) not in sys.path:
    sys.path.insert(0, str(_SPATAIL))

import placement_solver  # noqa: E402

FIXTURES = _HERE / "fixtures"
# Loose enough for Swift Float vs Python double + the plan's 4-decimal rounding.
DEFAULT_TOL = {"position_m": 0.005, "yaw_rad": 0.005, "scale": 0.005}


def check_fixture(path: Path) -> list:
    """Solve one fixture; return a list of human-readable deviations (empty = pass)."""
    fx = json.loads(path.read_text(encoding="utf-8"))
    plan = placement_solver.solve_from_dicts(fx["room"], fx["assets"], fx.get("intent"))
    expect = fx["expect"]
    tol = dict(DEFAULT_TOL)
    tol.update(expect.get("tolerances", {}))
    errs = []

    if plan["anchor"] != expect["anchor"]:
        errs.append(f"anchor: got {plan['anchor']!r}, want {expect['anchor']!r}")
    if plan["scaleVariant"] != expect["scaleVariant"]:
        errs.append(f"scaleVariant: got {plan['scaleVariant']!r}, "
                    f"want {expect['scaleVariant']!r}")

    got_by_id = {p["assetId"]: p for p in plan["placements"]}
    if len(plan["placements"]) != len(expect["placements"]):
        errs.append(f"placement count: got {len(plan['placements'])}, "
                    f"want {len(expect['placements'])}")
    for want in expect["placements"]:
        aid = want["assetId"]
        got = got_by_id.get(aid)
        if got is None:
            errs.append(f"{aid}: missing from plan")
            continue
        for axis, g, w in zip("xyz", got["position_m"], want["position_m"]):
            if abs(g - w) > tol["position_m"]:
                errs.append(f"{aid}: position {axis}: got {g}, want {w} "
                            f"(tol {tol['position_m']})")
        if abs(got["yaw_rad"] - want["yaw_rad"]) > tol["yaw_rad"]:
            errs.append(f"{aid}: yaw_rad: got {got['yaw_rad']}, want {want['yaw_rad']} "
                        f"(tol {tol['yaw_rad']})")
        if abs(got["scale"] - want["scale"]) > tol["scale"]:
            errs.append(f"{aid}: scale: got {got['scale']}, want {want['scale']} "
                        f"(tol {tol['scale']})")
        if bool(got["fits"]) != bool(want["fits"]):
            errs.append(f"{aid}: fits: got {got['fits']}, want {want['fits']}")
        for k in ("anchor", "zone"):
            if k in want and got[k] != want[k]:
                errs.append(f"{aid}: {k}: got {got[k]!r}, want {want[k]!r}")
        if not got.get("reason"):
            errs.append(f"{aid}: reason string missing (canonical richness)")

    # Python-only extras: the Swift Plan carries no diagnostics, so an XCTest skips
    # this block; here it locks the coverage/keep-out/baked-pin plumbing.
    for k, w in (expect.get("diagnostics") or {}).items():
        g = plan["diagnostics"].get(k)
        ok = abs(g - w) <= 0.001 if isinstance(w, float) else g == w
        if not ok:
            errs.append(f"diagnostics.{k}: got {g!r}, want {w!r}")
    return errs


def main(argv: list) -> int:
    names = set(argv)
    paths = sorted(FIXTURES.glob("*.json"))
    if names:
        paths = [p for p in paths if p.stem in names or p.name in names]
    if not paths:
        print(f"no fixtures found in {FIXTURES}")
        return 1
    failures = 0
    for p in paths:
        errs = check_fixture(p)
        status = "PASS" if not errs else "FAIL"
        print(f"  [{status}] {p.stem}")
        for e in errs:
            print(f"          - {e}")
        failures += 1 if errs else 0
    if failures:
        print(f"PARITY FAIL ({failures}/{len(paths)} fixtures)")
        return 1
    print(f"PARITY PASS ({len(paths)} fixtures)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
