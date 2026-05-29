"""Regression tests for the Lane-C scorer + psycho-fit + HQ fallback.

Guards the locked formula so future edits to weights / gates surface as
test failures rather than silent score drift.
"""

from jobpilot.core.portal_scanner import PortalJob
from jobpilot.core.queue_builder import _score_job, _score_psyche_fit, reset_caches


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


def test_remote_role_beats_kill_location():
    """Same role, NYC/Remote vs SF — NYC should outscore SF by the loc dim."""
    reset_caches()
    nyc, _, _ = _score_job(_job("Forward Deployed Engineer", "Clarion Health", "New York"))
    sf,  _, _ = _score_job(_job("Forward Deployed Engineer", "Soff", "San Francisco"))
    assert nyc > sf, f"NYC ({nyc}) should outscore SF ({sf})"


def test_hq_fallback_bites_when_role_location_empty():
    """Empty role location → HQ lookup → SF kill still fires (HappyRobot HQ = SF)."""
    reset_caches()
    empty, _, _ = _score_job(_job("Forward Deployed Engineer", "HappyRobot", ""))
    given, _, _ = _score_job(_job("Forward Deployed Engineer", "HappyRobot", "Remote"))
    assert given > empty, (
        f"explicit Remote ({given}) should beat empty/HQ-SF ({empty}); "
        "COMPANY_HQ fallback may have broken"
    )


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
