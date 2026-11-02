"""Translate legacy lane records into ledger listings and events.

Each ``from_*`` function is pure: legacy records in, ``ImportRecord``s out.
``write`` applies records to any Ledger. Every event carries a deterministic
key, so re-running an import only adds what changed since the last run.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from jobpilot.bridge.timestamps import to_utc
from jobpilot.engine.domain import Event, EventKind, Lane, Listing, opportunity_id
from jobpilot.engine.ports import Ledger

K = EventKind

_ATS_PROVIDERS = frozenset({"greenhouse", "lever", "ashby", "indeed", "adzuna"})
_URL = re.compile(r"https?://[^\s)\]|>]+")


@dataclass(frozen=True)
class EventSpec:
    """An event waiting for its opportunity ID."""

    kind: EventKind
    occurred_at: str
    key: str
    data: Mapping[str, Any] = field(default_factory=dict)
    # Skip when the opportunity already has an event of this kind, e.g. a queue
    # row marked "applied" after the tracker already recorded the application.
    only_if_absent: bool = False


@dataclass(frozen=True)
class ImportRecord:
    listing: Listing
    seen_at: str
    events: tuple[EventSpec, ...]


@dataclass
class ImportReport:
    source: str
    records: int = 0
    new_opportunities: int = 0
    events_added: int = 0
    events_already_recorded: int = 0
    skipped: list[str] = field(default_factory=list)


def _clean(data: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in data.items() if value not in (None, "", [], {})
    }


# ----- queue.json (jobs lane) -------------------------------------------------

_QUEUE_OUTCOMES = {"applied": K.APPLIED, "skipped": K.SKIPPED, "rejected": K.REJECTED}


def from_queue(
    jobs: Iterable[Mapping[str, Any]], *, default_time: str
) -> tuple[list[ImportRecord], list[str]]:
    """Queue rows become discoveries, plus any decision the queue recorded.

    The queue never stored when a status changed, so a decision is timed at
    discovery and marked as estimated. "applied" and "rejected" marks that the
    legacy reconcile derived from application evidence are not imported as
    events: that matcher falls back to fuzzy company-and-title matching, and
    once marked a different role at the same company as applied. Real
    applications come from the tracker. A derived mark stays on the discovery
    event as ``legacy_status``.
    """
    records: list[ImportRecord] = []
    skipped: list[str] = []
    for job in jobs:
        portal = str(job.get("portal", "")).strip().lower()
        listing = Listing(
            company=str(job.get("company", "")),
            title=str(job.get("title", "")),
            url=str(job.get("url", "")),
            location=str(job.get("location", "")),
            lane=Lane.JOB,
            provider=portal if portal in _ATS_PROVIDERS else "",
        )
        try:
            identity = listing.identity_key()
        except ValueError as exc:
            skipped.append(f"queue row {job.get('id', '?')}: {exc}")
            continue
        seen, estimated = to_utc(str(job.get("queued_at", "")), default_time)
        ref = f"queue:{job.get('id') or identity}"
        events = [
            EventSpec(
                K.DISCOVERED,
                seen,
                f"{ref}:discovered",
                _clean(
                    {
                        "fit_score": job.get("fit_score"),
                        "psyche_score": job.get("psyche_score"),
                        "track": job.get("track"),
                        "portal": portal,
                        "suppression_reason": job.get("suppression_reason"),
                        "time_estimated": estimated or None,
                    }
                ),
            )
        ]
        status = str(job.get("status", "")).strip().lower()
        reason = str(job.get("suppression_reason", ""))
        derived = status in {"applied", "rejected"} and reason.startswith(
            "application evidence"
        )
        if derived:
            events[0] = EventSpec(
                K.DISCOVERED,
                seen,
                f"{ref}:discovered",
                {**events[0].data, "legacy_status": status},
            )
        elif status in _QUEUE_OUTCOMES:
            outcome = _QUEUE_OUTCOMES[status]
            events.append(
                EventSpec(
                    outcome,
                    seen,
                    f"{ref}:{outcome.value}",
                    {"legacy_status": status, "time_estimated": True},
                    only_if_absent=True,
                )
            )
        records.append(ImportRecord(listing, seen, tuple(events)))
    return records, skipped


# ----- applications.db (jobs lane tracker) ------------------------------------

_TRACKER_APPLIED = frozenset({"submitted", "applied", "rejected", "interview"})


def from_tracker(
    rows: Iterable[Mapping[str, Any]], *, default_time: str
) -> tuple[list[ImportRecord], list[str]]:
    """Tracker rows become applications and their later outcomes.

    Synthetic URLs (``manual://...``) carry no identity, so those rows fall back
    to company and title.
    """
    records: list[ImportRecord] = []
    skipped: list[str] = []
    for row in rows:
        url = str(row.get("job_url", ""))
        if not url.startswith(("http://", "https://")):
            url = ""
        listing = Listing(
            company=str(row.get("company", "")),
            title=str(row.get("job_title", "")),
            url=url,
            lane=Lane.JOB,
        )
        try:
            listing.identity_key()
        except ValueError as exc:
            skipped.append(f"tracker row {row.get('id', '?')}: {exc}")
            continue
        applied_at, applied_estimated = to_utc(
            str(row.get("applied_at", "")), default_time
        )
        updated_at, _ = to_utc(str(row.get("updated_at", "")), applied_at)
        status = str(row.get("status", "")).strip().lower()
        ref = f"tracker:{row.get('id')}"
        events: list[EventSpec] = []
        if status in _TRACKER_APPLIED:
            events.append(
                EventSpec(
                    K.APPLIED,
                    applied_at,
                    f"{ref}:applied",
                    _clean({"time_estimated": applied_estimated or None}),
                )
            )
        if status == "rejected":
            events.append(EventSpec(K.REJECTED, updated_at, f"{ref}:rejected"))
        elif status == "interview":
            events.append(EventSpec(K.INTERVIEW, updated_at, f"{ref}:interview"))
        elif status == "started":
            events.append(
                EventSpec(
                    K.SHORTLISTED,
                    applied_at,
                    f"{ref}:started",
                    {"legacy_status": status},
                )
            )
        elif status in {"abandoned", "skipped"}:
            events.append(
                EventSpec(
                    K.SKIPPED, updated_at, f"{ref}:{status}", {"legacy_status": status}
                )
            )
        records.append(ImportRecord(listing, applied_at, tuple(events)))
    return records, skipped


# ----- pipeline.md (gigs lane) ------------------------------------------------


class PipelineRow(Protocol):
    """The fields read from a gigs pipeline row (gigs.core.pipeline.Row)."""

    status: str
    score: int
    company: str
    role: str
    pay: str
    apply: str
    saved: str
    last_touched: str
    gig_id: str


# What each pipeline status implies happened, in order. Unknown labels the
# user invented are kept on the discovery event as ``legacy_status``.
_PIPELINE_PATHS: dict[str, tuple[EventKind, ...]] = {
    "new": (),
    "saved": (K.SHORTLISTED,),
    "drafted": (K.SHORTLISTED, K.KIT_PREPARED),
    "sent": (K.APPLIED,),
    "replied": (K.APPLIED, K.REPLIED),
    "interview": (K.APPLIED, K.INTERVIEW),
    "hired": (K.APPLIED, K.OFFER),
    "passed": (K.SKIPPED,),
    "archived": (K.SKIPPED,),
    "ghosted": (K.APPLIED, K.NO_RESPONSE),
    "withdrawn": (K.APPLIED, K.WITHDRAWN),
    "rejected": (K.APPLIED, K.REJECTED),
}


def from_pipeline(
    rows: Iterable[PipelineRow],
    *,
    first_seen: Mapping[str, str],
    default_time: str,
) -> tuple[list[ImportRecord], list[str]]:
    """Pipeline rows become gig opportunities with the history their status implies."""
    records: list[ImportRecord] = []
    skipped: list[str] = []
    for row in rows:
        match = _URL.search(row.apply or "")
        url = match.group(0) if match else ""
        listing = Listing(
            company=row.company,
            title=row.role,
            url=url,
            lane=Lane.GIG,
            provider="" if url or not row.gig_id else "gigpilot",
            provider_job_id="" if url else row.gig_id,
        )
        try:
            identity = listing.identity_key()
        except ValueError as exc:
            skipped.append(f"pipeline row {row.gig_id or '?'}: {exc}")
            continue
        discovered, estimated = to_utc(first_seen.get(row.gig_id, ""), default_time)
        decided, _ = to_utc(row.saved, discovered)
        touched, _ = to_utc(row.last_touched, decided)
        ref = f"pipeline:{row.gig_id or identity}"
        status = row.status.strip().lower()
        events = [
            EventSpec(
                K.DISCOVERED,
                discovered,
                f"{ref}:discovered",
                _clean(
                    {
                        "score": row.score or None,
                        "pay": row.pay,
                        "legacy_status": status
                        if status not in _PIPELINE_PATHS
                        else None,
                        "time_estimated": estimated or None,
                    }
                ),
            )
        ]
        for kind in _PIPELINE_PATHS.get(status, ()):
            when = decided if kind in {K.SHORTLISTED, K.SKIPPED} else touched
            events.append(
                EventSpec(kind, when, f"{ref}:{kind.value}", {"legacy_status": status})
            )
        records.append(ImportRecord(listing, discovered, tuple(events)))
    return records, skipped


# ----- writing ----------------------------------------------------------------


def write(
    ledger: Ledger,
    records: Iterable[ImportRecord],
    *,
    source: str,
    skipped: Iterable[str] = (),
) -> ImportReport:
    """Apply import records to a ledger. Safe to repeat."""
    report = ImportReport(source=source, skipped=list(skipped))
    for record in records:
        report.records += 1
        if ledger.get(opportunity_id(record.listing.identity_key())) is None:
            report.new_opportunities += 1
        opportunity = ledger.upsert(record.listing, seen_at=record.seen_at)
        present = {event.kind for event in ledger.events(opportunity.id)}
        for spec in record.events:
            if spec.only_if_absent and spec.kind in present:
                report.events_already_recorded += 1
                continue
            event = Event(
                opportunity.id,
                spec.kind,
                spec.occurred_at,
                f"import:{source}",
                spec.data,
                key=spec.key,
            )
            if ledger.append(event):
                report.events_added += 1
                present.add(spec.kind)
            else:
                report.events_already_recorded += 1
    return report
