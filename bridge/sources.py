"""Read the legacy lanes' stores without changing them."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any


def read_queue(path: Path) -> list[dict[str, Any]]:
    """Rows of the jobs lane's queue.json, or [] when there is no queue."""
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    if not isinstance(data, list):
        raise ValueError(f"{path} should hold a list of jobs")
    return [item for item in data if isinstance(item, dict)]


def read_tracker(path: Path) -> list[dict[str, Any]]:
    """Rows of the application tracker, opened read-only."""
    if not path.exists():
        return []
    with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        has_table = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'applications'"
        ).fetchone()
        if not has_table:
            return []
        rows = db.execute(
            "SELECT id, job_url, job_title, company, applied_at, status, updated_at "
            "FROM applications ORDER BY id"
        ).fetchall()
    return [dict(row) for row in rows]


def read_pipeline(path: Path) -> list[Any]:
    """Rows of the gigs lane's pipeline.md table."""
    if not path.exists():
        return []
    # Imported lazily so the gigs lane's import-time path setup only runs
    # when a pipeline is actually read.
    from jobpilot.gigs.core.pipeline import parse_text

    return parse_text(path.read_text())


def read_first_seen(path: Path) -> dict[str, str]:
    """The gigs lane's gig_id -> first-seen timestamp map, or {}."""
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    return (
        {str(key): str(value) for key, value in data.items()}
        if isinstance(data, dict)
        else {}
    )
