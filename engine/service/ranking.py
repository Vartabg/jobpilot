"""Rank: order every open role that passed screening by its recorded fit.

Ranking is a read, not a judgment: it records nothing and judges nothing.
Roles are taken from the ledger with their latest screening and fit
assessments, scored by the pure rules in the domain, and returned best
first. Ties go to the most recently seen role, then company and title, so
the order is stable between runs. Roles that failed a hard rule, are
closed, or already have an application in flight are never ranked.
"""

from __future__ import annotations

from jobpilot.engine.domain import SCREEN, Lane, Screening, Status
from jobpilot.engine.domain.fit import FIT, Fit
from jobpilot.engine.domain.ranking import Ranked, build_ranked
from jobpilot.engine.ports import Ledger

ACTIONABLE = frozenset({Status.NEW, Status.SHORTLISTED, Status.READY})


def rank_opportunities(
    ledger: Ledger,
    *,
    lane: Lane = Lane.JOB,
    limit: int | None = None,
) -> list[Ranked]:
    """Open roles that passed screening, best fit first."""
    ranked: list[Ranked] = []
    for opportunity in ledger.opportunities(lane):
        if ledger.status(opportunity.id) not in ACTIONABLE:
            continue
        screening_assessment = ledger.latest_assessment(opportunity.id, SCREEN)
        if screening_assessment is None:
            continue
        screening = Screening.from_dict(screening_assessment.result)
        if not screening.passed:
            continue
        fit_assessment = ledger.latest_assessment(opportunity.id, FIT)
        fit = (
            Fit.from_dict(fit_assessment.result)
            if fit_assessment is not None
            else None
        )
        ranked.append(build_ranked(opportunity, screening, fit))
    # Stable sorts, least significant first. Judged beats unjudged (even a
    # judged off-target role), then score, then recency, then company/title.
    ranked.sort(key=lambda row: (row.opportunity.company.lower(), row.opportunity.title.lower()))
    ranked.sort(key=lambda row: row.opportunity.last_seen, reverse=True)
    ranked.sort(key=lambda row: row.score, reverse=True)
    ranked.sort(key=lambda row: row.judged, reverse=True)
    return ranked[:limit] if limit is not None else ranked
