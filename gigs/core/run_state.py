"""Last digest heartbeat — did launchd actually run?"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from jobpilot.gigs.core.io_lock import atomic_write_text, file_lock
from jobpilot.gigs.core.paths import data_dir

DATA_DIR = data_dir()
LAST_RUN_PATH = DATA_DIR / "last_run.json"
# Digest runs twice daily — 36h of silence means the launchd job is dead.
HEARTBEAT_MAX_AGE_HOURS = 36.0


def _load_unlocked() -> dict[str, Any]:
    if not LAST_RUN_PATH.exists():
        return {}
    try:
        data = json.loads(LAST_RUN_PATH.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def load() -> dict[str, Any]:
    with file_lock(LAST_RUN_PATH):
        return _load_unlocked()


def record_digest(**fields: Any) -> None:
    """Persist summary of the latest digest run."""
    payload = {
        "last_digest_at": datetime.now().isoformat(),
        **fields,
    }
    with file_lock(LAST_RUN_PATH):
        atomic_write_text(LAST_RUN_PATH, json.dumps(payload, indent=2))


def digest_is_stale(*, hours: float = 30.0) -> bool:
    """True if no successful digest within `hours` (launchd miss / laptop asleep)."""
    data = load()
    at = data.get("last_digest_at")
    if not at:
        return True
    try:
        last = datetime.fromisoformat(at)
    except Exception:
        return True
    if not data.get("ok", True):
        return True
    return datetime.now() - last > timedelta(hours=hours)


def last_digest_age(*, now: datetime | None = None) -> timedelta | None:
    """Age of the last recorded digest attempt (ok or failed).

    None means no run was ever recorded (or the timestamp is unreadable) —
    callers should treat that as stale.
    """
    data = load()
    at = data.get("last_digest_at")
    if not at:
        return None
    try:
        last = datetime.fromisoformat(at)
    except Exception:
        return None
    return (now or datetime.now()) - last


def heartbeat_is_stale(*, max_age_hours: float = HEARTBEAT_MAX_AGE_HOURS) -> bool:
    """True when nothing was recorded within `max_age_hours` — even a failed
    run counts as a heartbeat (its failure already alerted); this catches the
    scheduler itself going silent. Compare digest_is_stale, which also flags
    failed runs."""
    age = last_digest_age()
    return age is None or age > timedelta(hours=max_age_hours)


def format_summary() -> str:
    data = load()
    if not data:
        return "No digest runs recorded yet."
    lines = [
        f"Last digest: {data.get('last_digest_at', '?')}",
        f"  ok={data.get('ok')}  collected={data.get('collected')}  "
        f"ranked_new={data.get('ranked_new')}  pushed={data.get('pushed')}",
    ]
    if data.get("cross_source_deduped"):
        lines.append(f"  cross-source deduped: {data['cross_source_deduped']}")
    if data.get("stale_sources"):
        lines.append(f"  stale sources: {', '.join(data['stale_sources'])}")
    if data.get("error"):
        lines.append(f"  error: {data['error']}")
    if digest_is_stale():
        lines.append("  [stale] No successful digest in ~30h — check launchd / logs")
    return "\n".join(lines)