"""Shared, JSON-friendly models for opportunity source evidence."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

DIRECT_ATS_PROVIDERS = frozenset({"ashby", "greenhouse", "lever"})
AGGREGATOR_PROVIDERS = frozenset({"adzuna", "google_jobs", "indeed"})


def utc_now_iso() -> str:
    """Return a stable UTC timestamp suitable for JSON and SQLite."""
    return datetime.now(UTC).isoformat()


def infer_source_kind(provider: str) -> str:
    """Classify a provider without treating the result as a trust score."""
    normalized = provider.strip().lower()
    if normalized in DIRECT_ATS_PROVIDERS:
        return "direct_ats"
    if normalized in AGGREGATOR_PROVIDERS:
        return "aggregator"
    if normalized == "recruiter":
        return "recruiter"
    return "unknown"


@dataclass
class SourceObservation:
    """One provider's observable facts about one role at one point in time."""

    provider: str = ""
    tenant: str = ""
    provider_job_id: str = ""
    canonical_url: str = ""
    fetched_at: str = ""
    listing_state: str = "unknown"
    source_kind: str = ""
    company: str = ""
    title: str = ""
    description: str = ""
    location: str = ""
    posted_at: str = ""
    expires_at: str = ""
    compensation: dict[str, Any] = field(default_factory=dict)
    workplace_type: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.provider = self.provider.strip().lower()
        self.tenant = self.tenant.strip().lower()
        self.provider_job_id = str(self.provider_job_id).strip()
        self.listing_state = self.listing_state.strip().lower() or "unknown"
        self.source_kind = self.source_kind.strip().lower() or infer_source_kind(self.provider)

    @property
    def is_direct(self) -> bool:
        return self.source_kind == "direct_ats"


@dataclass
class SourceRunResult:
    """Target-level result, including meaningful zero and failure outcomes."""

    provider: str
    target: str
    status: str
    result_count: int = 0
    fetched_at: str = field(default_factory=utc_now_iso)
    label: str = ""
    error_type: str = ""
    error: str = ""

    def __post_init__(self) -> None:
        self.provider = self.provider.strip().lower()
        self.status = self.status.strip().lower()
        if self.status not in {"success", "zero", "failure", "skipped"}:
            raise ValueError(f"Unsupported source-run status: {self.status}")


@dataclass
class LegitimacyAssessment:
    """Deterministic evidence grade and action gate; never a probability."""

    grade: str
    state: str
    reasons: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    evaluated_at: str = field(default_factory=utc_now_iso)
    verified_at: str = ""

    def __post_init__(self) -> None:
        self.grade = self.grade.upper()
        self.state = self.state.lower()
        if self.grade not in set("ABCDEF"):
            raise ValueError(f"Unsupported legitimacy grade: {self.grade}")
        if self.state not in {"recommend", "review", "hold", "block"}:
            raise ValueError(f"Unsupported legitimacy state: {self.state}")

    @property
    def can_apply(self) -> bool:
        return self.state == "recommend"
