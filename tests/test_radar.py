"""Radar view tests — shared loaders and senior filter."""

from jobpilot.core.queue_builder import QueueJob
from jobpilot.gigs.core.models import Gig
from jobpilot.ui.income_data import IncomeViewOptions, load_jobs
from jobpilot.ui.radar import RadarOptions, build_radar_renderable


def test_radar_options_alias():
    assert RadarOptions is IncomeViewOptions


def test_load_jobs_hides_senior_for_radar(monkeypatch):
    jobs = [
        QueueJob(
            id="a1", company="Osano", title="Senior AI Engineer",
            url="https://example.com/1", location="Austin, TX",
            portal="greenhouse", track="tech", fit_score=51, keywords=[], status="queued",
        ),
        QueueJob(
            id="b2", company="Civitech", title="Analytics Engineer",
            url="https://example.com/2", location="Remote",
            portal="lever", track="tech", fit_score=46, keywords=[], status="queued",
        ),
    ]
    monkeypatch.setattr("jobpilot.ui.income_data.load_queue", lambda: jobs)
    opts = IncomeViewOptions(austin=True, hide_senior_jobs=True, jobs_limit=10)
    view = load_jobs(opts)
    assert len(view) == 1
    assert view[0].company == "Civitech"


def test_build_radar_renderable_with_mocks(monkeypatch):
    gig = Gig(
        id="g1", source="hn", title="Contract Dev", url="https://example.com",
        company="Acme", fit_score=80,
    )
    job = QueueJob(
        id="j1", company="Co", title="Engineer", url="https://jobs.example.com",
        location="Remote", portal="lever", track="tech", fit_score=50,
        keywords=[], status="queued",
    )
    monkeypatch.setattr("jobpilot.ui.radar.load_gigs", lambda opts, **kw: ([gig], {"shown": 1}))
    monkeypatch.setattr("jobpilot.ui.radar.load_jobs", lambda opts: [job])

    from rich.console import Console

    console = Console(width=120, record=True)
    console.print(build_radar_renderable(IncomeViewOptions(gigs_limit=5, jobs_limit=5)))
    text = console.export_text()
    assert "Autonomous Income Radar" in text
    assert "Acme" in text
    assert "Co" in text