"""Turn the legacy lanes' loose timestamps into the engine's UTC form."""

from __future__ import annotations

from datetime import date, datetime, time

from jobpilot.engine.domain import normalize_timestamp


def to_utc(value: str, default: str) -> tuple[str, bool]:
    """Return ``(utc_timestamp, estimated)`` for a legacy timestamp.

    - A timestamp with a zone is exact.
    - A naive timestamp is read as this machine's local time, which is how the
      legacy lanes wrote it, and is also exact.
    - A bare date becomes local noon, marked as estimated, so a zone shift can
      never move it to another day.
    - Anything else falls back to ``default``, marked as estimated.
    """
    text = (value or "").strip()
    if text:
        try:
            if len(text) == 10:
                moment = datetime.combine(
                    date.fromisoformat(text), time(12)
                ).astimezone()
                return normalize_timestamp(moment.isoformat()), True
            moment = datetime.fromisoformat(text)
            if moment.tzinfo is None:
                moment = moment.astimezone()
            return normalize_timestamp(moment.isoformat()), False
        except ValueError:
            pass
    return normalize_timestamp(default), True
