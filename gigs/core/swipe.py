"""Swipe engine — the on-demand, one-at-a-time mobile job flow.

Builds a ranked queue of fresh gigs (same scan/score/geo/currency path as the
digest), renders a phone card per gig with the apply prepped, and records a
swipe decision (apply -> sent, pass -> passed) back into pipeline.md. The
on-demand model: "give me the jobs", swipe through, apply with one tap.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable, Optional
from urllib.parse import quote

from jobpilot.gigs.core import pipeline, preferences
from jobpilot.gigs.core.collect import collect_all
from jobpilot.gigs.core.dedupe import dedupe_cross_source
from jobpilot.gigs.core.dispatcher import _fmt_pay
from jobpilot.gigs.core.models import Gig
from jobpilot.gigs.core.pipeline import Row
from jobpilot.gigs.core.proposals import (
    build_revenue_brief,
    contains_placeholder,
    email_body,
    email_subject,
)
from jobpilot.gigs.core.scorer import _posted_age_days, filter_and_rank
from jobpilot.gigs.core.scrapers.weworkremotely import enrich_apply_urls
from jobpilot.gigs.core.store import filter_new, mark_seen, unmark_seen


def build_queue(
    *, limit: int = 40, min_score: int = 55, fresh_only: bool = False,
    on_progress: Optional[Callable[[str], None]] = None,
) -> list[Gig]:
    """Ranked roles to swipe — home-metro/remote + currency-aware (same filters
    as the digest), minus anything already DECIDED in the pipeline.

    fresh_only defaults False so the swiper shows every undecided role: the
    digest marks its best finds seen, so fresh_only=True would hide exactly the
    high-fit jobs sitting in pipeline.md as `new`. 'Decided' (the pipeline-status
    gate) is what keeps already-handled roles out — not seen.json.
    """
    gigs, _results = collect_all(on_progress=on_progress)
    if fresh_only:
        fresh = set(filter_new([g.id for g in gigs]))
        gigs = [g for g in gigs if g.id in fresh]
    gigs, _ = dedupe_cross_source(gigs)
    decided = {r.gig_id for r in pipeline.parse() if r.gig_id and r.status != "new"}
    gigs = [g for g in gigs if g.id not in decided]
    ranked = filter_and_rank(
        gigs, min_score=min_score, top_n=limit,
        contract_first=True, drop_rigid_schedule=True,
    )
    # Resolve WWR listings to a real apply target (mailto/ATS/careers) so the
    # Apply tap doesn't dead-end on the paywalled aggregator page. Network
    # fetches are capped inside enrich_apply_urls.
    try:
        enrich_apply_urls(ranked)
    except Exception:
        pass
    return ranked


def _apply_target(gig: Gig) -> tuple[str, bool]:
    """(target, is_mailto). For mailto leads, a prefilled mailto so tapping
    opens the phone's Mail composer ready to send; otherwise the apply URL.
    Falls back to the source post if a draft placeholder ever leaks."""
    base = gig.apply_url or gig.url
    if base.lower().startswith("mailto:"):
        subj, body = email_subject(gig), email_body(gig)
        if contains_placeholder(subj) or contains_placeholder(body):
            return gig.url, False
        addr = base[len("mailto:"):].split("?", 1)[0]
        return f"mailto:{addr}?subject={quote(subj)}&body={quote(body)}", True
    return base, False


def card(gig: Gig) -> dict:
    """Everything the phone card needs for one gig."""
    brief = build_revenue_brief(gig)
    target, is_mailto = _apply_target(gig)
    return {
        "id": gig.id,
        "company": gig.company or gig.source,
        "role": (gig.title or "").split("|")[0].strip() or "Role",
        "score": gig.fit_score,
        "pay": _fmt_pay(gig),
        "location": gig.location or "—",
        "why": [r for r in (gig.fit_reasons or [])[:4]],
        "offer": brief.offer,
        "subject": email_subject(gig),
        "draft": email_body(gig),
        "apply_target": target,
        "is_mailto": is_mailto,
        "resume": preferences.resume_for(brief.offer),
        "source_url": gig.url,
        "source": gig.source,
        "posted_age_days": _posted_age_days(gig.posted_at),
        "tags": (gig.tags or [])[:6],
    }


def _upsert_row(gig: Gig, status: str, note: str = ""):
    """Set this gig's pipeline status to `status` and persist. Returns the
    WriteResult so the caller can tell if the write was refused (shrink guard)
    and avoid reporting a false success. The status is passed as authoritative
    so the writer's disk-wins merge can't revert the swipe back to its old
    status."""
    rows = pipeline.parse()
    today = datetime.now().strftime("%-m/%-d")
    for r in rows:
        if r.gig_id and r.gig_id == gig.id:
            r.status = status
            r.last_touched = today
            if note:
                r.notes = f"{r.notes} {note}".strip() if r.notes else note
            break
    else:
        rows.append(Row(
            status=status,
            score=gig.fit_score,
            company=gig.company or gig.source,
            role=(gig.title or "").split("|")[0].strip()[:80],
            pay=pipeline._fmt_pay_for_pipeline(gig),
            apply=gig.apply_url or gig.url,
            last_touched=today,
            notes=note,
            gig_id=gig.id,
        ))
    return pipeline.write(rows, authoritative_status={gig.id: status})


def record_decision(gig: Gig, action: str, reason: str = "") -> str:
    """Record a swipe. apply -> 'sent', pass -> 'passed' (+ optional reason).
    Only marks the gig seen if the pipeline write actually persisted — a
    refused write must not silently swallow the decision. Returns the stored
    status, or raises RuntimeError if the write was refused."""
    status = "sent" if action == "apply" else "passed"
    note = f"pass:{reason}" if (action != "apply" and reason) else ""
    result = _upsert_row(gig, status, note)
    if getattr(result, "refused", False):
        raise RuntimeError(f"pipeline write refused — {gig.id} not recorded")
    mark_seen([gig.id])
    return status


def undo_decision(gig: Gig) -> None:
    """Revert a swipe: status back to 'new' and un-mark seen so it resurfaces."""
    _upsert_row(gig, "new")
    unmark_seen([gig.id])
