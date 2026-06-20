"""Track gig IDs already sent so we don't repeat in future digests."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from jobpilot.gigs.core.io_lock import atomic_write_text, file_lock
from jobpilot.gigs.core.paths import data_dir

DATA_DIR = data_dir()
SEEN_PATH = DATA_DIR / "seen.json"
FIRST_SEEN_PATH = DATA_DIR / "first_seen.json"
MAX_RETAIN_DAYS = 30  # forget gigs older than this so storage stays bounded

# Archived entries are permanent: their seen.json value is this prefix plus
# the archive timestamp, and _prune never drops them — an auto-archived gig
# must never resurface in a digest.
ARCHIVED_PREFIX = "archived:"


def _load_unlocked() -> dict[str, str]:
    """{gig_id: iso_timestamp_first_seen}"""
    if not SEEN_PATH.exists():
        return {}
    try:
        return json.loads(SEEN_PATH.read_text())
    except Exception:
        return {}


def _load() -> dict[str, str]:
    with file_lock(SEEN_PATH):
        return _load_unlocked()


def _save(seen: dict[str, str]) -> None:
    with file_lock(SEEN_PATH):
        atomic_write_text(SEEN_PATH, json.dumps(seen, indent=2))


def _prune(seen: dict[str, str]) -> dict[str, str]:
    """Drop entries older than MAX_RETAIN_DAYS. Archived entries are kept
    forever (see ARCHIVED_PREFIX)."""
    cutoff = datetime.now().timestamp() - (MAX_RETAIN_DAYS * 86400)
    out = {}
    for k, v in seen.items():
        if isinstance(v, str) and v.startswith(ARCHIVED_PREFIX):
            out[k] = v
            continue
        try:
            ts = datetime.fromisoformat(v).timestamp()
            if ts >= cutoff:
                out[k] = v
        except Exception:
            # Keep malformed entries — safer than dropping
            out[k] = v
    return out


def filter_new(gig_ids: list[str]) -> list[str]:
    """Return only IDs we haven't seen before."""
    seen = _load()
    return [gid for gid in gig_ids if gid not in seen]


def mark_seen(gig_ids: list[str]) -> None:
    """Record these IDs as sent. Runs prune on write."""
    with file_lock(SEEN_PATH):
        seen = _prune(_load_unlocked())
        now = datetime.now().isoformat()
        for gid in gig_ids:
            seen.setdefault(gid, now)
        atomic_write_text(SEEN_PATH, json.dumps(seen, indent=2))


def unmark_seen(gig_ids: list[str]) -> None:
    """Remove these IDs from seen (used to undo a swipe so the gig can resurface)."""
    if not gig_ids:
        return
    with file_lock(SEEN_PATH):
        seen = _load_unlocked()
        changed = False
        for gid in gig_ids:
            if gid in seen:
                del seen[gid]
                changed = True
        if changed:
            atomic_write_text(SEEN_PATH, json.dumps(seen, indent=2))


def mark_archived(gig_ids: list[str]) -> None:
    """Record these IDs as permanently retired (auto-archived or collapsed
    away). Unlike mark_seen, the entry survives pruning forever and
    overwrites any existing timestamp."""
    if not gig_ids:
        return
    with file_lock(SEEN_PATH):
        seen = _prune(_load_unlocked())
        now = datetime.now().isoformat()
        for gid in gig_ids:
            seen[gid] = f"{ARCHIVED_PREFIX}{now}"
        atomic_write_text(SEEN_PATH, json.dumps(seen, indent=2))


def seen_timestamps() -> dict[str, str]:
    """Read-only copy of {gig_id: first-seen ISO timestamp}. Archived
    entries carry the "archived:" prefix on their timestamp."""
    return _load()


def seen_count() -> int:
    return len(_load())


# ----- first-seen stamps for pipeline `new` rows ---------------------------


def sync_first_seen(active_new_ids: list[str]) -> dict[str, str]:
    """Stamp first-seen timestamps for the pipeline's `new` rows.

    Keeps exactly the given IDs: stamps now for any ID without a stamp and
    drops stamps for IDs no longer `new` (archived, decided, or removed).
    The stamp is the auto-archive clock — a row still `new` 14 days after
    its stamp moves to pipeline_archive.md. Returns the resulting
    {gig_id: iso_timestamp} mapping."""
    with file_lock(FIRST_SEEN_PATH):
        current: dict[str, str] = {}
        if FIRST_SEEN_PATH.exists():
            try:
                data = json.loads(FIRST_SEEN_PATH.read_text())
                if isinstance(data, dict):
                    current = data
            except Exception:
                current = {}
        now = datetime.now().isoformat()
        out = {gid: current.get(gid, now) for gid in active_new_ids}
        if out != current:
            atomic_write_text(FIRST_SEEN_PATH, json.dumps(out, indent=2))
    return out