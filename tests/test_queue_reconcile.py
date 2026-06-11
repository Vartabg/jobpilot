from pathlib import Path

import jobpilot.core.queue_builder as qb
from jobpilot.core.application_tracker import ApplicationTracker
from jobpilot.core.queue_builder import QueueJob


def test_reconcile_preserves_skipped_company_sibling(monkeypatch, tmp_path: Path):
    tracker = ApplicationTracker(data_dir=tmp_path)
    tracker.log_application(
        company="Growth Protocol",
        title="Forward Deployed Engineer",
        url="https://jobs.ashbyhq.com/growthprotocol/us-role",
        status="submitted",
    )
    monkeypatch.setattr(qb, "DATA_DIR", tmp_path)
    monkeypatch.setattr(qb, "QUEUE_PATH", tmp_path / "queue.json")
    monkeypatch.setattr(qb, "get_application_tracker", lambda: tracker)

    qb.save_queue(
        [
            QueueJob(
                id="london",
                company="Growth Protocol",
                title="Forward Deployed Engineer",
                url="https://jobs.ashbyhq.com/growthprotocol/london-role",
                location="London, United Kingdom",
                portal="ashby",
                track="both",
                fit_score=85,
                keywords=["engineer", "forward deployed"],
                status="skipped",
            ),
            QueueJob(
                id="us",
                company="Growth Protocol",
                title="Forward Deployed Engineer",
                url="https://jobs.ashbyhq.com/growthprotocol/us-role",
                location="New York City; Remote (United States)",
                portal="ashby",
                track="both",
                fit_score=85,
                keywords=["engineer", "forward deployed"],
                status="viewing",
            ),
        ]
    )

    changed, total = qb.reconcile_queue_with_tracker()
    loaded = {job.id: job.status for job in qb.load_queue()}

    assert (changed, total) == (1, 2)
    assert loaded["london"] == "skipped"
    assert loaded["us"] == "submitted"
