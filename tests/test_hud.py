"""HUD render smoke tests."""

from jobpilot.ui.hud import (
    _austin_countdown,
    _update_new_gigs,
    build_hud_layout,
    export_hud_text,
)
from jobpilot.ui.hud_state import HudState
from jobpilot.ui.income_data import IncomeViewOptions, gig_badges, gig_pay_label


def test_gig_pay_label_hourly():
    from jobpilot.gigs.core.models import Gig
    g = Gig(id="x", source="hn", title="T", url="u", pay_hourly_est=45)
    assert gig_pay_label(g) == "$45/hr"


def test_export_hud_text_with_mocks(monkeypatch):
    from jobpilot.core.queue_builder import QueueJob
    from jobpilot.gigs.core.models import Gig

    gig = Gig(
        id="hn-1", source="hn", title="Founding Engineer", url="https://example.com",
        company="Acme", fit_score=90, apply_url="mailto:a@b.com",
    )
    job = QueueJob(
        id="abc", company="Civitech", title="Analytics Engineer",
        url="https://jobs.lever.co/x", location="Austin, TX or Remote",
        portal="lever", track="tech", fit_score=46, keywords=[], status="queued",
    )
    from jobpilot.ui.hud_state import HudData

    monkeypatch.setattr(
        "jobpilot.ui.hud.load_hud_data",
        lambda opts, **kw: HudData(
            gigs=[gig], jobs=[job], gigs_meta={"shown": 1, "collected": 1},
            pipe_rows=[], pipe_counts={},
        ),
    )

    text = export_hud_text(IncomeViewOptions())
    assert "Acme" in text
    assert "Civitech" in text
    assert "mailto:a@b.com" in text


def test_build_hud_layout_renders(monkeypatch):
    from jobpilot.ui.hud_state import HudData

    monkeypatch.setattr(
        "jobpilot.ui.hud.load_hud_data",
        lambda opts, **kw: HudData(
            gigs=[], jobs=[], gigs_meta={"shown": 0, "collected": 0, "fresh_count": 0},
            pipe_rows=[], pipe_counts={},
        ),
    )
    monkeypatch.setattr("jobpilot.ui.hud.get_profile_store", lambda: type("S", (), {"load": lambda self: type("P", (), {"first_name": "G", "last_name": "V"})()})())
    monkeypatch.setattr("jobpilot.ui.hud.check_dashboard", lambda _p: False)
    monkeypatch.setattr("jobpilot.ui.hud.check_chrome", lambda _p=9222: False)
    monkeypatch.setattr("jobpilot.ui.hud.get_application_tracker", lambda: type("T", (), {"get_stats": lambda self: {}, "close": lambda self: None})())

    layout = build_hud_layout(IncomeViewOptions())
    assert layout.name == "root"


def test_austin_countdown_future():
    assert "d to Austin" in _austin_countdown() or "Austin" in _austin_countdown()


def test_new_gig_tracking():
    from jobpilot.gigs.core.models import Gig

    state = HudState()
    g1 = Gig(id="a", source="hn", title="T", url="u")
    g2 = Gig(id="b", source="hn", title="T2", url="u2")
    _update_new_gigs(state, [g1])
    assert state.new_gig_ids == set()
    _update_new_gigs(state, [g1, g2])
    assert state.new_gig_ids == {"b"}


def test_build_hud_layout_interactive_state(monkeypatch):
    from jobpilot.gigs.core.models import Gig

    gig = Gig(id="g1", source="hn", title="Role", url="https://x.com", company="Co", fit_score=70, fit_reasons=["+10 async"])
    monkeypatch.setattr("jobpilot.ui.hud.load_hud_data", lambda opts, **kw: __import__("jobpilot.ui.hud_state", fromlist=["HudData"]).HudData(
        gigs=[gig], jobs=[], gigs_meta={"shown": 1, "collected": 1, "fresh_count": 1}, pipe_rows=[], pipe_counts={},
    ))
    monkeypatch.setattr("jobpilot.ui.hud.get_profile_store", lambda: type("S", (), {"load": lambda self: type("P", (), {"first_name": "G", "last_name": "V"})()})())
    monkeypatch.setattr("jobpilot.ui.hud.check_dashboard", lambda _p: False)
    monkeypatch.setattr("jobpilot.ui.hud.check_chrome", lambda _p=9222: False)
    monkeypatch.setattr("jobpilot.ui.hud.get_application_tracker", lambda: type("T", (), {"get_stats": lambda self: {}, "close": lambda self: None})())

    state = HudState(lane="gig", gig_index=0)
    layout = build_hud_layout(IncomeViewOptions(), state=state, interactive=True)
    assert layout.name == "root"