"""Shared data loaders for autonomous-income terminal views (radar, hud, board)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from collections.abc import Callable
from typing import Any, Optional

from jobpilot.core.queue_builder import QueueJob, load_queue
from jobpilot.core.work_style import is_contract_friendly, is_schedule_rigid
from jobpilot.gigs.core.collect import collect_all
from jobpilot.gigs.core.dedupe import dedupe_cross_source
from jobpilot.gigs.core.models import Gig
from jobpilot.gigs.core.scorer import apply_friction, filter_and_rank
from jobpilot.gigs.core.store import filter_new
from jobpilot.ui.view_helpers import is_senior_title


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
        return f"${gig.salary_min/1000:.0f}-${gig.salary_max/1000:.0f}K"
    if gig.salary_max:
        return f"≤${gig.salary_max/1000:.0f}K"
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


def load_gigs(
    opts: IncomeViewOptions,
    *,
    on_progress: Optional[Callable[[str], None]] = None,
) -> tuple[list[Gig], dict[str, Any]]:
    gigs, results = collect_all(on_progress=on_progress)
    meta: dict[str, Any] = {"sources": [], "collected": len(gigs), "fresh_only": opts.gigs_fresh_only}
    for r in results:
        meta["sources"].append({"name": r.name, "ok": r.ok, "fetched": r.fetched, "error": r.error})

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
    jobs = [j for j in load_queue() if j.status == "queued"]
    if opts.hide_senior_jobs:
        jobs = [j for j in jobs if not is_senior_title(j.title)]
    if opts.austin:
        jobs = [
            j for j in jobs
            if "austin" in (j.location or "").lower()
            or "remote" in (j.location or "").lower()
            or (j.location or "").lower() in {"", "not specified", "united states"}
        ]
    jobs.sort(key=lambda j: (j.fit_score, j.psyche_score), reverse=True)
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