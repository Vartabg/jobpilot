import json
from pathlib import Path

from jobpilot.gigs.core import feedback, pipeline, run_state, source_health
from jobpilot.gigs.core.feedback import parse_pass_reason, sync_from_pipeline
from jobpilot.gigs.core.pipeline import Row
from jobpilot.gigs.core.source_health import SourceResult, record_results, stale_zero_sources


def test_parse_pass_reason_extracts_code() -> None:
    assert parse_pass_reason("pass:wrong-stack — not my stack") == "wrong-stack"
    assert parse_pass_reason("looks spammy") == "other"


def test_parse_pass_reason_finds_code_mid_text() -> None:
    # Regression: the start-anchored .match treated any leading prose as
    # 'other' — "too low — pass:low-pay" lost its reason code.
    assert parse_pass_reason("too low — pass:low-pay") == "low-pay"
    assert parse_pass_reason("see digest, pass: spam") == "spam"
    assert parse_pass_reason("bypass:low-pay") == "other"  # word boundary
    assert parse_pass_reason("pass:not-a-known-code") == "other"


def test_sync_from_pipeline_appends_pass_feedback(tmp_path, monkeypatch) -> None:
    fb = tmp_path / "feedback.jsonl"
    idx = tmp_path / "feedback_index.json"
    monkeypatch.setattr(feedback, "FEEDBACK_PATH", fb)
    monkeypatch.setattr(feedback, "INDEX_PATH", idx)

    rows = [
        Row(
            status="passed",
            company="Acme",
            role="DevOps",
            notes="pass:wrong-stack",
            gig_id="wwr-1",
        ),
    ]
    assert sync_from_pipeline(rows) == 1
    assert sync_from_pipeline(rows) == 0
    assert json.loads(fb.read_text().strip())["reason"] == "wrong-stack"


def test_source_health_zero_streak(tmp_path, monkeypatch) -> None:
    path = tmp_path / "sources_health.json"
    monkeypatch.setattr(source_health, "HEALTH_PATH", path)

    for _ in range(3):
        record_results([SourceResult(name="RemoteOK", fetched=0, ok=True)])
    assert "RemoteOK" in stale_zero_sources(min_streak=3)

    record_results([SourceResult(name="RemoteOK", fetched=5, ok=True)])
    assert "RemoteOK" not in stale_zero_sources(min_streak=3)


def test_run_state_record_and_stale(tmp_path, monkeypatch) -> None:
    path = tmp_path / "last_run.json"
    monkeypatch.setattr(run_state, "LAST_RUN_PATH", path)

    run_state.record_digest(ok=True, ranked_new=2, pushed=True)
    data = run_state.load()
    assert data["ok"] is True
    assert data["ranked_new"] == 2
    assert not run_state.digest_is_stale(hours=1)