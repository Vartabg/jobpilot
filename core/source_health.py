"""Concise, privacy-safe health reporting for employment search sources."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from jobpilot.core.opportunity_models import SourceRunResult

_ADVICE = {
    "ConfigurationError": "provider configuration is missing",
    "HTTPError": "provider rejected the request or the target is unavailable",
    "UnsupportedPortal": "provider type is unsupported",
}


def _failure_advice(error_type: str) -> str:
    if error_type in _ADVICE:
        return _ADVICE[error_type]
    if error_type == "HTTP404":
        return "target no longer exists; verify or disable it"
    if error_type in {"HTTP401", "HTTP403"}:
        return "provider rejected automated discovery; prefer a direct ATS source"
    if error_type == "HTTP429":
        return "provider rate-limited the scan; retry later"
    if error_type.startswith("HTTP"):
        return "provider request failed; verify the target or retry later"
    return "temporary or unclassified source failure"


@dataclass(frozen=True)
class SourceHealthSummary:
    """Aggregate source-run facts without retaining target URLs or queries."""

    total: int
    healthy: int
    with_results: int
    zero_results: int
    failed: int
    skipped: int
    failure_groups: tuple[tuple[str, str, int], ...] = ()

    @property
    def degraded(self) -> bool:
        return self.failed > 0

    @property
    def summary_line(self) -> str:
        if not self.total:
            return "Source health: no targets were scanned."
        return (
            f"Source health: {self.healthy}/{self.total} targets responded "
            f"({self.with_results} with matches, {self.zero_results} with none); "
            f"{self.failed} failed; {self.skipped} disabled."
        )

    @property
    def notices(self) -> tuple[str, ...]:
        return tuple(
            f"{provider}: {count} target{'s' if count != 1 else ''} — "
            f"{_failure_advice(error_type)}"
            for provider, error_type, count in self.failure_groups
        )


def summarize_source_runs(
    runs: Iterable[SourceRunResult],
) -> SourceHealthSummary:
    """Summarize one scan without exposing target values or provider errors."""
    items = list(runs)
    statuses = Counter(item.status for item in items)
    failures = Counter(
        (item.provider or "unknown", item.error_type or "UnknownError")
        for item in items
        if item.status == "failure"
    )
    groups = tuple(
        (provider, error_type, count)
        for (provider, error_type), count in sorted(failures.items())
    )
    successful = statuses["success"]
    zero = statuses["zero"]
    return SourceHealthSummary(
        total=len(items),
        healthy=successful + zero,
        with_results=successful,
        zero_results=zero,
        failed=statuses["failure"],
        skipped=statuses["skipped"],
        failure_groups=groups,
    )
