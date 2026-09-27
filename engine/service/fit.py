"""Assess fit: judge every open role that passed screening.

Roles are taken most relevant first, so a limited run spends its time where it
matters. Each judgment is recorded as a ``fit`` assessment keyed by its inputs:
the posting text, the evidence, the target titles, and the checker. A role is
only judged again when one of those changes.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field

from jobpilot.engine.domain import (
    SCREEN,
    Assessment,
    Lane,
    Screening,
    Settings,
    Status,
)
from jobpilot.engine.domain.fit import (
    FIT,
    FIT_VERSION,
    ModelError,
    evidence_fingerprint,
    evidence_from,
    title_relevance,
)
from jobpilot.engine.ports import FitChecker, Ledger

ACTIONABLE = frozenset({Status.NEW, Status.SHORTLISTED, Status.READY})


@dataclass
class FitReport:
    candidates: int = 0
    assessed: int = 0
    up_to_date: int = 0
    deferred: int = 0
    failed: list[str] = field(default_factory=list)
    by_tier: Counter[str] = field(default_factory=Counter)


def _targets_fingerprint(settings: Settings) -> str:
    return hashlib.sha256(
        json.dumps(list(settings.targets.titles)).encode()
    ).hexdigest()[:16]


def assess_fits(
    ledger: Ledger,
    checker: FitChecker,
    settings: Settings,
    *,
    now: str,
    limit: int | None = None,
) -> FitReport:
    report = FitReport()
    inputs = f"{evidence_fingerprint(evidence_from(settings))}:{_targets_fingerprint(settings)}"
    candidates = []
    for opportunity in ledger.opportunities(Lane.JOB):
        if ledger.status(opportunity.id) not in ACTIONABLE:
            continue
        screening = ledger.latest_assessment(opportunity.id, SCREEN)
        posting = ledger.posting(opportunity.id)
        if (
            screening is None
            or posting is None
            or not Screening.from_dict(screening.result).passed
        ):
            continue
        relevance = title_relevance(opportunity.title, settings.targets.titles)
        candidates.append((relevance, opportunity, posting))
    candidates.sort(
        key=lambda row: (-row[0], row[1].company.lower(), row[1].title.lower())
    )

    for _, opportunity, posting in candidates:
        report.candidates += 1
        basis = f"{posting.content_hash}:{inputs}:{checker.name}:{FIT_VERSION}"
        latest = ledger.latest_assessment(opportunity.id, FIT)
        if latest is not None and latest.basis == basis:
            report.up_to_date += 1
            report.by_tier[str(latest.result.get("tier", ""))] += 1
            continue
        if limit is not None and report.assessed >= limit:
            report.deferred += 1
            continue
        try:
            fit = checker.assess(posting, settings)
        except ModelError as exc:
            report.failed.append(f"{opportunity.company} — {opportunity.title}: {exc}")
            continue
        ledger.record_assessment(
            Assessment(opportunity.id, FIT, FIT_VERSION, now, fit.to_dict(), basis)
        )
        report.assessed += 1
        report.by_tier[fit.tier.value] += 1
    return report
