"""educator.py - SPATAIL EDUCATOR: the single "ask anything" front door.

    python studio/educator.py "How do Newton's laws work?"
    python studio/educator.py "Explain how a lever works" --image diagram.png

You ask a question; the studio answers with a spatial demo built and animated in
Blender, staged by XR rule, and played in the tester room. This is the thin
deterministic spine; the *intelligence* (turning an arbitrary question into a
storyboard of real-world demos) is the Director agent's job — see
studio/scenes/README and .claude/agents/studio-director.md.

Flow:
  question -> brief.json
           -> Director: scene spec (studio/scenes/<id>.json)   [agent or fixture]
           -> Blender build (Artist real-world objects + animation)
           -> contract (Developer staging)  -> tester room / XR runtime

HARD RULE honoured end to end: the Blender build never falls back to primitives;
an unknown demo aborts loudly so a half-real answer is never shipped silently.
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

STUDIO = Path(__file__).resolve().parent
PY = sys.executable


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return (s[:40] or "demo")


def make_brief(question: str, image: str | None) -> Path:
    brief_id = slugify(question)
    brief = {
        "briefId": brief_id,
        "prompt": question,
        "image": image,
        "audience": "curious learner",
        "learningGoal": f"Build spatial intuition for: {question}",
        "subject": "auto",
        "scene": brief_id,
    }
    out = STUDIO / "brief" / f"{brief_id}.json"
    out.write_text(json.dumps(brief, indent=2), encoding="utf-8")
    return out


def ensure_scene_spec(brief_path: Path) -> Path:
    """Return the scene spec for this brief. If the Director has already authored
    one (studio/scenes/<id>.json), use it. Otherwise try the deterministic
    fixture router (studio/director_fixtures.py) so the spine runs without an LLM.
    If neither yields a spec, exit with guidance to invoke the Director agent."""
    brief = json.loads(brief_path.read_text(encoding="utf-8"))
    scene_id = brief["scene"]
    spec = STUDIO / "scenes" / f"{scene_id}.json"
    if spec.exists():
        return spec
    # deterministic fallback router for known topics (keeps the demo runnable
    # offline). This is NOT a content fallback to primitives — it only chooses an
    # existing, fully-real scene spec; if it can't, we stop.
    try:
        sys.path.insert(0, str(STUDIO))
        import director_fixtures as df
        routed = df.route(brief)
        if routed:
            spec.write_text(json.dumps(routed, indent=2), encoding="utf-8")
            print(f"[educator] Director (fixture) authored {spec.name}")
            return spec
    except Exception as e:
        print(f"[educator] fixture router unavailable: {e}")
    sys.exit(
        f"[educator] No scene spec for {scene_id!r} yet.\n"
        f"  Invoke the Director agent to author studio/scenes/{scene_id}.json from\n"
        f"  {brief_path}, then re-run. (In the live team, the Director does this\n"
        f"  automatically and hands off to the Artist.)")


def run(brief_path: Path):
    spec = ensure_scene_spec(brief_path)
    print(f"[educator] scene spec: {spec}")
    r = subprocess.run([PY, str(STUDIO / "run.py"), "--brief", str(brief_path)])
    if r.returncode != 0:
        sys.exit("[educator] pipeline failed")
    print("[educator] answer ready. Start the tester room:")
    print(f"    {PY} studio/viewer/server.py")
    print("    open http://localhost:5180/studio/viewer/studio.html")


def main():
    ap = argparse.ArgumentParser(description="SPATAIL EDUCATOR — ask anything, get a spatial demo.")
    ap.add_argument("question", help="what to explain, in plain language")
    ap.add_argument("--image", default=None, help="optional reference image path")
    a = ap.parse_args()
    brief_path = make_brief(a.question, a.image)
    print(f"[educator] brief: {brief_path}")
    run(brief_path)


if __name__ == "__main__":
    main()
