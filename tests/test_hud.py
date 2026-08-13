"""HUD render smoke tests."""

from datetime import UTC, datetime

from rich.console import Console

from jobpilot.core.queue_builder import QueueJob
from jobpilot.ui.hud import (
    _austin_countdown,
    _detail_panel,
    _jobs_table,
    _update_new_gigs,
    build_hud_layout,
    export_hud_text,
)
from jobpilot.ui.hud_state import HudState
from jobpilot.ui.income_data import IncomeViewOptions, gig_pay_label


def _ready_job(**overrides) -> QueueJob:
    values = {
        "id": "ready",
        "company": "Civitech",
        "title": "Analytics Engineer",
        "url": "https://jobs.lever.co/x",
        "location": "Austin, TX or Remote",
        "portal": "lever",
        "track": "tech",
        "fit_score": 46,
        "keywords": [],
        "status": "queued",
        "decision": "apply_now",
        "assessment_status": "assessed",
        "qualification_lower_bound": 78,
        "work_context_match": 82,
        "evidence_coverage": 75,
        "biggest_gap": "AWS depth",
        "matched_accounts": ["customer-field"],
        "legitimacy_state": "recommend",
        "evidence_grade": "A",
        "verified_at": datetime.now(UTC).isoformat(),
        "psyche_score": 15,
    }
    values.update(overrides)
    return QueueJob(**values)


def _render_text(renderable, *, width: int = 220) -> str:
    console = Console(width=width, record=True)
    console.print(renderable)
    return console.export_text()


def test_gig_pay_label_hourly():
    from jobpilot.gigs.core.models import Gig

    g = Gig(id="x", source="hn", title="T", url="u", pay_hourly_est=45)
    assert gig_pay_label(g) == "$45/hr"


def test_export_hud_text_with_mocks(monkeypatch):
    from jobpilot.gigs.core.models import Gig

    gig = Gig(
        id="hn-1",
        source="hn",
        title="Founding Engineer",
        url="https://example.com",
        company="Acme",
        fit_score=90,
        apply_url="mailto:a@b.com",
    )
    job = _ready_job(id="abc")
    from jobpilot.ui.hud_state import HudData

    monkeypatch.setattr(
        "jobpilot.ui.hud.load_hud_data",
        lambda opts, **kw: HudData(
            gigs=[gig],
            jobs=[job],
            gigs_meta={"shown": 1, "collected": 1},
            pipe_rows=[],
            pipe_counts={},
        ),
    )

    text = export_hud_text(IncomeViewOptions())
    assert "Acme" in text
    assert "Civitech" in text
    assert "mailto:a@b.com" in text
    assert "decision=apply now" in text
    assert "qualification_floor=78/100" in text
    assert "coverage=75%" in text
    assert "posting_evidence=A/recommend" in text
    assert "Biggest gap: AWS depth" in text
    assert "psyche" not in text.lower()


def test_hud_job_row_shows_concise_evidence_without_psyche_text():
    text = _render_text(_jobs_table([_ready_job()], HudState(lane="job")))

    assert "Dec" in text
    assert "Q" in text
    assert "Cov" in text
    assert "Post" in text
    assert "apply now" in text
    assert "78" in text
    assert "75%" in text
    assert "A/recommend" in text
    assert "gap: AWS depth" in text
    assert "psyche" not in text.lower()
    assert "culture" not in text.lower()


def test_hud_job_detail_shows_evidence_axes_and_biggest_gap(monkeypatch):
    monkeypatch.setattr("jobpilot.ui.hud.materials_ready", lambda _company: False)

    text = _render_text(_detail_panel([], [_ready_job()], HudState(lane="job")))

    assert "Decision apply now" in text
    assert "qualification floor 78/100" in text
    assert "coverage 75%" in text
    assert "Posting evidence A/recommend" in text
    assert "context 82/100" in text
    assert "Biggest gap: AWS depth" in text
    assert "psyche" not in text.lower()


def test_hud_job_detail_uses_matched_account_when_no_gap(monkeypatch):
    monkeypatch.setattr("jobpilot.ui.hud.materials_ready", lambda _company: False)

    text = _render_text(
        _detail_panel(
            [],
            [_ready_job(biggest_gap="", matched_accounts=["customer-field"])],
            HudState(lane="job"),
        )
    )

    assert "Matched account: customer-field" in text


def test_build_hud_layout_renders(monkeypatch):
    from jobpilot.ui.hud_state import HudData

    monkeypatch.setattr(
        "jobpilot.ui.hud.load_hud_data",
        lambda opts, **kw: HudData(
            gigs=[],
            jobs=[],
            gigs_meta={"shown": 0, "collected": 0, "fresh_count": 0},
            pipe_rows=[],
            pipe_counts={},
        ),
    )
    monkeypatch.setattr(
        "jobpilot.ui.hud.get_profile_store",
        lambda: type(
            "S",
            (),
            {
                "load": lambda self: type(
                    "P", (), {"first_name": "G", "last_name": "V"}
                )()
            },
        )(),
    )
    monkeypatch.setattr("jobpilot.ui.hud.check_dashboard", lambda _p: False)
    monkeypatch.setattr("jobpilot.ui.hud.check_chrome", lambda _p=9222: False)
    monkeypatch.setattr(
        "jobpilot.ui.hud.get_application_tracker",
        lambda: type(
            "T", (), {"get_stats": lambda self: {}, "close": lambda self: None}
        )(),
    )

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


def test_build_hud_layout_plain_mode(monkeypatch):
    from jobpilot.gigs.core.models import Gig

    gig = Gig(
        id="g1",
        source="hn",
        title="Engineer",
        url="https://x.com",
        company="Acme",
        fit_score=80,
        fit_reasons=["+10 async"],
    )
    monkeypatch.setattr(
        "jobpilot.ui.hud.load_hud_data",
        lambda opts, **kw: __import__(
            "jobpilot.ui.hud_state", fromlist=["HudData"]
        ).HudData(
            gigs=[gig],
            jobs=[],
            gigs_meta={"shown": 1, "collected": 1, "fresh_count": 1},
            pipe_rows=[],
            pipe_counts={},
        ),
    )
    monkeypatch.setattr(
        "jobpilot.ui.hud.get_profile_store",
        lambda: type(
            "S",
            (),
            {
                "load": lambda self: type(
                    "P", (), {"first_name": "G", "last_name": "V"}
                )()
            },
        )(),
    )
    monkeypatch.setattr("jobpilot.ui.hud.check_dashboard", lambda _p: True)
    monkeypatch.setattr("jobpilot.ui.hud.check_chrome", lambda _p=9222: True)
    monkeypatch.setattr(
        "jobpilot.ui.hud.get_application_tracker",
        lambda: type(
            "T",
            (),
            {
                "get_stats": lambda self: {"total": 1, "submitted": 0},
                "close": lambda self: None,
            },
        )(),
    )

    layout = build_hud_layout(IncomeViewOptions(), plain=True)
    assert layout.name == "root"


def test_build_hud_layout_interactive_state(monkeypatch):
    from jobpilot.gigs.core.models import Gig

    gig = Gig(
        id="g1",
        source="hn",
        title="Role",
        url="https://x.com",
        company="Co",
        fit_score=70,
        fit_reasons=["+10 async"],
    )
    monkeypatch.setattr(
        "jobpilot.ui.hud.load_hud_data",
        lambda opts, **kw: __import__(
            "jobpilot.ui.hud_state", fromlist=["HudData"]
        ).HudData(
            gigs=[gig],
            jobs=[],
            gigs_meta={"shown": 1, "collected": 1, "fresh_count": 1},
            pipe_rows=[],
            pipe_counts={},
        ),
    )
    monkeypatch.setattr(
        "jobpilot.ui.hud.get_profile_store",
        lambda: type(
            "S",
            (),
            {
                "load": lambda self: type(
                    "P", (), {"first_name": "G", "last_name": "V"}
                )()
            },
        )(),
    )
    monkeypatch.setattr("jobpilot.ui.hud.check_dashboard", lambda _p: False)
    monkeypatch.setattr("jobpilot.ui.hud.check_chrome", lambda _p=9222: False)
    monkeypatch.setattr(
        "jobpilot.ui.hud.get_application_tracker",
        lambda: type(
            "T", (), {"get_stats": lambda self: {}, "close": lambda self: None}
        )(),
    )

    state = HudState(lane="gig", gig_index=0)
    layout = build_hud_layout(IncomeViewOptions(), state=state, interactive=True)
    assert layout.name == "root"
