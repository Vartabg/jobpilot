"""Tests for the PNG dashboard renderer + ascii fallback."""

from jobpilot.ui.dashboard_image import (
    DashboardData,
    ascii_dashboard,
    collect_dashboard_data,
    render_dashboard_png,
)

_PNG_SIG = b"\x89PNG\r\n\x1a\n"


def _sample() -> DashboardData:
    return DashboardData(
        name="Sam",
        generated_at="Fri Jun 19  17:00",
        gig_stages=[("new", 110), ("saved", 12), ("sent", 3)],
        job_stages=[("applied", 31), ("interview", 1), ("rejected", 30)],
        sources=[("RemoteOK", True, 100), ("HN Who's Hiring", False, 0)],
        income={"active": 4, "drafted": 2, "sent": 1, "potential_week": 1800},
        hot=[("Acme — AI Engineer", 100), ("Globex — RAG Lead", 88)],
    )


def test_render_produces_valid_png():
    png = render_dashboard_png(_sample())
    assert png[:8] == _PNG_SIG
    assert len(png) > 1000


def test_render_handles_empty_data():
    png = render_dashboard_png(DashboardData(generated_at="now"))
    assert png[:8] == _PNG_SIG


def test_render_scale_one_is_smaller_canvas():
    # scale=1 must still produce a valid PNG (used by tests / low-res paths)
    png = render_dashboard_png(_sample(), scale=1)
    assert png[:8] == _PNG_SIG


def test_ascii_dashboard_has_all_sections():
    txt = ascii_dashboard(_sample())
    for section in ("Gigs pipeline", "Job applications", "Source health",
                    "Income velocity", "What's hot"):
        assert section in txt
    assert "Acme — AI Engineer" in txt
    assert "✗ HN Who's Hiring" in txt  # failed source marked


def test_ascii_dashboard_empty_is_safe():
    txt = ascii_dashboard(DashboardData(generated_at="now"))
    assert "(none)" in txt
    assert "(nothing fresh)" in txt


def test_collect_dashboard_data_is_resilient():
    # Reads real local state; must never raise and must return the dataclass.
    data = collect_dashboard_data()
    assert isinstance(data, DashboardData)
    assert isinstance(data.gig_stages, list)
    assert isinstance(data.income, dict)
