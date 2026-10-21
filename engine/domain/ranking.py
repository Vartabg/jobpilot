"""Ranking: order the roles that passed screening by how well they fit.

The score is decided here, in pure code, from the recorded fit judgment and
nothing else, so the order can always be explained in plain words:

- the fit tier carries most of the weight (strong 60, possible 40,
  stretch 20, off target 0);
- coverage of the must-have requirements adds up to 25;
- how closely the title matches a target title adds up to 15.

Screening unknowns never lower the score. An unsure check is a reason to
look, not a reason to bury a role, so unknowns are reported as notes.
A role nobody has judged yet scores 0 and sorts below every judged role:
ranking never invents a judgment.
"""

from __future__ import annotations

from dataclasses import dataclass

from jobpilot.engine.domain.fit import Fit, Tier
from jobpilot.engine.domain.opportunity import Opportunity
from jobpilot.engine.domain.screening import Screening

RANK_VERSION = "rank/1"

_TIER_POINTS = {
    Tier.STRONG: 60.0,
    Tier.POSSIBLE: 40.0,
    Tier.STRETCH: 20.0,
    Tier.OFF_TARGET: 0.0,
}
COVERAGE_POINTS = 25.0
RELEVANCE_POINTS = 15.0


@dataclass(frozen=True)
class Ranked:
    """One role that passed screening, with its rank score and why."""

    opportunity: Opportunity
    score: float
    fit: Fit | None
    screening: Screening
    reasons: tuple[str, ...] = ()

    @property
    def judged(self) -> bool:
        return self.fit is not None


def score_fit(fit: Fit) -> float:
    """The 0-100 rank score for one recorded fit judgment."""
    points = (
        _TIER_POINTS[fit.tier]
        + fit.coverage * COVERAGE_POINTS
        + fit.title_relevance * RELEVANCE_POINTS
    )
    return round(points, 1)


def _reasons(fit: Fit | None, screening: Screening) -> tuple[str, ...]:
    if fit is None:
        reasons = ["Not judged yet"]
    else:
        reasons = [
            f"{fit.tier.value.replace('_', ' ').title()} fit",
            f"covers {fit.coverage:.0%} of the must-haves",
            f"title match {fit.title_relevance:.0%}",
        ]
        if fit.biggest_gap:
            reasons.append(f"biggest gap: {fit.biggest_gap}")
    unsure = len(screening.unknowns)
    if unsure:
        reasons.append(f"{unsure} rule check{'s' if unsure > 1 else ''} unsure — worth a look")
    return tuple(reasons)


def build_ranked(
    opportunity: Opportunity,
    screening: Screening,
    fit: Fit | None,
) -> Ranked:
    """Score one screened role. Unjudged roles score 0."""
    return Ranked(
        opportunity=opportunity,
        score=score_fit(fit) if fit is not None else 0.0,
        fit=fit,
        screening=screening,
        reasons=_reasons(fit, screening),
    )
