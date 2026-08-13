"""Radar view tests — shared loaders and senior filter."""

from datetime import UTC, datetime

from jobpilot.core.queue_builder import QueueJob
from jobpilot.gigs.core.models import Gig
from jobpilot.ui.income_data import IncomeViewOptions, load_jobs
from jobpilot.ui.radar import RadarOptions, build_radar_renderable


def _ready_job(**overrides) -> QueueJob:
    values = {
        "id": "ready",
        "company": "Civitech",
        "title": "Analytics Engineer",
        "url": "https://example.com/ready",
        "location": "Remote",
        "portal": "lever",
        "track": "tech",
        "fit_score": 70,
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
    }
    values.update(overrides)
    return QueueJob(**values)


def test_radar_options_alias():
    assert RadarOptions is IncomeViewOptions


def test_load_jobs_hides_senior_for_radar(monkeypatch):
    jobs = [
        _ready_job(
            id="a1",
            company="Osano",
            title="Senior AI Engineer",
            url="https://example.com/1",
            location="Austin, TX",
            portal="greenhouse",
        ),
        _ready_job(
            id="b2",
            url="https://example.com/2",
        ),
    ]
    monkeypatch.setattr(
        "jobpilot.ui.income_data.queue_builder.load_current_slate",
        lambda: jobs,
    )
    opts = IncomeViewOptions(austin=True, hide_senior_jobs=True, jobs_limit=10)
    view = load_jobs(opts)
    assert len(view) == 1
    assert view[0].company == "Civitech"


def test_load_jobs_hides_non_ready_and_legacy_roles(monkeypatch):
    ready = _ready_job(id="ready", fit_score=10)
    investigate = _ready_job(
        id="investigate",
        company="Needs Research",
        decision="investigate",
        legitimacy_state="hold",
        fit_score=100,
    )
    legacy = QueueJob(
        id="legacy",
        company="Legacy Proxy",
        title="Forward Deployed Engineer",
        url="https://example.com/legacy",
        location="Remote",
        portal="greenhouse",
        track="tech",
        fit_score=100,
        keywords=["engineer"],
        status="queued",
        psyche_score=15,
    )
    monkeypatch.setattr(
        "jobpilot.ui.income_data.queue_builder.load_current_slate",
        lambda: [investigate, legacy, ready],
    )

    view = load_jobs(
        IncomeViewOptions(austin=False, hide_senior_jobs=False, jobs_limit=10)
    )

    assert [job.id for job in view] == ["ready"]


def test_load_jobs_fails_closed_when_readiness_check_errors(monkeypatch):
    ready = _ready_job(id="ready")
    malformed = _ready_job(id="malformed")

    def readiness(job: QueueJob) -> bool:
        if job.id == "malformed":
            raise RuntimeError("malformed evidence")
        return True

    monkeypatch.setattr(
        "jobpilot.ui.income_data.queue_builder.load_current_slate",
        lambda: [malformed, ready],
    )
    monkeypatch.setattr(
        "jobpilot.ui.income_data.queue_builder.is_apply_ready", readiness
    )

    view = load_jobs(
        IncomeViewOptions(austin=False, hide_senior_jobs=False, jobs_limit=10)
    )

    assert [job.id for job in view] == ["ready"]


def test_load_jobs_sorts_each_evidence_axis_once(monkeypatch):
    jobs = [
        _ready_job(
            id="fit",
            decision="stretch",
            legitimacy_state="review",
            qualification_lower_bound=90,
            evidence_coverage=90,
            work_context_match=90,
            fit_score=100,
            psyche_score=15,
        ),
        _ready_job(
            id="coverage",
            decision="stretch",
            legitimacy_state="review",
            qualification_lower_bound=90,
            evidence_coverage=100,
            work_context_match=0,
            fit_score=0,
        ),
        _ready_job(
            id="decision",
            decision="apply_now",
            legitimacy_state="block",
            qualification_lower_bound=0,
            evidence_coverage=0,
            work_context_match=0,
            fit_score=0,
        ),
        _ready_job(
            id="context",
            decision="stretch",
            legitimacy_state="review",
            qualification_lower_bound=90,
            evidence_coverage=90,
            work_context_match=100,
            fit_score=0,
        ),
        _ready_job(
            id="qualification",
            decision="stretch",
            legitimacy_state="review",
            qualification_lower_bound=100,
            evidence_coverage=0,
            work_context_match=0,
            fit_score=0,
        ),
        _ready_job(
            id="legitimacy",
            decision="stretch",
            legitimacy_state="recommend",
            qualification_lower_bound=0,
            evidence_coverage=0,
            work_context_match=0,
            fit_score=0,
        ),
    ]
    monkeypatch.setattr(
        "jobpilot.ui.income_data.queue_builder.load_current_slate",
        lambda: jobs,
    )
    monkeypatch.setattr(
        "jobpilot.ui.income_data.queue_builder.is_apply_ready", lambda _job: True
    )

    view = load_jobs(
        IncomeViewOptions(austin=False, hide_senior_jobs=False, jobs_limit=10)
    )

    assert [job.id for job in view] == [
        "decision",
        "legitimacy",
        "qualification",
        "coverage",
        "context",
        "fit",
    ]


def test_build_radar_renderable_with_mocks(monkeypatch):
    gig = Gig(
        id="g1",
        source="hn",
        title="Contract Dev",
        url="https://example.com",
        company="Acme",
        fit_score=80,
    )
    job = _ready_job(
        id="j1",
        company="Co",
        title="Engineer",
        url="https://jobs.example.com",
        fit_score=50,
    )
    monkeypatch.setattr(
        "jobpilot.ui.radar.load_gigs", lambda opts, **kw: ([gig], {"shown": 1})
    )
    monkeypatch.setattr("jobpilot.ui.radar.load_jobs", lambda opts: [job])

    from rich.console import Console

    console = Console(width=120, record=True)
    console.print(build_radar_renderable(IncomeViewOptions(gigs_limit=5, jobs_limit=5)))
    text = console.export_text()
    assert "Autonomous Income Radar" in text
    assert "Acme" in text
    assert "Co" in text
    assert "Decision" in text
    assert "Qual" in text
    assert "Posting" in text
    assert "queued ATS" not in text
