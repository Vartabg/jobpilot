"""Time-scoped, event-based outcome metrics for JobPilot."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

STAGES = (
    "applied",
    "outreach",
    "human_reply",
    "screen",
    "interview",
    "offer",
    "rejected",
    "withdrawn",
    "no_response",
)
DECIDED = {"offer", "rejected", "withdrawn", "no_response"}
POSITIVE = {"human_reply", "screen", "interview", "offer"}
MIN_DECIDED_FOR_RECALIBRATION = 20


def _date(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def summarize_outcomes(
    events: Iterable[dict[str, Any]],
    *,
    days: int = 30,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Summarize explicit events; application silence remains censored."""
    current = (now or datetime.now(UTC)).astimezone(UTC)
    cutoff = current - timedelta(days=max(0, days))
    scoped: list[dict[str, Any]] = []
    for event in events:
        occurred = _date(event.get("occurred_at"))
        if occurred is not None and cutoff <= occurred <= current:
            scoped.append(event)

    stage_roles = {
        (
            str(event.get("opportunity_id", "")).strip(),
            str(event.get("event_type", "")).strip(),
        )
        for event in scoped
        if str(event.get("opportunity_id", "")).strip()
    }
    stages = Counter(stage for _role, stage in stage_roles)
    roles = {
        str(event.get("opportunity_id", "")).strip()
        for event in scoped
        if str(event.get("opportunity_id", "")).strip()
    }
    terminal_roles = {
        str(event.get("opportunity_id", "")).strip()
        for event in scoped
        if event.get("event_type") in DECIDED
    }
    positive_roles = {
        str(event.get("opportunity_id", "")).strip()
        for event in scoped
        if event.get("event_type") in POSITIVE
    }
    role_channel: dict[str, str] = {}
    for event in scoped:
        role = str(event.get("opportunity_id", "")).strip()
        provenance = event.get("provenance")
        provenance_channel = (
            provenance.get("acquisition_channel", "")
            if isinstance(provenance, dict)
            else ""
        )
        channel = str(
            event.get("acquisition_channel") or provenance_channel
        ).strip() or "unknown"
        if role and (channel != "unknown" or role not in role_channel):
            role_channel[role] = channel
    channels = Counter(
        role_channel.get(role, "unknown") for role in terminal_roles
    )
    explicit_role_lanes: set[tuple[str, str]] = set()
    for event in scoped:
        role = str(event.get("opportunity_id", "")).strip()
        provenance = event.get("provenance")
        lane = str(
            event.get("lane")
            or (provenance.get("lane", "") if isinstance(provenance, dict) else "")
        ).strip()
        if role and lane:
            explicit_role_lanes.add((role, lane))
    explicit_terminal_lanes = {
        (role, lane)
        for role, lane in explicit_role_lanes
        if role in terminal_roles
    }
    roles_with_lanes = {role for role, _lane in explicit_terminal_lanes}
    role_lanes = {
        *explicit_terminal_lanes,
        *((role, "unknown") for role in terminal_roles - roles_with_lanes),
    }
    lanes = Counter(lane for _role, lane in role_lanes)
    decided = len(terminal_roles)
    return {
        "days": days,
        "roles": len(roles),
        "decided": decided,
        "may_recalibrate": decided >= MIN_DECIDED_FOR_RECALIBRATION,
        "positive_human_response": len(positive_roles),
        "stages": {stage: stages[stage] for stage in STAGES},
        "channels": dict(channels),
        "lanes": dict(lanes),
    }
