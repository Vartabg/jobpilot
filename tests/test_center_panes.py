"""Command center plain-language pane tests."""

from jobpilot.ui.center_panes import (
    _read_activity_messages,
    _translate_log_line,
    build_activity_panel,
    build_status_panel,
)


def test_translate_log_line_mark_applied():
    line = '2026-06-18 12:00:00 INFO: POST /api/job/abc123/mark-applied'
    assert _translate_log_line(line) == "You marked an application as submitted"


def test_translate_log_line_queue():
    assert _translate_log_line("GET /api/queue HTTP/1.1") == "Job list was viewed"


def test_read_activity_messages_from_text(tmp_path):
    log = tmp_path / "serve.log"
    log.write_text(
        "2026-06-18 10:00:00 INFO: Uvicorn running on http://127.0.0.1:8767\n"
        "2026-06-18 10:01:00 INFO: GET /api/queue\n",
        encoding="utf-8",
    )
    msgs = _read_activity_messages(log)
    assert any("Dashboard is online" in m or "Job list was viewed" in m for m in msgs)


def test_build_status_panel_renders(monkeypatch):
    monkeypatch.setattr("jobpilot.ui.center_panes.check_dashboard", lambda _p: True)
    monkeypatch.setattr("jobpilot.ui.center_panes.check_chrome", lambda _p=9222: True)
    monkeypatch.setattr(
        "jobpilot.ui.center_panes.load_gigs",
        lambda opts: ([], {"shown": 0, "fresh_count": 0, "collected": 0}),
    )
    monkeypatch.setattr("jobpilot.ui.center_panes.load_jobs", lambda opts: [])
    monkeypatch.setattr("jobpilot.ui.center_panes.load_pipeline_rows", lambda opts: [])
    monkeypatch.setattr("jobpilot.ui.center_panes._outreach_packages", lambda: [])

    panel = build_status_panel()
    assert panel.title is not None


def test_build_activity_panel_renders():
    panel = build_activity_panel()
    assert panel.title == "Recent activity"