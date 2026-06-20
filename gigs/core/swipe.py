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
from jobpilot.gigs.core.scorer import filter_and_rank
from jobpilot.gigs.core.store import filter_new, mark_seen


def build_queue(
    *, limit: int = 40, min_score: int = 55, fresh_only: bool = True,
    on_progress: Optional[Callable[[str], None]] = None,
) -> list[Gig]:
    """Ranked fresh gigs to swipe — home-metro/remote + currency-aware (same
    filters as the digest), minus anything already decided in the pipeline."""
    gigs, _results = collect_all(on_progress=on_progress)
    if fresh_only:
        fresh = set(filter_new([g.id for g in gigs]))
        gigs = [g for g in gigs if g.id in fresh]
    gigs, _ = dedupe_cross_source(gigs)
    decided = {r.gig_id for r in pipeline.parse() if r.gig_id and r.status != "new"}
    gigs = [g for g in gigs if g.id not in decided]
    return filter_and_rank(
        gigs, min_score=min_score, top_n=limit,
        contract_first=True, drop_rigid_schedule=True,
    )


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
        "tags": (gig.tags or [])[:6],
    }


def _upsert_row(gig: Gig, status: str, note: str = "") -> None:
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
    pipeline.write(rows)


def record_decision(gig: Gig, action: str, reason: str = "") -> str:
    """Record a swipe. apply -> 'sent', pass -> 'passed' (+ optional reason).
    Marks the gig seen so it won't resurface. Returns the stored status."""
    if action == "apply":
        _upsert_row(gig, "sent")
        mark_seen([gig.id])
        return "sent"
    note = f"pass:{reason}" if reason else ""
    _upsert_row(gig, "passed", note)
    mark_seen([gig.id])
    return "passed"
