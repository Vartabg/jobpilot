"""Swipe engine — the on-demand, one-at-a-time mobile job flow.

Builds a ranked queue of fresh gigs (same scan/score/geo/currency path as the
digest), renders a phone card per gig with the apply prepped, and records a
swipe decision (apply -> sent, pass -> passed) back into pipeline.md. The
on-demand model: "give me the jobs", swipe through, apply with one tap.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from datetime import datetime
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
    draft_mode,
    email_body,
    email_subject,
)
from jobpilot.gigs.core.scorer import _posted_age_days, filter_and_rank
from jobpilot.gigs.core.scrapers.weworkremotely import enrich_apply_urls
from jobpilot.gigs.core.store import filter_new, mark_seen, unmark_seen


def build_queue(
    *,
    limit: int | None = None,
    min_score: int | None = None,
    fresh_only: bool = False,
    on_progress: Callable[[str], None] | None = None,
) -> list[Gig]:
    """Ranked roles to swipe — same search gate as `gigs now` (prefs.search),
    minus anything already DECIDED in the pipeline.

    fresh_only defaults False so the swiper shows every undecided role: the
    digest marks its best finds seen, so fresh_only=True would hide exactly the
    high-fit jobs sitting in pipeline.md as `new`. 'Decided' (the pipeline-status
    gate) is what keeps already-handled roles out — not seen.json.
    """
    cfg = preferences.search_config()
    if min_score is None:
        min_score = int(cfg.get("min_score", 60))
    if limit is None:
        limit = max(int(cfg.get("top_n", 12)) * 3, 40)
    contract_first = bool(cfg.get("contract_first", False))
    drop_rigid = bool(cfg.get("drop_rigid_schedule", True))

    # Mobile backlog hygiene first — stale/overflow `new` rows otherwise
    # reappear forever in Get jobs (status "new" is not "decided").
    try:
        from jobpilot.gigs.core import pipeline as _pipe

        hyg = _pipe.archive_stale_new()
        if on_progress and hyg.get("archived"):
            on_progress(
                f"[dim]Backlog: archived {hyg['archived']} "
                f"(age={hyg.get('archived_age', 0)}, cap={hyg.get('archived_cap', 0)})[/]",
            )
    except Exception:
        pass

    gigs, _results = collect_all(on_progress=on_progress)
    if fresh_only:
        fresh = set(filter_new([g.id for g in gigs]))
        gigs = [g for g in gigs if g.id in fresh]
    gigs, _ = dedupe_cross_source(gigs)
    decided = {r.gig_id for r in pipeline.parse() if r.gig_id and r.status != "new"}
    gigs = [g for g in gigs if g.id not in decided]
    ranked = filter_and_rank(
        gigs,
        min_score=min_score,
        top_n=limit,
        contract_first=contract_first,
        drop_rigid_schedule=drop_rigid,
    )
    # Resolve WWR listings to a real apply target (mailto/ATS/careers) so the
    # Apply tap doesn't dead-end on the paywalled aggregator page. Network
    # fetches are capped inside enrich_apply_urls.
    with contextlib.suppress(Exception):
        enrich_apply_urls(ranked)
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
        addr = base[len("mailto:") :].split("?", 1)[0]
        return f"mailto:{addr}?subject={quote(subj)}&body={quote(body)}", True
    return base, False


def criteria_pills() -> list[str]:
    """Short gate labels for the mobile header (not the full CLI report)."""
    loc = preferences.location_config()
    search = preferences.search_config()
    pills: list[str] = []
    tags = loc.get("home_metro_tags") or []
    if tags:
        pills.append(str(tags[0]).title() if isinstance(tags[0], str) else str(tags[0]))
    if loc.get("require_home_or_remote"):
        pills.append("Home/remote only")
    elif loc.get("allow_remote"):
        pills.append("Remote OK")
    pills.append(f"Score ≥{search.get('min_score', 60)}")
    if search.get("drop_rigid_schedule"):
        pills.append("Anti 9–5")
    if search.get("contract_first"):
        pills.append("Contract first")
    pills.append("Pay not filtered")
    titles = search.get("target_titles") or []
    if titles:
        # First two targets so the strip stays scannable on a phone.
        pills.append(" · ".join(str(t) for t in titles[:2]))
    return pills


def session_meta() -> dict:
    """Session-level payload for the phone (criteria + default resume)."""
    resumes = preferences.resumes_map()
    return {
        "criteria_pills": criteria_pills(),
        "default_resume": resumes.get("default") or "",
        "resumes": resumes,
        "notes": list(preferences.search_config().get("notes") or []),
        "on_demand": True,
    }


def card(gig: Gig) -> dict:
    """Everything the phone card needs for one gig — including crib paste pack."""
    from jobpilot.gigs.core.crib import mobile_crib

    brief = build_revenue_brief(gig)
    target, is_mailto = _apply_target(gig)
    resume = preferences.resume_for(brief.offer)
    mode = draft_mode(gig)
    return {
        "id": gig.id,
        "company": gig.company or gig.source,
        "role": (gig.title or "").split("|")[0].strip() or "Role",
        "score": gig.fit_score,
        "pay": _fmt_pay(gig),
        "location": gig.location or "—",
        "why": [r for r in (gig.fit_reasons or [])[:4]],
        "offer": brief.offer,
        "draft_mode": mode,  # "fte" | "contract" — phone shows which pitch
        "subject": email_subject(gig),
        "draft": email_body(gig),
        "apply_target": target,
        "is_mailto": is_mailto,
        "resume": resume,
        "source_url": gig.url,
        "source": gig.source,
        "posted_age_days": _posted_age_days(gig.posted_at),
        "tags": (gig.tags or [])[:6],
        "crib": mobile_crib(gig),
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
        rows.append(
            Row(
                status=status,
                score=gig.fit_score,
                company=gig.company or gig.source,
                role=(gig.title or "").split("|")[0].strip()[:80],
                pay=pipeline._fmt_pay_for_pipeline(gig),
                apply=gig.apply_url or gig.url,
                last_touched=today,
                notes=note,
                gig_id=gig.id,
            )
        )
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
