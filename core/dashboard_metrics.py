"""Privacy-safe operational metrics for the human-in-the-loop dashboard."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jobpilot.core.config import DATA_DIR
from jobpilot.core.opportunity_ledger import OpportunityLedger
from jobpilot.core.outcome_metrics import summarize_outcomes
from jobpilot.core.queue_builder import QueueJob, is_apply_ready
from jobpilot.core.relocation import RelocationState


def _latest_source_health(connection: sqlite3.Connection) -> dict[str, Any]:
    rows = connection.execute(
        "SELECT source, target, status, result_count, fetched_at, payload_json "
        "FROM source_runs ORDER BY fetched_at, id"
    ).fetchall()
    latest: dict[tuple[str, str], sqlite3.Row] = {}
    for row in rows:
        latest[(str(row["source"]), str(row["target"]))] = row
    statuses = Counter(str(row["status"]) for row in latest.values())
    failures = Counter()
    for row in latest.values():
        if row["status"] != "failure":
            continue
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            payload = {}
        failures[(str(row["source"]), str(payload.get("error_type") or "UnknownError"))] += 1
    healthy = statuses["success"] + statuses["zero"]
    return {
        "total": len(latest),
        "healthy": healthy,
        "with_results": statuses["success"],
        "zero_results": statuses["zero"],
        "failed": statuses["failure"],
        "disabled": statuses["skipped"],
        "matched_listings": sum(int(row["result_count"] or 0) for row in latest.values()),
        "last_scan_at": max((str(row["fetched_at"]) for row in latest.values()), default=""),
        "failure_groups": [
            {"provider": provider, "error_type": error_type, "count": count}
            for (provider, error_type), count in sorted(failures.items())
        ],
    }


def collect_dashboard_metrics(
    jobs: list[QueueJob],
    *,
    tracker_stats: dict[str, Any] | None = None,
    db_path: Path | None = None,
    days: int = 30,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Combine cached queue, source, and explicit outcome facts without mutation."""
    current = (now or datetime.now(UTC)).astimezone(UTC)
    active = [job for job in jobs if job.status in {"queued", "viewing"}]
    current_direct = [job for job in jobs if job.legitimacy_state == "recommend"]
    active_current_direct = [
        job for job in active if job.legitimacy_state == "recommend"
    ]
    relocation_counts = Counter(job.relocation_state for job in current_direct)
    supported_relocation = sum(
        job.relocation_state
        in {RelocationState.OFFERED, RelocationState.CONDITIONAL}
        for job in active_current_direct
    )
    queue_summary = {
        "roles": len(jobs),
        "active": len(active),
        "current_direct": len(current_direct),
        "ready": sum(is_apply_ready(job, now=current) for job in jobs),
        "investigate": sum(job.decision == "investigate" for job in active),
        "supported_relocation": supported_relocation,
        "status_counts": dict(Counter(job.status for job in jobs)),
        "decision_counts": dict(Counter(job.decision for job in jobs)),
    }
    relocation = {
        state.value: relocation_counts[state]
        for state in RelocationState
    }
    source_health: dict[str, Any] = {
        "available": False,
        "total": 0,
        "healthy": 0,
        "with_results": 0,
        "zero_results": 0,
        "failed": 0,
        "disabled": 0,
        "matched_listings": 0,
        "last_scan_at": "",
        "failure_groups": [],
    }
    outcomes = summarize_outcomes([], days=days, now=current)
    path = Path(db_path) if db_path else DATA_DIR / "opportunities.db"
    try:
        ledger = OpportunityLedger.open_readonly(db_path=path)
        try:
            source_health.update(_latest_source_health(ledger.connection))
            source_health["available"] = True
            events = [asdict(event) for event in ledger.iter_events()]
            outcomes = summarize_outcomes(events, days=days, now=current)
        finally:
            ledger.close()
    except (FileNotFoundError, OSError, sqlite3.Error, ValueError):
        pass
    applied = int(outcomes["stages"].get("applied", 0))
    positive = int(outcomes.get("positive_human_response", 0))
    interviews = int(outcomes["stages"].get("interview", 0))
    outcomes["response_rate"] = round(100 * positive / applied) if applied else None
    outcomes["interview_rate"] = round(100 * interviews / applied) if applied else None
    outcomes["calibration_state"] = (
        "manual_review_eligible" if outcomes["may_recalibrate"] else "fixed"
    )
    return {
        "generated_at": current.isoformat(),
        "queue": queue_summary,
        "relocation": relocation,
        "source_health": source_health,
        "outcomes": outcomes,
        "tracker": dict(tracker_stats or {}),
    }
