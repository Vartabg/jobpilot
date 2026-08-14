from datetime import UTC, datetime
from pathlib import Path

from jobpilot.core.dashboard_metrics import collect_dashboard_metrics
from jobpilot.core.opportunity_ledger import OpportunityLedger
from jobpilot.core.queue_builder import QueueJob


def _job(**overrides) -> QueueJob:
    values = {
        "id": "role-1",
        "company": "Acme",
        "title": "Solutions Engineer",
        "url": "https://jobs.example.test/acme/1",
        "location": "Seattle, WA",
        "portal": "greenhouse",
        "track": "tech",
        "fit_score": 60,
        "keywords": [],
        "status": "queued",
        "decision": "investigate",
        "assessment_status": "assessed",
        "legitimacy_state": "recommend",
        "relocation_state": "offered",
    }
    values.update(overrides)
    return QueueJob(**values)


def test_dashboard_combines_queue_latest_source_health_and_outcomes(
    tmp_path: Path,
) -> None:
    now = datetime.now(UTC)
    db_path = tmp_path / "opportunities.db"
    ledger = OpportunityLedger(db_path=db_path)
    try:
        opportunity_id = ledger.upsert_opportunity("Acme", "Solutions Engineer")
        ledger.record_source_run(
            "greenhouse",
            "acme",
            "failure",
            fetched_at="2026-01-01T00:00:00+00:00",
            payload={"error_type": "ReadTimeout"},
        )
        ledger.record_source_run(
            "greenhouse",
            "acme",
            "success",
            12,
            fetched_at=now.isoformat(),
        )
        ledger.record_source_run(
            "ashby",
            "other",
            "skipped",
            fetched_at=now.isoformat(),
        )
        ledger.append_event(opportunity_id, "applied", now.isoformat())
        ledger.append_event(opportunity_id, "human_reply", now.isoformat())
        ledger.append_event(opportunity_id, "interview", now.isoformat())
    finally:
        ledger.close()

    metrics = collect_dashboard_metrics(
        [_job()],
        tracker_stats={"total": 9},
        db_path=db_path,
        now=now,
    )

    assert metrics["queue"]["supported_relocation"] == 1
    assert metrics["relocation"]["offered"] == 1
    assert metrics["source_health"] == {
        "available": True,
        "total": 2,
        "healthy": 1,
        "with_results": 1,
        "zero_results": 0,
        "failed": 0,
        "disabled": 1,
        "matched_listings": 12,
        "last_scan_at": now.isoformat(),
        "failure_groups": [],
    }
    assert metrics["outcomes"]["response_rate"] == 100
    assert metrics["outcomes"]["interview_rate"] == 100
    assert metrics["tracker"]["total"] == 9


def test_missing_ledger_is_reported_without_creating_it(tmp_path: Path) -> None:
    missing = tmp_path / "missing.db"

    metrics = collect_dashboard_metrics([_job()], db_path=missing)

    assert metrics["source_health"]["available"] is False
    assert metrics["outcomes"]["stages"]["applied"] == 0
    assert not missing.exists()
