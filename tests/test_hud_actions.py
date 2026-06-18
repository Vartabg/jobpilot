"""Tests for HUD keyboard actions (mocked subprocess / IO)."""

from unittest.mock import MagicMock, patch

from jobpilot.core.queue_builder import QueueJob
from jobpilot.gigs.core.models import Gig
from jobpilot.ui import hud_actions


def test_open_url_calls_mac_open():
    with patch("jobpilot.ui.hud_actions.subprocess.run") as run:
        assert hud_actions.open_url("https://example.com")
        run.assert_called_once_with(["open", "https://example.com"], check=False)


def test_open_url_empty():
    assert not hud_actions.open_url("")


def test_copy_text_uses_pbcopy():
    with patch("jobpilot.ui.hud_actions.subprocess.run") as run:
        assert hud_actions.copy_text("hello")
        run.assert_called_once()
        assert run.call_args[0][0] == ["pbcopy"]


def test_skip_job(monkeypatch):
    job = QueueJob(
        id="x", company="Co", title="T", url="u", location="Austin",
        portal="gh", track="tech", fit_score=50, keywords=[], status="queued",
    )
    monkeypatch.setattr("jobpilot.ui.hud_actions.update_job_status", lambda _id, st: st == "skipped")
    assert "skipped" in hud_actions.skip_job(job)


def test_pick_with_fzf_returns_index():
    lines = ["a", "b", "c"]
    proc = MagicMock(returncode=0, stdout=b"b\n")
    with patch("jobpilot.ui.hud_actions.subprocess.run", return_value=proc):
        assert hud_actions.pick_with_fzf(lines) == 1


def test_draft_gig_proposal_returns_message(monkeypatch):
    gig = Gig(id="gig-123", source="hn", title="Build API", url="https://x.com", company="Acme", fit_score=80)
    monkeypatch.setattr(
        "jobpilot.gigs.core.proposals.build_revenue_brief",
        lambda g: type("B", (), {"offer": "hi", "action": "do"})(),
    )
    with patch("jobpilot.ui.hud_actions.subprocess.run"):
        with patch("pathlib.Path.write_text"):
            with patch("pathlib.Path.mkdir"):
                msg = hud_actions.draft_gig_proposal(gig)
    assert "draft" in msg