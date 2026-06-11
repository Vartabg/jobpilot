"""Regression tests for the Lane-C scorer + psycho-fit + location gate.

All gates are policy-driven (core/policy_config.py). These tests inject a
fixture policy so they exercise the MECHANISM without depending on anyone's
personal data/policy.json — a fresh clone (neutral defaults: no kill
keywords, location gate off) must also behave as asserted below.
"""

import pytest

import jobpilot.core.policy_config as pc
import jobpilot.core.queue_builder as qb
from jobpilot.core.policy_config import policy_from_dict
from jobpilot.core.portal_scanner import PortalJob
from jobpilot.core.queue_builder import (
    _is_allowed_location,
    _score_job,
    _score_psyche_fit,
    reset_caches,
)

# A fixture policy exercising every gate: management-ladder title kills,
# moat tags, and a US-metros-plus-remote location gate.
FIXTURE_POLICY = {
    "queue": {
        "title_kill_keywords": [
            "senior manager", "principal", "staff engineer",
            "director", "vp ", "head of", "vice president",
        ],
        "moat_company_tags": {
            "happyrobot": "logistics_ai",
            "soff": "manufacturing",
            "clarion health": "healthcare_ops",
        },
        "high_moat_industries": ["logistics_ai", "manufacturing", "healthcare_ops"],
        "excluded_industries": [],
        "location_gate": {
            "enabled": True,
            "allowed_locations": [
                "new york", "nyc", "austin", "denver", "portland", "seattle",
                "san francisco", "bay area",
            ],
            "remote_terms": ["remote"],
            "country_terms": ["united states", "usa", "us"],
            "blocked_locations": [
                "london", "europe", "latam", "german speaking", "move to the us",
            ],
            "blocked_without_country": ["canada"],
        },
    },
}


@pytest.fixture
def gated_policy(monkeypatch):
    """Install the fixture policy + neutral psyche/notes caches."""
    reset_caches()
    monkeypatch.setattr(pc, "_policy_cache", policy_from_dict(FIXTURE_POLICY))
    qb._psyche_profile_cache = {"loved_signals": {}, "hated_signals": {}}
    qb._portal_notes_cache = {}
    yield
    reset_caches()


@pytest.fixture
def neutral_policy(monkeypatch):
    """Install the shipped (neutral) defaults — what a fresh clone gets."""
    reset_caches()
    monkeypatch.setattr(pc, "_policy_cache", policy_from_dict({}))
    qb._psyche_profile_cache = {"loved_signals": {}, "hated_signals": {}}
    qb._portal_notes_cache = {}
    yield
    reset_caches()


def _job(title: str, company: str, location: str = "") -> PortalJob:
    return PortalJob(company=company, title=title, url="http://x/y", location=location, portal="ashby")


def test_configured_kill_titles_are_hard_gated(gated_policy):
    """Titles matching policy title_kill_keywords → score == 5 (kill)."""
    for bad_title in (
        "Senior Manager, Solutions Engineering",
        "VP of Engineering",
        "Director of Customer Success",
        "Principal Engineer",
        "Head of Forward Deployed",
    ):
        score, _track, _psy = _score_job(_job(bad_title, "Avallon AI", "New York"))
        assert score == 5, f"{bad_title!r} should be hard-gated, got {score}"


def test_neutral_policy_kills_no_titles(neutral_policy):
    """With shipped defaults no title is killed and no location is gated."""
    score, _track, _psy = _score_job(_job("VP of Engineering", "Acme", "London"))
    assert score > 5, f"neutral policy must not hard-gate, got {score}"


def test_founding_fde_in_moat_industry_scores_high(gated_policy):
    """Founding FDE at a high-moat company should clear ~75.

    Injects a minimal psyche profile via the cache so the test does not depend
    on `data/psyche_profile.json` existing (it's user-specific and gitignored,
    so a fresh clone would not have it).
    """
    qb._psyche_profile_cache = {
        "loved_signals": {
            "title": ["founding", "deployed", "forward deployed", "fde"],
            "company_or_note": [],
            "industry": ["logistics_ai", "manufacturing"],
        },
        "hated_signals": {"title": [], "company_or_note": [], "industry": []},
    }
    score, track, psy = _score_job(_job(
        "Founding Forward Deployed Engineer", "HappyRobot", "Remote",
    ))
    assert score >= 75, f"expected ≥75 for founding FDE in high-moat, got {score}"
    assert track == "both"
    assert psy >= 12, f"psycho-fit should be high for founding/FDE titles, got {psy}"


def test_location_gate_allows_configured_metros_and_remote(gated_policy):
    """An enabled gate allows only the configured metros, remote, and country."""
    assert _is_allowed_location("Forward Deployed Engineer", "Clarion Health", "New York")
    assert _is_allowed_location("Forward Deployed Engineer", "Soff", "San Francisco")
    assert _is_allowed_location("Forward Deployed Engineer", "Acme", "Austin")
    assert _is_allowed_location("Forward Deployed Engineer", "Acme", "Denver")
    assert _is_allowed_location("Forward Deployed Engineer", "Acme", "Portland")
    assert _is_allowed_location("Forward Deployed Engineer", "Acme", "Seattle")
    assert _is_allowed_location("Forward Deployed Engineer", "Acme", "Remote US")


def test_location_gate_blocks_international_and_unknown_locations(gated_policy):
    """Blocked terms / unknown locations should not queue."""
    assert not _is_allowed_location("Forward Deployed Engineer", "Acme", "London")
    assert not _is_allowed_location("Forward Deployed Engineer", "Acme", "Remote, Europe")
    assert not _is_allowed_location("Forward Deployed Engineer", "Acme", "Not specified")
    assert _is_allowed_location("Forward Deployed Engineer", "Acme", "Remote US/Canada")
    assert not _is_allowed_location("Forward Deployed Engineer", "Acme", "Remote Canada")


def test_blocked_terms_in_title_still_disqualify(gated_policy):
    """A clearly disallowed marker in the TITLE is a conservative block —
    even when the company HQ fallback would otherwise qualify the job."""
    # HQ fallbacks: HappyRobot → San Francisco, Haast → Remote (COMPANY_HQ)
    assert not _is_allowed_location("Forward Deployed Engineer | Europe/LATAM", "HappyRobot", "")
    assert not _is_allowed_location("Forward Deployed Engineer - German Speaking", "HappyRobot", "")
    assert not _is_allowed_location("Forward Deployed Engineer - Move to the US!", "Haast", "")


def test_title_text_never_qualifies_a_location(gated_policy):
    """Regression: location matching must only consider location fields.

    Previously the job TITLE was mixed into the location haystack, so a title
    containing the word "us" ("Help us build...") passed the US gate via the
    word-anchored "us" country term, and "| NYC" in a title could stand in
    for a real location. Titles must not QUALIFY a job's location.
    """
    assert not _is_allowed_location("Help us build the future of AI", "Acme", "")
    assert not _is_allowed_location("Help us build the future of AI", "Acme", "Not specified")
    assert not _is_allowed_location("Join us — Forward Deployed Engineer", "Acme", "London")
    assert not _is_allowed_location("Forward Deployed Engineer | NYC", "Acme", "Not specified")
    # ...but the same words in the LOCATION field still qualify.
    assert _is_allowed_location("Help us build the future of AI", "Acme", "Remote (US)")
    assert _is_allowed_location("Forward Deployed Engineer", "Acme", "NYC")


def test_short_country_tokens_are_word_anchored(gated_policy):
    """'us' must match 'Austin, US' but never substrings like 'status'."""
    assert _is_allowed_location("Engineer", "Acme", "Austin, US")
    assert not _is_allowed_location("Engineer", "Acme", "Mauritius")
    assert not _is_allowed_location("Engineer", "Acme", "Status: hiring")


def test_location_gate_disabled_allows_everything(neutral_policy):
    """Gate off (the shipped default) → every location passes, even empty."""
    assert _is_allowed_location("Forward Deployed Engineer", "Acme", "London")
    assert _is_allowed_location("Forward Deployed Engineer", "Acme", "")
    assert _is_allowed_location("Forward Deployed Engineer", "UnknownCo", "Not specified")


def test_hq_fallback_allows_known_allowed_company_when_role_location_empty(gated_policy):
    """Empty role location uses the company-HQ fallback; no-HQ roles are excluded."""
    known, _, _ = _score_job(_job("Forward Deployed Engineer", "HappyRobot", ""))
    unknown, _, _ = _score_job(_job("Forward Deployed Engineer", "UnknownCo", ""))
    assert known > 0, "known Bay Area HQ should be eligible when ATS omits location"
    assert unknown == 0, "unknown location with no HQ fallback should be excluded"


def test_policy_company_hq_overrides_builtin_table(gated_policy, monkeypatch):
    """`queue.company_hq` in policy.json extends/overrides the built-in table."""
    override = dict(FIXTURE_POLICY)
    override["queue"] = dict(FIXTURE_POLICY["queue"])
    override["queue"]["company_hq"] = {"unknownco": "Denver", "happyrobot": "London"}
    monkeypatch.setattr(pc, "_policy_cache", policy_from_dict(override))
    assert _is_allowed_location("Engineer", "UnknownCo", "")
    assert not _is_allowed_location("Engineer", "HappyRobot", "")


def test_psyche_fit_responds_to_loved_signal():
    """A title carrying a loved signal scores higher than a generic title."""
    reset_caches()
    profile = {
        "loved_signals": {"title": ["founding"], "company_or_note": [], "industry": []},
        "hated_signals": {"title": [], "company_or_note": [], "industry": []},
    }
    loved = _score_psyche_fit("founding forward deployed engineer", "x", None, "", profile)
    plain = _score_psyche_fit("forward deployed engineer", "x", None, "", profile)
    assert loved > plain, f"loved ({loved}) should beat plain ({plain})"
