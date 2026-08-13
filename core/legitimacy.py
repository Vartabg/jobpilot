"""Deterministic legitimacy assessment for opportunity evidence."""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from jobpilot.core.opportunity_models import (
    LegitimacyAssessment,
    SourceObservation,
    SourceRunResult,
)

_BLOCKED_STATES = frozenset({"404", "closed", "expired", "removed", "unavailable"})
_ACTIVE_STATES = frozenset({"active", "listed", "open", "published"})
_SCAM_PATTERNS = (
    re.compile(r"\b(?:application|interview|training|background check) fee\b", re.I),
    re.compile(r"\b(?:deposit|cash) (?:a |the |our )?check\b", re.I),
    re.compile(r"\b(?:bitcoin|cryptocurrency|usdt|crypto wallet)\b", re.I),
    re.compile(r"\b(?:buy|purchase|pay for) (?:your |the |any )?equipment\b", re.I),
)


def _as_utc(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _result(
    grade: str,
    state: str,
    reasons: list[str],
    evidence: list[str],
    now: datetime,
    verified_at: str = "",
) -> LegitimacyAssessment:
    return LegitimacyAssessment(
        grade,
        state,
        reasons,
        evidence,
        now.astimezone(UTC).isoformat(),
        verified_at,
    )


def assess_legitimacy(
    observations: Iterable[SourceObservation] | SourceObservation,
    *,
    source_runs: Iterable[SourceRunResult] = (),
    now: datetime | None = None,
) -> LegitimacyAssessment:
    """Grade evidence and select a fail-closed action state."""
    observed = [observations] if isinstance(observations, SourceObservation) else list(observations)
    runs = list(source_runs)
    instant = (now or datetime.now(UTC)).astimezone(UTC)
    evidence = [f"{item.provider}:{item.provider_job_id or 'unresolved'}" for item in observed]
    text = " ".join(f"{item.title} {item.description}" for item in observed)

    scam = any(pattern.search(text) for pattern in _SCAM_PATTERNS)
    direct_states = {item.listing_state for item in observed if item.is_direct}
    conflict = bool(direct_states & _ACTIVE_STATES and direct_states & _BLOCKED_STATES)
    closed = any(item.listing_state in _BLOCKED_STATES for item in observed)
    past_expiry = any(
        expiry is not None and expiry < instant
        for expiry in (_as_utc(item.expires_at) for item in observed)
    )
    if scam or conflict or closed or past_expiry:
        reasons: list[str] = []
        if scam:
            reasons.append("scam_payment_pattern")
        if conflict:
            reasons.append("conflicting_listing_state")
        if closed:
            reasons.append("closed_or_expired_listing")
        if past_expiry:
            reasons.append("past_expiry")
        return _result("F", "block", reasons, evidence, instant)

    direct = [item for item in observed if item.is_direct]
    current_direct = [
        item for item in direct
        if item.listing_state in _ACTIVE_STATES
        and (stamp := _as_utc(item.fetched_at)) is not None
        and timedelta(0) <= instant - stamp <= timedelta(hours=24)
    ]
    if current_direct:
        complete = any(
            item.tenant and item.provider_job_id and item.canonical_url and item.description
            for item in current_direct
        )
        grade = "A" if complete else "B"
        reasons = ["current_direct_listing"]
        if not complete:
            reasons.append("incomplete_listing_details")
        verified_at = max(
            stamp for item in current_direct
            if (stamp := _as_utc(item.fetched_at)) is not None
        ).isoformat()
        return _result(
            grade, "recommend", reasons, evidence, instant, verified_at
        )

    if direct:
        stamps = [_as_utc(item.fetched_at) for item in direct]
        if not any(stamps):
            reason = "unknown_verification_time"
        elif any(item.listing_state not in _ACTIVE_STATES for item in direct):
            reason = "listing_state_unconfirmed"
        else:
            reason = "stale_verification"
        return _result("D", "hold", [reason], evidence, instant)

    aggregators = [item for item in observed if item.source_kind == "aggregator"]
    recruiters = [item for item in observed if item.source_kind == "recruiter"]
    if aggregators:
        return _result("C", "review", ["aggregator_only"], evidence, instant)
    if recruiters:
        return _result("D", "review", ["recruiter_only"], evidence, instant)
    if any(run.status == "failure" for run in runs):
        return _result("E", "hold", ["source_failure"], evidence, instant)
    return _result("E", "hold", ["insufficient_source_evidence"], evidence, instant)
