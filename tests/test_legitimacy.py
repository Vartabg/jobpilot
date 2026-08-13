"""Deterministic legitimacy gating from source evidence."""

from datetime import UTC, datetime, timedelta

import pytest

from jobpilot.core.legitimacy import assess_legitimacy
from jobpilot.core.opportunity_models import SourceObservation, SourceRunResult

NOW = datetime(2026, 8, 13, 18, 0, tzinfo=UTC)


def _observation(**overrides: object) -> SourceObservation:
    values: dict[str, object] = {
        "provider": "greenhouse",
        "tenant": "acme",
        "provider_job_id": "123",
        "canonical_url": "https://boards.greenhouse.io/acme/jobs/123",
        "fetched_at": NOW.isoformat(),
        "listing_state": "listed",
        "description": "Work directly with customers to deploy reliable systems.",
    }
    values.update(overrides)
    return SourceObservation(**values)  # type: ignore[arg-type]


def test_current_direct_ats_proof_is_recommended() -> None:
    result = assess_legitimacy([_observation()], now=NOW)

    assert result.grade == "A"
    assert result.state == "recommend"
    assert "current_direct_listing" in result.reasons
    assert result.verified_at == NOW.isoformat()


def test_aggregator_timestamp_cannot_extend_direct_verification() -> None:
    direct_time = NOW - timedelta(hours=23)
    result = assess_legitimacy(
        [
            _observation(fetched_at=direct_time.isoformat()),
            _observation(
                provider="indeed",
                tenant="",
                source_kind="aggregator",
                canonical_url="https://www.indeed.com/viewjob?jk=123",
                fetched_at=NOW.isoformat(),
            ),
        ],
        now=NOW,
    )

    assert result.state == "recommend"
    assert result.verified_at == direct_time.isoformat()


def test_current_direct_listing_with_incomplete_details_is_grade_b() -> None:
    result = assess_legitimacy([_observation(description="")], now=NOW)

    assert (result.grade, result.state) == ("B", "recommend")


@pytest.mark.parametrize("provider", ["indeed", "adzuna", "recruiter"])
def test_unresolved_aggregator_or_recruiter_requires_review(provider: str) -> None:
    result = assess_legitimacy([_observation(
        provider=provider, tenant="", source_kind="", canonical_url="https://example.test/lead"
    )], now=NOW)

    assert result.state == "review"
    assert result.grade in {"C", "D"}


def test_direct_proof_older_than_24_hours_is_held() -> None:
    fetched = (NOW - timedelta(hours=24, seconds=1)).isoformat()

    result = assess_legitimacy([_observation(fetched_at=fetched)], now=NOW)

    assert (result.grade, result.state) == ("D", "hold")
    assert "stale_verification" in result.reasons


def test_unknown_verification_date_is_held() -> None:
    result = assess_legitimacy([_observation(fetched_at="")], now=NOW)

    assert result.state == "hold"
    assert "unknown_verification_time" in result.reasons


@pytest.mark.parametrize("listing_state", ["closed", "expired", "404"])
def test_closed_or_expired_listing_is_blocked(listing_state: str) -> None:
    result = assess_legitimacy([_observation(listing_state=listing_state)], now=NOW)

    assert (result.grade, result.state) == ("F", "block")


def test_past_expiry_is_blocked() -> None:
    result = assess_legitimacy([
        _observation(expires_at=(NOW - timedelta(seconds=1)).isoformat())
    ], now=NOW)

    assert result.state == "block"
    assert "past_expiry" in result.reasons


def test_conflicting_direct_listing_states_are_blocked() -> None:
    result = assess_legitimacy([
        _observation(provider_job_id="123", listing_state="listed"),
        _observation(provider_job_id="123", listing_state="closed"),
    ], now=NOW)

    assert result.state == "block"
    assert "conflicting_listing_state" in result.reasons


@pytest.mark.parametrize("phrase", [
    "Pay an application fee before your interview.",
    "Deposit the check we send and forward the balance.",
    "Transfer Bitcoin to the recruiter wallet.",
    "Purchase equipment from our approved vendor before starting.",
])
def test_known_job_scam_payment_patterns_are_blocked(phrase: str) -> None:
    result = assess_legitimacy([_observation(description=phrase)], now=NOW)

    assert result.state == "block"
    assert "scam_payment_pattern" in result.reasons


def test_source_outage_without_current_observation_is_held() -> None:
    run = SourceRunResult(
        provider="greenhouse", target="acme", status="failure",
        fetched_at=NOW.isoformat(), error_type="HTTPError",
    )

    result = assess_legitimacy([], source_runs=[run], now=NOW)

    assert (result.grade, result.state) == ("E", "hold")
    assert "source_failure" in result.reasons
