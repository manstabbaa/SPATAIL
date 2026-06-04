"""config.py — load SPATAIL external API keys WITHOUT ever committing them.

Resolution order per key: real environment variable > ~/.spatail/secrets.env
(a file that lives OUTSIDE the repo). Nothing here writes keys into the repo.
"""
from __future__ import annotations

import os
from pathlib import Path

SECRETS_FILE = Path.home() / ".spatail" / "secrets.env"


def _load_file() -> dict:
    kv: dict[str, str] = {}
    if SECRETS_FILE.exists():
        for line in SECRETS_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                kv[k.strip()] = v.strip()
    return kv


_FILE = _load_file()


def get(name: str, default: str = "") -> str:
    """env var wins, then the out-of-repo secrets file, then default."""
    return os.environ.get(name) or _FILE.get(name, default)


def require(name: str) -> str:
    v = get(name)
    if not v:
        raise RuntimeError(
            f"{name} is not set. Add it to {SECRETS_FILE} (KEY=value) or export it. "
            f"Never hard-code or commit API keys.")
    return v


GEMINI_API_KEY = get("GEMINI_API_KEY")
MESHY_API_KEY = get("MESHY_API_KEY")
