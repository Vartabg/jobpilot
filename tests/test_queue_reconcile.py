import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import jobpilot.core.queue_builder as qb
from jobpilot.core.application_tracker import ApplicationTracker
from jobpilot.core.policy_config import policy_from_dict, set_policy
from jobpilot.core.queue_builder import QueueJob


def _job(job_id: str, *, status: str = "queued") -> QueueJob:
    return QueueJob(
        id=job_id,
        company=f"Company {job_id}",
        title="Customer Engineer",
        url=f"https://jobs.example.test/{job_id}",
        location="Remote, United States",
        portal="greenhouse",
        track="both",
        fit_score=80,
        keywords=["customer"],
        status=status,
    )


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


def test_get_job_reconciles_external_application_before_action(
    monkeypatch, tmp_path: Path
) -> None:
    tracker = ApplicationTracker(data_dir=tmp_path)
    monkeypatch.setattr(qb, "DATA_DIR", tmp_path)
    monkeypatch.setattr(qb, "QUEUE_PATH", tmp_path / "queue.json")
    monkeypatch.setattr(qb, "get_application_tracker", lambda: tracker)
    job = QueueJob(
        id="acme-role",
        company="Acme",
        title="Customer Engineer",
        url="https://jobs.example.test/acme/1",
        location="Remote US",
        portal="greenhouse",
        track="both",
        fit_score=80,
        keywords=["customer"],
        status="queued",
    )
    qb.save_queue([job])
    tracker.log_application(
        company=job.company,
        title=job.title,
        url=job.url,
        status="applied",
    )

    current = qb.get_job(job.id)

    assert current is not None
    assert current.status == "applied"
    assert qb.is_apply_ready(current) is False


def test_reconcile_downgrades_cached_recommendation_when_history_is_incomplete(
    monkeypatch,
    tmp_path: Path,
) -> None:
    tracker = ApplicationTracker(data_dir=tmp_path)

    class _IncompleteEvidence:
        errors = ("Gmail cache is stale",)

        @staticmethod
        def match(_company: str, _title: str, _url: str):
            return None

    monkeypatch.setattr(qb, "QUEUE_PATH", tmp_path / "queue.json")
    monkeypatch.setattr(qb, "get_application_tracker", lambda: tracker)
    monkeypatch.setattr(
        qb,
        "get_application_evidence_index",
        lambda _tracker=None: _IncompleteEvidence(),
    )
    ready = _job("ready")
    ready.decision = "apply_now"
    ready.assessment_status = "assessed"
    ready.legitimacy_state = "recommend"
    ready.verified_at = datetime.now(UTC).isoformat()
    qb.save_queue([ready])
    set_policy(policy_from_dict({
        "application_evidence": {"fail_closed": True},
    }))

    try:
        changed, total = qb.reconcile_queue_with_tracker()
        current = qb.load_queue()[0]
    finally:
        set_policy(None)

    assert (changed, total) == (1, 1)
    assert current.decision == "investigate"
    assert "Application history is incomplete" in current.suppression_reason


def test_refresh_and_status_update_are_one_serialized_transaction(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(qb, "QUEUE_PATH", tmp_path / "queue.json")
    qb.save_queue([_job("role-1")])
    refresh_started = threading.Event()
    release_refresh = threading.Event()
    status_started = threading.Event()

    def slow_build(**_kwargs) -> list[QueueJob]:
        stale_snapshot = qb.load_queue()
        refresh_started.set()
        assert release_refresh.wait(timeout=3)
        return stale_snapshot

    def mark_skipped() -> bool:
        status_started.set()
        return qb.update_job_status("role-1", "skipped")

    monkeypatch.setattr(qb, "build_queue", slow_build)
    with ThreadPoolExecutor(max_workers=2) as executor:
        refresh_future = executor.submit(qb.refresh_queue)
        assert refresh_started.wait(timeout=3)
        status_future = executor.submit(mark_skipped)
        assert status_started.wait(timeout=3)
        assert status_future.done() is False
        release_refresh.set()
        assert len(refresh_future.result(timeout=3)) == 1
        assert status_future.result(timeout=3) is True

    assert qb.load_queue()[0].status == "skipped"


def test_concurrent_atomic_saves_publish_one_complete_snapshot(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(qb, "QUEUE_PATH", tmp_path / "queue.json")
    snapshots = [
        [_job(f"alpha-{index}") for index in range(30)],
        [_job(f"beta-{index}") for index in range(30)],
    ]
    barrier = threading.Barrier(3)

    def save(snapshot: list[QueueJob]) -> None:
        barrier.wait(timeout=3)
        qb.save_queue(snapshot)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(save, snapshot) for snapshot in snapshots]
        barrier.wait(timeout=3)
        for future in futures:
            future.result(timeout=3)

    persisted = json.loads(qb.QUEUE_PATH.read_text(encoding="utf-8"))
    persisted_ids = {row["id"] for row in persisted}
    expected_ids = [{job.id for job in snapshot} for snapshot in snapshots]
    assert len(persisted) == 30
    assert persisted_ids in expected_ids
    assert not list(tmp_path.glob(".queue.json.*.tmp"))


def test_load_queue_keeps_valid_rows_and_logs_each_malformed_row(
    monkeypatch,
    tmp_path: Path,
    caplog,
) -> None:
    monkeypatch.setattr(qb, "QUEUE_PATH", tmp_path / "queue.json")
    second = asdict(_job("valid-2"))
    second["future_schema_field"] = "ignored"
    qb.QUEUE_PATH.write_text(
        json.dumps([
            asdict(_job("valid-1")),
            "not an object",
            {"id": "missing-fields"},
            {**asdict(_job("invalid-list")), "keywords": "customer"},
            second,
        ]),
        encoding="utf-8",
    )
    caplog.set_level(logging.WARNING, logger=qb.log.name)

    loaded = qb.load_queue()

    assert [job.id for job in loaded] == ["valid-1", "valid-2"]
    warnings = [
        record.message
        for record in caplog.records
        if "Skipping malformed queue row" in record.message
    ]
    assert len(warnings) == 3


def test_load_queue_rejects_non_list_top_level_with_visible_warning(
    monkeypatch,
    tmp_path: Path,
    caplog,
) -> None:
    monkeypatch.setattr(qb, "QUEUE_PATH", tmp_path / "queue.json")
    qb.QUEUE_PATH.write_text('{"jobs": []}', encoding="utf-8")
    caplog.set_level(logging.WARNING, logger=qb.log.name)

    assert qb.load_queue() == []
    assert "top-level JSON must be a list" in caplog.text
