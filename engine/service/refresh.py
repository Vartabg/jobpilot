"""Refresh: read every board, record what changed, and screen each posting.

For each board that answers:
- a role seen for the first time gets a ``discovered`` event, and a closed
  role that reappears is reopened;
- the posting's full text is stored and screened against the user's rules,
  and the screening is recorded unless the same inputs were already screened;
- a role that this board listed before but no longer does gets a ``closed``
  event, if nothing has happened past the decision stage.

A board that fails to answer changes nothing, so an outage never closes roles.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field

from jobpilot.engine.domain import (
    SCREEN,
    SCREEN_VERSION,
    Assessment,
    BoardTarget,
    Event,
    EventKind,
    Lane,
    Settings,
    Status,
    opportunity_id,
    rules_fingerprint,
    screen,
)
from jobpilot.engine.ports import Boards, Ledger

_CLOSABLE = frozenset({Status.NEW, Status.SHORTLISTED, Status.SKIPPED, Status.READY})


@dataclass
class RefreshReport:
    boards: int = 0
    failed_boards: list[str] = field(default_factory=list)
    postings: int = 0
    new_roles: int = 0
    reopened: int = 0
    closed: int = 0
    passed: int = 0
    failed: int = 0
    failures_by_rule: Counter[str] = field(default_factory=Counter)
    unknowns_by_rule: Counter[str] = field(default_factory=Counter)


def refresh(
    ledger: Ledger,
    boards: Boards,
    targets: Iterable[BoardTarget],
    settings: Settings,
    *,
    now: str,
) -> RefreshReport:
    report = RefreshReport()
    fingerprint = rules_fingerprint(settings)
    for target in targets:
        report.boards += 1
        result = boards.fetch(target)
        if result.error:
            report.failed_boards.append(f"{target.name}: {result.error}")
            continue
        seen: set[str] = set()
        for posting in result.postings:
            report.postings += 1
            is_new = ledger.get(opportunity_id(posting.listing.identity_key())) is None
            opportunity = ledger.upsert(posting.listing, seen_at=now)
            seen.add(opportunity.id)
            if is_new:
                ledger.append(
                    Event(
                        opportunity.id,
                        EventKind.DISCOVERED,
                        now,
                        "scan",
                        key=f"scan:{opportunity.id}:discovered",
                    )
                )
                report.new_roles += 1
            elif ledger.status(opportunity.id) is Status.CLOSED:
                ledger.append(
                    Event(
                        opportunity.id,
                        EventKind.DISCOVERED,
                        now,
                        "scan",
                        {"reopened": True},
                        key=f"scan:{opportunity.id}:reopened:{now}",
                    )
                )
                report.reopened += 1
            ledger.save_posting(opportunity.id, posting, fetched_at=now)
            screening = screen(posting, settings)
            ledger.record_assessment(
                Assessment(
                    opportunity.id,
                    SCREEN,
                    SCREEN_VERSION,
                    now,
                    screening.to_dict(),
                    basis=f"{posting.content_hash}:{fingerprint}",
                )
            )
            if screening.passed:
                report.passed += 1
            else:
                report.failed += 1
            report.failures_by_rule.update(check.rule for check in screening.failures)
            report.unknowns_by_rule.update(check.rule for check in screening.unknowns)
        report.closed += _close_missing(ledger, target, seen, now)
    return report


def _close_missing(
    ledger: Ledger, target: BoardTarget, seen: set[str], now: str
) -> int:
    closed = 0
    tenant = target.token.lower()
    for opportunity in ledger.opportunities(Lane.JOB):
        if (
            opportunity.provider != target.provider
            or opportunity.tenant != tenant
            or opportunity.id in seen
        ):
            continue
        if ledger.status(opportunity.id) in _CLOSABLE:
            ledger.append(
                Event(
                    opportunity.id,
                    EventKind.CLOSED,
                    now,
                    "scan",
                    {"board": target.name},
                    key=f"scan:{opportunity.id}:closed:{now}",
                )
            )
            closed += 1
    return closed
