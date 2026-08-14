"""Read-only application-history queries for agent-facing operations."""

from __future__ import annotations

import sqlite3

from jobpilot.core.application_tracker import ApplicationTracker


def read_tracker_status(
    company: str,
    title: str,
    url: str,
) -> tuple[bool, str | None]:
    """Return whether history was readable and the exact-role status."""
    try:
        tracker = ApplicationTracker.open_readonly()
    except FileNotFoundError:
        return True, None
    except (OSError, sqlite3.Error, ValueError):
        return False, None
    try:
        return True, tracker.role_status(company, title, url)
    except (OSError, sqlite3.Error, ValueError):
        return False, None
    finally:
        tracker.close()


def read_tracker_stats() -> dict[str, int]:
    """Read aggregate history without creating or repairing the database."""
    try:
        tracker = ApplicationTracker.open_readonly()
    except FileNotFoundError:
        return {}
    except (OSError, sqlite3.Error, ValueError):
        return {}
    try:
        return tracker.get_stats()
    except (OSError, sqlite3.Error, ValueError):
        return {}
    finally:
        tracker.close()
