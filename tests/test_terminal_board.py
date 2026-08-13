"""Tests for the Rich terminal board."""

from datetime import UTC, datetime
from types import SimpleNamespace

from rich.console import Console

from jobpilot.core.queue_builder import QueueJob
from jobpilot.ui.terminal_board import (
    BoardFilters,
    _next_up_panel,
    _queue_table,
    _stats_panel,
    build_board_renderable,
    filter_jobs,
)
from jobpilot.ui.view_helpers import score_bar


def _job(**overrides) -> QueueJob:
    values = {
        "id": "ready",
        "company": "Acme",
        "title": "Customer Engineer",
        "url": "https://jobs.example.test/acme/1",
        "location": "Austin, TX",
        "portal": "greenhouse",
        "track": "both",
        "fit_score": 78,
        "keywords": ["customer"],
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
    }
    values.update(overrides)
    return QueueJob(**values)


def _render_text(renderable, *, width: int = 200) -> str:
    console = Console(width=width, record=True)
    console.print(renderable)
    return console.export_text()


def test_score_bar_colors_by_threshold():
    high = score_bar(82)
    assert "82" in str(high)
    low = score_bar(35)
    assert "35" in str(low)


def test_filter_jobs_austin_and_fresh_uses_apply_readiness():
    jobs = [
        _job(id="ready", company="Osano"),
        _job(
            id="hold",
            company="Unverified",
            decision="investigate",
            assessment_status="unscorable",
            legitimacy_state="hold",
            verified_at="",
        ),
        _job(id="applied", company="Already Applied", status="applied"),
        _job(id="remote", company="Remote Ready", location="Remote US"),
    ]

    austin = filter_jobs(jobs, BoardFilters(fresh=True, austin=True, limit=10))

    assert [job.id for job in austin] == ["ready"]


def test_board_reconciles_before_loading_recommendations(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "jobpilot.ui.terminal_board.queue_builder.load_current_slate",
        lambda: calls.append("current") or [],
    )

    build_board_renderable()

    assert calls == ["current"]


def test_filter_jobs_legacy_queue_row_fails_closed():
    legacy = QueueJob(
        id="legacy",
        company="Legacy Co",
        title="Forward Deployed Engineer",
        url="https://jobs.example.test/legacy/1",
        location="Austin, TX",
        portal="greenhouse",
        track="both",
        fit_score=100,
        keywords=["engineer"],
        status="queued",
        psyche_score=15,
    )

    assert filter_jobs([legacy], BoardFilters(fresh=True)) == []


def test_filter_jobs_sorts_by_separate_evidence_axes():
    jobs = [
        _job(
            id="fit",
            decision="stretch",
            legitimacy_state="review",
            qualification_lower_bound=90,
            evidence_coverage=90,
            work_context_match=90,
            fit_score=100,
        ),
        _job(
            id="coverage",
            decision="stretch",
            legitimacy_state="review",
            qualification_lower_bound=90,
            evidence_coverage=100,
            work_context_match=0,
            fit_score=0,
        ),
        _job(
            id="decision",
            decision="apply_now",
            legitimacy_state="block",
            qualification_lower_bound=0,
            evidence_coverage=0,
            work_context_match=0,
            fit_score=0,
        ),
        _job(
            id="work-context",
            decision="stretch",
            legitimacy_state="review",
            qualification_lower_bound=90,
            evidence_coverage=90,
            work_context_match=100,
            fit_score=0,
        ),
        _job(
            id="qualification",
            decision="stretch",
            legitimacy_state="review",
            qualification_lower_bound=100,
            evidence_coverage=0,
            work_context_match=0,
            fit_score=0,
        ),
        _job(
            id="legitimacy",
            decision="stretch",
            legitimacy_state="recommend",
            qualification_lower_bound=0,
            evidence_coverage=0,
            work_context_match=0,
            fit_score=0,
        ),
        _job(
            id="lower-fit",
            decision="stretch",
            legitimacy_state="review",
            qualification_lower_bound=90,
            evidence_coverage=90,
            work_context_match=90,
            fit_score=90,
        ),
    ]

    result = filter_jobs(jobs, BoardFilters(status="all"))

    assert [job.id for job in result] == [
        "decision",
        "legitimacy",
        "qualification",
        "coverage",
        "work-context",
        "fit",
        "lower-fit",
    ]


def test_filter_jobs_does_not_sort_on_legacy_psyche_score():
    first = _job(id="first", psyche_score=0)
    second = _job(id="second", psyche_score=15)

    result = filter_jobs([first, second], BoardFilters(status="all"))

    assert [job.id for job in result] == ["first", "second"]


def test_stats_distinguish_apply_ready_from_investigate():
    ready = _job(id="ready")
    investigate = _job(
        id="investigate",
        decision="investigate",
        assessment_status="unscorable",
        legitimacy_state="hold",
        verified_at="",
    )

    text = _render_text(_stats_panel([ready, investigate]))

    assert "1 ready" in text
    assert "1 investigate" in text
    assert "Locations (ready)" in text


def test_next_up_only_uses_apply_ready_evidence():
    ready = _job(id="ready", company="Evidence Co", fit_score=25, psyche_score=0)
    legacy_proxy = QueueJob(
        id="legacy",
        company="Legacy Proxy Co",
        title="Forward Deployed Engineer",
        url="https://jobs.example.test/legacy/1",
        location="Austin, TX",
        portal="greenhouse",
        track="both",
        fit_score=100,
        keywords=["engineer"],
        status="queued",
        psyche_score=15,
    )

    text = _render_text(_next_up_panel([legacy_proxy, ready]))

    assert "Evidence Co" in text
    assert "Legacy Proxy Co" not in text
    assert "Decision apply now" in text
    assert "Qualification floor 78/100" in text
    assert "Coverage 75%" in text
    assert "Posting evidence A/recommend" in text
    assert "Biggest gap: AWS depth" in text
    assert "psyche" not in text.lower()


def test_next_up_uses_matched_account_when_no_gap():
    text = _render_text(
        _next_up_panel([_job(biggest_gap="", matched_accounts=["customer-field"])])
    )

    assert "Matched account: customer-field" in text


def test_queue_table_shows_concise_evidence_columns():
    text = _render_text(_queue_table([_job()], title="Queue"))

    assert "Decision" in text
    assert "Qual" in text
    assert "Cover" in text
    assert "Context" in text
    assert "Posting" in text
    assert "A/recommend" in text
    assert "Biggest gap" in text
    assert "Psy" not in text


def test_build_board_renderable_with_mocks(monkeypatch):
    job = _job(
        id="abc12345",
        company="Osano",
        title="Senior AI Engineer",
        url="https://boards.greenhouse.io/osano/jobs/1",
        portal="greenhouse",
        track="tech",
        fit_score=51,
        keywords=["engineer"],
    )
    profile = SimpleNamespace(
        first_name="Alex",
        last_name="Sample",
        city="Rivertown",
        state="OH",
    )
    tracker = SimpleNamespace(
        get_stats=lambda: {
            "total": 1,
            "submitted": 0,
            "interview": 0,
            "in_progress": 0,
        },
        get_recent=lambda limit: [],
        close=lambda: None,
    )
    store = SimpleNamespace(load=lambda: profile)

    monkeypatch.setattr(
        "jobpilot.ui.terminal_board.queue_builder.load_current_slate",
        lambda: [job],
    )
    monkeypatch.setattr("jobpilot.ui.terminal_board.get_profile_store", lambda: store)
    monkeypatch.setattr(
        "jobpilot.ui.terminal_board.get_application_tracker", lambda: tracker
    )
    monkeypatch.setattr(
        "jobpilot.ui.terminal_board.check_dashboard", lambda _port: False
    )
    monkeypatch.setattr("jobpilot.ui.terminal_board.check_chrome", lambda: False)

    text = _render_text(build_board_renderable(filters=BoardFilters(austin=True)))

    assert "JobPilot Board" in text
    assert "Osano" in text
    assert "Austin" in text
    assert "Decision" in text
    assert "psyche" not in text.lower()
