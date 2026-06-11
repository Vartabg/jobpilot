"""Per-source scrape health for observability and stale-source alerts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from jobpilot.gigs.core.io_lock import atomic_write_text, file_lock
from jobpilot.gigs.core.paths import data_dir

DATA_DIR = data_dir()
HEALTH_PATH = DATA_DIR / "sources_health.json"
ZERO_ALERT_STREAK = 3


@dataclass(frozen=True)
class SourceResult:
    name: str
    fetched: int
    ok: bool
    error: str | None = None


def _load_unlocked() -> dict:
    if not HEALTH_PATH.exists():
        return {}
    try:
        data = json.loads(HEALTH_PATH.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _load() -> dict:
    with file_lock(HEALTH_PATH):
        return _load_unlocked()


def _save(data: dict) -> None:
    with file_lock(HEALTH_PATH):
        atomic_write_text(HEALTH_PATH, json.dumps(data, indent=2))


def record_results(results: list[SourceResult]) -> None:
    """Update health after a collect pass."""
    now = datetime.now().isoformat()
    data = _load()
    for result in results:
        entry = data.get(result.name, {})
        entry["last_run_at"] = now
        entry["last_count"] = result.fetched
        entry["last_ok"] = result.ok
        entry["last_error"] = result.error
        if result.ok and result.fetched > 0:
            entry["zero_streak"] = 0
            entry["last_success_at"] = now
        elif result.ok and result.fetched == 0:
            entry["zero_streak"] = int(entry.get("zero_streak", 0)) + 1
        else:
            entry["zero_streak"] = int(entry.get("zero_streak", 0)) + 1
        data[result.name] = entry
    _save(data)


def stale_zero_sources(*, min_streak: int = ZERO_ALERT_STREAK) -> list[str]:
    """Sources that returned zero gigs for `min_streak` consecutive OK runs."""
    out: list[str] = []
    for name, entry in _load().items():
        if entry.get("last_ok") and int(entry.get("zero_streak", 0)) >= min_streak:
            out.append(name)
    return sorted(out)


def failed_sources() -> list[tuple[str, str]]:
    """Sources whose last run raised an exception."""
    out: list[tuple[str, str]] = []
    for name, entry in sorted(_load().items()):
        if not entry.get("last_ok") and entry.get("last_error"):
            out.append((name, entry["last_error"]))
    return out


def warning_line(
    *,
    stale: list[str] | None = None,
    failed: list[tuple[str, str]] | None = None,
) -> str:
    """One-line warning for the digest push + markdown header. '' when healthy."""
    stale = stale_zero_sources() if stale is None else stale
    failed = failed_sources() if failed is None else failed
    parts: list[str] = []
    if failed:
        parts.append("failed: " + ", ".join(name for name, _ in failed))
    if stale:
        parts.append(f"zero gigs {ZERO_ALERT_STREAK}+ runs: " + ", ".join(stale))
    if not parts:
        return ""
    return "Source trouble — " + "; ".join(parts)


def format_dashboard() -> str:
    """Human-readable source health table."""
    data = _load()
    if not data:
        return "No source health recorded yet — run `gigpilot digest` or `scan`."
    lines = ["Source health:", ""]
    for name in sorted(data):
        entry = data[name]
        count = entry.get("last_count", "?")
        ok = "ok" if entry.get("last_ok") else "FAIL"
        streak = entry.get("zero_streak", 0)
        at = entry.get("last_run_at", "?")[:19]
        extra = f" zero_streak={streak}" if streak else ""
        err = entry.get("last_error")
        if err:
            extra += f" err={err[:60]}"
        lines.append(f"  {name:18} {ok:4}  count={count:4}{extra}  @ {at}")
    stale = stale_zero_sources()
    if stale:
        lines.append("")
        lines.append(f"Alert: zero results {ZERO_ALERT_STREAK}+ runs: {', '.join(stale)}")
    return "\n".join(lines)