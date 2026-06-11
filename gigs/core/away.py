"""Latest-leads persistence + Reminders driven by pipeline state.

The legacy commands.txt / saved_leads.md / command_results.md flow has been
removed. The pipeline.md is now the single user-editable surface for status
changes. This module's remaining responsibilities are:

- Snapshot the latest ranked gigs to disk (so the digest archive can be
  re-rendered without re-scraping)
- Sync Reminders.app from pipeline rows that should be followed up on

Reminder-created bookkeeping lives in `data/reminder_flags.json` (keyed by
gig_id), NOT in the pipeline's Notes column — Notes is a user-facing cell,
and the old "reminder_created" literal polluted it and broke pass-reason
extraction. Rows that still carry the literal arrive from pipeline.parse
with `legacy_reminder_flag` set (the literal itself stripped); the flag is
migrated into the sidecar here, and the next pipeline write scrubs the file.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from jobpilot.gigs.core.io_lock import atomic_write_text, file_lock
from jobpilot.gigs.core.models import Gig
from jobpilot.gigs.core import pipeline
from jobpilot.gigs.core.paths import data_dir
from jobpilot.gigs.core.reminders import create_reminder_for_gig

DATA_DIR = data_dir()
LATEST_PATH = DATA_DIR / "latest_leads.json"
REMINDER_FLAGS_PATH = DATA_DIR / "reminder_flags.json"


def save_latest_leads(gigs: list[Gig], path: Path = LATEST_PATH) -> None:
    """Persist the most recent ranked gigs to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([asdict(gig) for gig in gigs], indent=2))


def load_latest_leads(path: Path = LATEST_PATH) -> list[Gig]:
    if not path.exists():
        return []
    try:
        rows = json.loads(path.read_text())
        return [Gig(**row) for row in rows]
    except Exception:
        return []


def _load_reminder_flags() -> dict[str, str]:
    if not REMINDER_FLAGS_PATH.exists():
        return {}
    try:
        data = json.loads(REMINDER_FLAGS_PATH.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_reminder_flags(flags: dict[str, str]) -> None:
    with file_lock(REMINDER_FLAGS_PATH):
        atomic_write_text(REMINDER_FLAGS_PATH, json.dumps(flags, indent=2))


def _flag_key(row: pipeline.Row) -> str:
    """Sidecar key: gig_id, falling back to company|role for hand-added rows."""
    return row.gig_id or f"{row.company}|{row.role}"


def sync_reminders_from_pipeline(rows: list[pipeline.Row] | None = None) -> int:
    """Create Reminders for actively-pursued gigs (saved/drafted/sent) that
    don't yet have one. Returns count created.

    Dedup state lives in data/reminder_flags.json ({key: created-at}); rows
    flagged the legacy way (in Notes) are migrated into the sidecar without
    creating a duplicate Reminder. The pipeline file itself is never written
    from here — Notes stays the user's cell.
    """
    rows = rows if rows is not None else pipeline.parse()
    flags = _load_reminder_flags()
    created = 0
    dirty = False
    now = datetime.now().isoformat()

    for row in rows:
        key = _flag_key(row)
        if row.legacy_reminder_flag and key not in flags:
            flags[key] = now  # migrated from the legacy Notes literal
            dirty = True
        if not row.is_actively_pursuing:
            continue
        if key in flags:
            continue
        # Build a synthetic Gig just for the reminder (most fields unused)
        g = Gig(
            id=row.gig_id or "row",
            source="pipeline",
            title=row.role,
            url=row.apply,
            apply_url=row.apply,
            company=row.company,
            fit_score=row.score,
        )
        if create_reminder_for_gig(g):
            created += 1
            flags[key] = now
            dirty = True

    if dirty:
        _save_reminder_flags(flags)
    return created
