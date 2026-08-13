"""Shared data loaders for autonomous-income terminal views (radar, hud, board)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from jobpilot.core import queue_builder
from jobpilot.core.queue_builder import QueueJob
from jobpilot.core.work_style import is_contract_friendly, is_schedule_rigid
from jobpilot.gigs.core.collect import collect_all
from jobpilot.gigs.core.dedupe import dedupe_cross_source
from jobpilot.gigs.core.models import Gig
from jobpilot.gigs.core.scorer import filter_and_rank
from jobpilot.gigs.core.store import filter_new
from jobpilot.ui.view_helpers import is_senior_title

_DECISION_RANK = {
    "apply_now": 4,
    "stretch": 3,
    "investigate": 2,
    "skip": 1,
}
_LEGITIMACY_RANK = {
    "recommend": 4,
    "review": 3,
    "hold": 2,
    "block": 1,
}


@dataclass
class IncomeViewOptions:
    """Shared filters for HUD and radar (gigs + backup ATS jobs).

    ``hide_senior_jobs`` matches ``BoardFilters.autonomous`` on the queue board.
    """

    austin: bool = True
    contract_first: bool = True
    drop_rigid_schedule: bool = True
    gigs_limit: int = 30
    jobs_limit: int = 20
    min_gig_score: int = 45
    gigs_fresh_only: bool = True
    hide_senior_jobs: bool = True
    pipeline_limit: int = 12


def gig_pay_label(gig: Gig) -> str:
    if gig.pay_hourly_est:
        return f"${gig.pay_hourly_est:.0f}/hr"
    if gig.salary_max and gig.salary_min:
        return f"${gig.salary_min / 1000:.0f}-${gig.salary_max / 1000:.0f}K"
    if gig.salary_max:
        return f"≤${gig.salary_max / 1000:.0f}K"
    return "?"


def gig_badges(gig: Gig) -> str:
    text = f"{gig.title} {gig.description}"
    bits = []
    if is_contract_friendly(text, title=gig.title):
        bits.append("[green]C[/]")
    if any("async" in r for r in (gig.fit_reasons or [])):
        bits.append("[cyan]A[/]")
    if is_schedule_rigid(text, title=gig.title):
        bits.append("[red]9-5[/]")
    return "".join(bits) or "[dim]·[/]"


def gig_top_reason(gig: Gig) -> str:
    for r in gig.fit_reasons or []:
        clean = r.replace("+", "").split(":", 1)[-1][:28]
        if clean:
            return clean
    return ""


def short_url(url: str, max_len: int = 42) -> str:
    u = (url or "").strip()
    if len(u) <= max_len:
        return u
    return u[: max_len - 3] + "..."


def _evidence_number(value: Any) -> int:
    """Normalize persisted evidence values without promoting missing data."""
    if value is None or value == "":
        return -1
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def _is_apply_ready(job: QueueJob) -> bool:
    """Delegate to the canonical queue gate and fail closed on malformed rows."""
    try:
        return bool(queue_builder.is_apply_ready(job))
    except Exception:
        return False


def _job_evidence_sort_key(job: QueueJob) -> tuple[int, int, int, int, int, int]:
    """Rank each evidence axis once, with aggregate fit only as a tie-breaker."""
    return (
        _DECISION_RANK.get(str(getattr(job, "decision", "")), 0),
        _LEGITIMACY_RANK.get(str(getattr(job, "legitimacy_state", "")), 0),
        _evidence_number(getattr(job, "qualification_lower_bound", None)),
        _evidence_number(getattr(job, "evidence_coverage", None)),
        _evidence_number(getattr(job, "work_context_match", None)),
        _evidence_number(getattr(job, "fit_score", None)),
    )


def load_gigs(
    opts: IncomeViewOptions,
    *,
    on_progress: Callable[[str], None] | None = None,
) -> tuple[list[Gig], dict[str, Any]]:
    gigs, results = collect_all(on_progress=on_progress)
    meta: dict[str, Any] = {
        "sources": [],
        "collected": len(gigs),
        "fresh_only": opts.gigs_fresh_only,
    }
    for r in results:
        meta["sources"].append(
            {"name": r.name, "ok": r.ok, "fetched": r.fetched, "error": r.error}
        )

    if opts.gigs_fresh_only:
        new_ids = set(filter_new([g.id for g in gigs]))
        meta["fresh_count"] = len(new_ids)
        gigs = [g for g in gigs if g.id in new_ids]
    gigs, deduped = dedupe_cross_source(gigs)
    meta["deduped"] = deduped
    ranked = filter_and_rank(
        gigs,
        min_score=opts.min_gig_score,
        top_n=opts.gigs_limit,
        contract_first=opts.contract_first,
        drop_rigid_schedule=opts.drop_rigid_schedule,
    )
    meta["shown"] = len(ranked)
    return ranked, meta


def load_jobs(opts: IncomeViewOptions) -> list[QueueJob]:
    jobs = [
        job for job in queue_builder.load_current_slate()
        if _is_apply_ready(job)
    ]
    if opts.hide_senior_jobs:
        jobs = [j for j in jobs if not is_senior_title(j.title)]
    if opts.austin:
        jobs = [
            j
            for j in jobs
            if "austin" in (j.location or "").lower()
            or "remote" in (j.location or "").lower()
            or (j.location or "").lower() in {"", "not specified", "united states"}
        ]
    jobs.sort(key=_job_evidence_sort_key, reverse=True)
    return jobs[: opts.jobs_limit]


def load_pipeline_rows(opts: IncomeViewOptions) -> list[Any]:
    try:
        from jobpilot.gigs.core import pipeline

        rows = pipeline.parse()
    except Exception:
        return []
    active_statuses = {"new", "saved", "drafted", "sent", "replied", "interview"}
    rows = [r for r in rows if r.status in active_statuses]
    rows.sort(key=lambda r: (-(r.score or 0), r.status))
    return rows[: opts.pipeline_limit]


def pipeline_summary(rows: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.status] = counts.get(r.status, 0) + 1
    return counts
