"""Resolve iCloud and workspace paths (override via env for other machines)."""

from __future__ import annotations

import os
from pathlib import Path


def icloud_root() -> Path:
    default = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs"
    return Path(os.environ.get("GIGPILOT_ICLOUD_ROOT", default))


def pipeline_dir() -> Path:
    return Path(os.environ.get("GIGPILOT_PIPELINE_DIR", icloud_root() / "GigPilot"))


def digests_dir() -> Path:
    return Path(os.environ.get("GIGPILOT_DIGESTS_DIR", icloud_root() / "Gigpilot_Digests"))


def away_dir() -> Path:
    return Path(os.environ.get("GIGPILOT_AWAY_DIR", icloud_root() / "Gigpilot_Away"))


def data_dir() -> Path:
    """Repo-local state dir (jobpilot's gitignored data/gigs). Overridable so
    smoke tests / sandboxed runs can't touch real state (seen.json, hygiene
    marker, latest_leads.json, ...)."""
    default = Path(__file__).parent.parent.parent / "data" / "gigs"
    return Path(os.environ.get("GIGPILOT_DATA_DIR", default))