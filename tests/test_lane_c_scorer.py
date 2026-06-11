"""Regression tests for the Lane-C scorer + psycho-fit + location gate.

Guards the locked formula so future edits to weights / gates surface as
test failures rather than silent score drift.
"""

from jobpilot.core.portal_scanner import PortalJob
from jobpilot.core.queue_builder import (
    _is_allowed_location,
    _score_job,
    _score_psyche_fit,
    reset_caches,
)


def _job(title: str, company: str, location: str = "") -> PortalJob:
    return PortalJob(company=company, title=title, url="http://x/y", location=location, portal="ashby")


def test_senior_bureaucrat_titles_are_hard_gated():
    """Sr Manager / Director / VP / Principal / Head of → score == 5 (kill)."""
    reset_caches()
    for bad_title in (
        "Senior Manager, Solutions Engineering",
        "VP of Engineering",
        "Director of Customer Success",
        "Principal Engineer",
        "Head of Forward Deployed",
    ):
        score, _track, _psy = _score_job(_job(bad_title, "Avallon AI"))
        assert score == 5, f"{bad_title!r} should be hard-gated, got {score}"


def test_founding_fde_in_moat_industry_scores_high():
    """Founding FDE at a high-moat company should clear ~75.

    Injects a minimal psyche profile via the cache so the test does not depend
    on `data/psyche_profile.json` existing (it's user-specific and gitignored,
    so a fresh clone would not have it).
    """
    import jobpilot.core.queue_builder as qb
    reset_caches()
    qb._psyche_profile_cache = {
        "loved_signals": {
            "title": ["founding", "deployed", "forward deployed", "fde"],
            "company_or_note": [],
            "industry": ["logistics_ai", "aviation_maintenance", "manufacturing"],
        },
        "hated_signals": {"title": [], "company_or_note": [], "industry": []},
    }
    qb._portal_notes_cache = {}
    score, track, psy = _score_job(_job(
        "Founding Forward Deployed Engineer", "HappyRobot", "Remote",
    ))
    assert score >= 75, f"expected ≥75 for founding FDE in high-moat, got {score}"
    assert track == "both"
    assert psy >= 12, f"psycho-fit should be high for founding/FDE titles, got {psy}"


def test_location_gate_allows_selected_us_metros_and_remote():
    """The active search allows only the user's selected US metros plus remote."""
    reset_caches()
    assert _is_allowed_location("Forward Deployed Engineer", "Clarion Health", "New York")
    assert _is_allowed_location("Forward Deployed Engineer", "Soff", "San Francisco")
    assert _is_allowed_location("Forward Deployed Engineer", "Acme", "Austin")
    assert _is_allowed_location("Forward Deployed Engineer", "Acme", "Denver")
    assert _is_allowed_location("Forward Deployed Engineer", "Acme", "Portland")
    assert _is_allowed_location("Forward Deployed Engineer", "Acme", "Seattle")
    assert _is_allowed_location("Forward Deployed Engineer", "Acme", "Remote US")
    assert _is_allowed_location("Forward Deployed Engineer | NYC", "Acme", "Not specified")


def test_location_gate_blocks_international_nonlisted_and_unknown_locations():
    """London/Europe/nonlisted US cities/unknown locations should not queue."""
    reset_caches()
    assert not _is_allowed_location("Forward Deployed Engineer", "Acme", "London")
    assert not _is_allowed_location("Forward Deployed Engineer", "Acme", "Remote, Europe")
    assert not _is_allowed_location("Forward Deployed Engineer | Europe/LATAM", "Starbridge", "")
    assert not _is_allowed_location("Forward Deployed Engineer - German Speaking", "HappyRobot", "")
    assert not _is_allowed_location("Forward Deployed Engineer - Move to the US!", "Haast", "")
    assert not _is_allowed_location("Forward Deployed Engineer", "Acme", "Not specified")
    assert _is_allowed_location("Forward Deployed Engineer", "Acme", "Remote US/Canada")
    assert not _is_allowed_location("Forward Deployed Engineer", "Acme", "Remote Canada")


def test_hq_fallback_allows_known_allowed_company_when_role_location_empty():
    """Empty role location uses COMPANY_HQ; unknown no-HQ roles are excluded."""
    reset_caches()
    known, _, _ = _score_job(_job("Forward Deployed Engineer", "HappyRobot", ""))
    growth_unknown, _, _ = _score_job(_job("Forward Deployed Engineer", "Growth Protocol", ""))
    unknown, _, _ = _score_job(_job("Forward Deployed Engineer", "UnknownCo", ""))
    assert known > 0, "known Bay Area HQ should be eligible when ATS omits location"
    assert growth_unknown == 0, "Growth Protocol postings must carry actual role location"
    assert unknown == 0, "unknown location with no HQ fallback should be excluded"


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
