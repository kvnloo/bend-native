"""Canonical private layout under ``~/.z0int``.

Repo = code. User state / corpora / specialists live only under HOME.
"""

from __future__ import annotations

import os
from pathlib import Path

SCHEMA = "z0int.paths.v1"


def home() -> Path:
    override = os.environ.get("Z0INT_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".z0int").resolve()


def ensure_layout(root: Path | None = None) -> dict[str, Path]:
    """Create the standard private tree; return named paths."""
    root = root or home()
    names = (
        "config",
        "state",
        "sources",
        "vault",
        "episodes",
        "stream",
        "replay",
        "models",
        "specialists",
        "benchmarks",
        "research",
        "receipts",
        "tokenomics",
        "logs",
        "shadow",
        "autoresearch",
        "runtime",
    )
    out: dict[str, Path] = {"root": root}
    root.mkdir(parents=True, exist_ok=True)
    for name in names:
        p = root / name
        p.mkdir(parents=True, exist_ok=True)
        out[name] = p
    return out


def onboard_state_path(root: Path | None = None) -> Path:
    return (root or home()) / "state" / "onboard.json"


def config_path(root: Path | None = None) -> Path:
    return (root or home()) / "config" / "z0int.json"
