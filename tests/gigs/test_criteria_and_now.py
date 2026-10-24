"""On-demand criteria report + independent push and crib controls."""

import pytest
from typer.testing import CliRunner

from jobpilot.gigs.core import preferences
from jobpilot.gigs.core.dispatcher import dispatch
from jobpilot.gigs.core.models import Gig


def test_format_criteria_report_includes_pay_and_resumes(monkeypatch) -> None:
    prefs = dict(preferences.DEFAULTS)
    prefs["pay"] = {
        "floor_annual_usd": 115000,
        "floor_hourly_usd": 55,
        "target_annual_usd": 145000,
        "target_hourly_usd": 75,
        "anchor_within_band_pct": 80,
    }
    prefs["location"] = {
        "home_metro_tags": ["austin"],
        "require_home_or_remote": True,
        "allow_remote": True,
    }
    prefs["resumes"] = {
        "default": "garo_solutions_v1.pdf",
        "AI workflow audit": "garo_ai_v1.pdf",
    }
    prefs["search"] = dict(preferences.DEFAULTS["search"])
    prefs["search"]["min_score"] = 60
    monkeypatch.setattr(preferences, "load", lambda path=None: prefs)

    text = preferences.format_criteria_report()
    assert "not a hard filter" in text
    assert "garo_solutions_v1.pdf" in text
    assert "min_score=60" in text
    assert "austin" in text.lower()


def test_search_config_merges_defaults() -> None:
    cfg = preferences.search_config(preferences.DEFAULTS)
    assert cfg["min_score"] == 60
    assert cfg["push_default"] is False
    assert "Forward Deployed Engineer" in cfg["target_titles"]


def test_dispatch_skips_ntfy_when_push_false(monkeypatch, tmp_path) -> None:
    calls: list[bool] = []

    def _fake_write(gigs, *, source_warning="", followups=None):
        p = tmp_path / "d.md"
        p.write_text("x")
        return p

    def _fake_crib(gigs, crib_dir=None):
        p = tmp_path / "c.md"
        p.write_text("c")
        return p

    def _fake_push(*a, **k):
        calls.append(True)
        return True

    monkeypatch.setattr("jobpilot.gigs.core.dispatcher.write_markdown", _fake_write)
    monkeypatch.setattr("jobpilot.gigs.core.dispatcher.write_crib_sheet", _fake_crib)
    monkeypatch.setattr(
        "jobpilot.gigs.core.dispatcher.save_latest_leads", lambda g: None
    )
    monkeypatch.setattr("jobpilot.gigs.core.dispatcher.push_ntfy", _fake_push)

    g = Gig(
        id="hn-1",
        source="hn",
        title="AI Engineer",
        company="Acme",
        url="https://example.com",
        description="claude mcp agent",
        fit_score=80,
    )
    result = dispatch([g], push=False)
    assert result["pushed"] is False
    assert calls == []
    assert result["crib_path"] is None
    assert not (tmp_path / "c.md").exists()

    result2 = dispatch([g], push=True)
    assert result2["pushed"] is True
    assert calls == [True]
    assert result2["crib_path"] is None
    assert not (tmp_path / "c.md").exists()

    result3 = dispatch([g], push=False, crib=True)
    assert result3["crib_path"] == str(tmp_path / "c.md")
    assert (tmp_path / "c.md").read_text() == "c"
    assert calls == [True]


@pytest.mark.parametrize("command", ["now", "digest"])
@pytest.mark.parametrize("request_crib", [False, True])
def test_cli_requires_explicit_crib_request(monkeypatch, command, request_crib):
    from jobpilot.gigs import cli

    calls = []
    monkeypatch.setattr(cli, "_run_digest", lambda **kw: calls.append(kw))
    args = [command] + (["--crib"] if request_crib else [])
    result = CliRunner().invoke(cli.app, args)
    assert result.exit_code == 0, result.output
    assert calls[0]["crib"] is request_crib


@pytest.mark.parametrize("request_crib", [False, True])
def test_empty_pull_only_writes_crib_when_requested(monkeypatch, request_crib):
    from types import SimpleNamespace

    from jobpilot.gigs import cli

    monkeypatch.setattr(cli, "migrate_applied_into_pipeline", lambda: None)
    monkeypatch.setattr(cli, "_collect_with_status", lambda: [])
    monkeypatch.setattr(cli, "filter_and_rank", lambda *a, **kw: [])
    monkeypatch.setattr(cli, "enrich_wwr", lambda gigs: gigs)
    monkeypatch.setattr(cli, "sync_reminders_from_pipeline", lambda rows: 0)
    monkeypatch.setattr(cli.feedback, "sync_from_pipeline", lambda rows: 0)
    monkeypatch.setattr(cli.pipeline, "parse", lambda: [])
    monkeypatch.setattr(cli.pipeline, "load_status_snapshot", lambda: {})
    monkeypatch.setattr(cli.pipeline, "save_status_snapshot", lambda rows: None)
    monkeypatch.setattr(
        cli.pipeline, "migrate_pipeline_hygiene",
        lambda: {"collapsed": 0, "ids_regenerated": 0},
    )
    monkeypatch.setattr(cli.pipeline, "archive_stale_new", lambda: {})
    monkeypatch.setattr(
        cli.pipeline, "write", lambda *a, **kw: SimpleNamespace(refused=False)
    )
    monkeypatch.setattr(cli.run_state, "record_digest", lambda **kw: None)
    calls = []
    monkeypatch.setattr(
        "jobpilot.gigs.core.crib.write_crib_sheet",
        lambda gigs: calls.append(gigs) or "crib_sheet.md",
    )
    cli._run_digest(min_score=60, top_n=12, push=False, crib=request_crib)
    assert calls == ([[]] if request_crib else [])


def test_crib_includes_criteria_block(tmp_path, monkeypatch) -> None:
    from jobpilot.gigs.core.crib import write_crib_sheet

    prefs = dict(preferences.DEFAULTS)
    prefs["resumes"] = {"default": "garo_solutions_v1.pdf"}
    monkeypatch.setattr(preferences, "load", lambda path=None: prefs)
    path = write_crib_sheet([], crib_dir=tmp_path)
    text = path.read_text()
    assert "Active criteria + resumes" in text
    assert "garo_solutions_v1.pdf" in text
