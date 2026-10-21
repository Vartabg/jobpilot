from jobpilot.engine.domain import Listing, Opportunity, Screening
from jobpilot.engine.domain.fit import Fit, Tier
from jobpilot.engine.domain.opportunity import opportunity_id
from jobpilot.engine.domain.ranking import build_ranked, score_fit
from jobpilot.engine.domain.screening import Check, Outcome


def fit(tier, coverage=0.0, relevance=0.0, gap=""):
    return Fit(
        tier=tier,
        title_relevance=relevance,
        coverage=coverage,
        matches=(),
        biggest_gap=gap,
        engine="rules",
    )


def ranked(fit_value, screening=None):
    listing = Listing("Acme", "Solutions Engineer", url="https://example.com/1")
    opportunity = Opportunity(
        id=opportunity_id(listing.identity_key()),
        identity_key=listing.identity_key(),
        lane=listing.lane,
        company=listing.company,
        title=listing.title,
        url=listing.url,
        location="",
        provider="",
        tenant="",
        provider_job_id="",
        first_seen="2026-09-27T12:00:00+00:00",
        last_seen="2026-09-27T12:00:00+00:00",
    )
    return build_ranked(opportunity, screening or Screening(()), fit_value)


def test_tier_carries_the_most_weight():
    assert score_fit(fit(Tier.STRONG)) > score_fit(fit(Tier.POSSIBLE))
    assert score_fit(fit(Tier.POSSIBLE)) > score_fit(fit(Tier.STRETCH))
    assert score_fit(fit(Tier.STRETCH)) > score_fit(fit(Tier.OFF_TARGET))


def test_coverage_then_relevance_break_tier_ties():
    full = score_fit(fit(Tier.POSSIBLE, coverage=1.0, relevance=1.0))
    bare = score_fit(fit(Tier.POSSIBLE, coverage=0.0, relevance=0.0))
    assert full - bare == 40.0  # 25 coverage + 15 relevance


def test_score_stays_within_0_and_100():
    assert score_fit(fit(Tier.STRONG, coverage=1.0, relevance=1.0)) == 100.0
    assert score_fit(fit(Tier.OFF_TARGET)) == 0.0


def test_unjudged_roles_score_zero_with_a_plain_reason():
    row = ranked(None)
    assert row.score == 0.0 and not row.judged
    assert row.reasons[0] == "Not judged yet"


def test_unsure_checks_never_lower_the_score():
    unsure = Screening((Check("pay", Outcome.UNKNOWN, "No pay listed"),))
    assert ranked(fit(Tier.STRONG, 0.5, 0.5), unsure).score == ranked(
        fit(Tier.STRONG, 0.5, 0.5)
    ).score
    assert "unsure" in ranked(fit(Tier.STRONG), unsure).reasons[-1]


def test_reasons_name_the_biggest_gap():
    row = ranked(fit(Tier.POSSIBLE, gap="Kubernetes experience"))
    assert any("Kubernetes" in reason for reason in row.reasons)
