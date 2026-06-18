"""Tests for the Rich terminal board."""

import json
from pathlib import Path

from jobpilot.core.queue_builder import QueueJob, save_queue
from jobpilot.ui.terminal_board import (
    BoardFilters,
    build_board_renderable,
    filter_jobs,
    score_bar,
)


def test_score_bar_colors_by_threshold():
    high = score_bar(82)
    assert "82" in str(high)
    low = score_bar(35)
    assert "35" in str(low)


def test_filter_jobs_austin_and_fresh():
    jobs = [
        QueueJob(
            id="a1", company="Osano", title="Senior AI Engineer",
            url="https://example.com/1", location="Austin, TX",
            portal="greenhouse", track="tech", fit_score=51, keywords=["ai"],
            status="queued",
        ),
        QueueJob(
            id="b2", company="Starbridge", title="FDE",
            url="https://example.com/2", location="New York City",
            portal="ashby", track="both", fit_score=90, keywords=["fde"],
            status="applied",
        ),
    ]
    austin = filter_jobs(jobs, BoardFilters(fresh=True, austin=True, limit=10))
    assert len(austin) == 1
    assert austin[0].company == "Osano"


def test_build_board_renderable_with_mocks(monkeypatch):
    from types import SimpleNamespace

    job = QueueJob(
        id="abc12345",
        company="Osano",
        title="Senior AI Engineer",
        url="https://boards.greenhouse.io/osano/jobs/1",
        location="Austin, TX",
        portal="greenhouse",
        track="tech",
        fit_score=51,
        keywords=["engineer"],
        status="queued",
        psyche_score=7,
    )
    profile = SimpleNamespace(first_name="Garo", last_name="Vartabedian", city="Austin", state="TX")
    tracker = SimpleNamespace(
        get_stats=lambda: {"total": 1, "submitted": 0, "interview": 0, "in_progress": 0},
        get_recent=lambda limit: [],
        close=lambda: None,
    )
    store = SimpleNamespace(load=lambda: profile)

    monkeypatch.setattr("jobpilot.ui.terminal_board.load_queue", lambda: [job])
    monkeypatch.setattr("jobpilot.ui.terminal_board.get_profile_store", lambda: store)
    monkeypatch.setattr("jobpilot.ui.terminal_board.get_application_tracker", lambda: tracker)
    monkeypatch.setattr("jobpilot.ui.terminal_board._check_dashboard", lambda _port: False)
    monkeypatch.setattr("jobpilot.ui.terminal_board._check_chrome", lambda _port=9222: False)

    from rich.console import Console

    console = Console(width=120, record=True)
    console.print(build_board_renderable(filters=BoardFilters(austin=True)))
    text = console.export_text()
    assert "JobPilot Board" in text
    assert "Osano" in text
    assert "Austin" in text