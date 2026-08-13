"""The everyday jobs command must reconcile before emitting recommendations."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

from typer.testing import CliRunner

import jobpilot.cli as cli
from jobpilot.core.queue_builder import QueueJob

runner = CliRunner()


def test_claim_state_is_exposed_without_changing_queue_json(tmp_path):
    lock = tmp_path / "claude-vetted-targets.json"
    lock.write_text(
        '{"targets":[{"company":"Acme","title":"Solutions Engineer",'
        '"decision":"keep","materials_status":"ready"}]}'
    )
    job = SimpleNamespace(
        company="Acme",
        title="Solutions Engineer",
        url="https://jobs.example.test/acme/1",
    )

    with patch.object(cli, "CLAUDE_VETTED_TARGETS_PATH", lock):
        assert cli._claim_state_for_job(job) == "ready"


def test_claim_state_does_not_cross_roles_at_same_company(tmp_path):
    lock = tmp_path / "claude-vetted-targets.json"
    lock.write_text(
        '{"targets":[{"company":"P-1 AI","title":"Forward Deployed Engineer",'
        '"decision":"keep","materials_status":"ready"}]}'
    )
    different_role = SimpleNamespace(
        company="P-1 AI",
        title="Founding Product Designer",
        url="https://jobs.example.test/p1/product-designer",
    )

    with patch.object(cli, "CLAUDE_VETTED_TARGETS_PATH", lock):
        assert cli._claim_state_for_job(different_role) == "unvetted"


def test_claim_state_does_not_expand_to_senior_title(tmp_path):
    lock = tmp_path / "claude-vetted-targets.json"
    lock.write_text(
        '{"targets":[{"company":"Acme","title":"Solutions Engineer",'
        '"decision":"keep","materials_status":"ready"}]}'
    )
    senior_role = SimpleNamespace(
        company="Acme",
        title="Senior Solutions Engineer",
        url="https://jobs.example.test/acme/senior-solutions",
    )

    with patch.object(cli, "CLAUDE_VETTED_TARGETS_PATH", lock):
        assert cli._claim_state_for_job(senior_role) == "unvetted"


def test_json_job_payload_includes_claim_state():
    job = SimpleNamespace(
        id="1",
        company="Acme",
        title="Solutions Engineer",
        url="https://jobs.example.test/acme/1",
        location="Remote",
        portal="ashby",
        track="tech",
        fit_score=80,
        keywords=["solutions"],
        status="queued",
        queued_at="2026-07-16T00:00:00",
        psyche_score=10,
    )

    with patch.object(cli, "_claim_state_for_job", return_value="unvetted"):
        payload = cli._job_output_payload(job)

    assert payload["claim_state"] == "unvetted"
    assert payload["status"] == "queued"


def _queue_job(**overrides) -> QueueJob:
    values = {
        "id": "ready",
        "company": "Acme",
        "title": "Customer Engineer",
        "url": "https://jobs.example.test/acme/1",
        "location": "Austin",
        "portal": "greenhouse",
        "track": "both",
        "fit_score": 80,
        "keywords": ["customer"],
        "status": "queued",
        "decision": "apply_now",
        "assessment_status": "assessed",
        "legitimacy_state": "recommend",
        "evidence_grade": "A",
        "verified_at": datetime.now(UTC).isoformat(),
    }
    values.update(overrides)
    return QueueJob(**values)


def test_cached_json_queue_reconciles_before_emitting(monkeypatch):
    reconciled: list[bool] = []
    monkeypatch.setattr("pathlib.Path.exists", lambda _path: True)
    monkeypatch.setattr(
        "jobpilot.core.queue_builder.reconcile_queue_with_tracker",
        lambda: reconciled.append(True) or (0, 1),
    )
    monkeypatch.setattr(
        "jobpilot.core.queue_builder.load_queue",
        lambda: [_queue_job()],
    )

    result = runner.invoke(cli.app, ["queue", "--json", "--fresh", "--no-open"])

    assert result.exit_code == 0
    assert reconciled == [True]
    assert '"id": "ready"' in result.stdout


def test_fresh_json_hides_hold_and_investigate_rows(monkeypatch):
    monkeypatch.setattr("pathlib.Path.exists", lambda _path: True)
    monkeypatch.setattr(
        "jobpilot.core.queue_builder.reconcile_queue_with_tracker",
        lambda: (0, 2),
    )
    monkeypatch.setattr(
        "jobpilot.core.queue_builder.load_queue",
        lambda: [
            _queue_job(),
            _queue_job(
                id="hold",
                title="Unverified Role",
                decision="investigate",
                legitimacy_state="hold",
                verified_at="",
            ),
        ],
    )

    result = runner.invoke(cli.app, ["queue", "--json", "--fresh", "--no-open"])

    assert result.exit_code == 0
    assert '"id": "ready"' in result.stdout
    assert '"id": "hold"' not in result.stdout


def test_board_help_describes_the_apply_ready_gate():
    result = runner.invoke(cli.app, ["board", "--help"])

    assert result.exit_code == 0
    assert "verified apply-ready roles" in result.stdout


def test_queue_open_points_to_api_backed_dashboard_not_html_file(monkeypatch):
    monkeypatch.setattr("pathlib.Path.exists", lambda _path: True)
    monkeypatch.setattr(
        "jobpilot.core.queue_builder.reconcile_queue_with_tracker",
        lambda: (0, 1),
    )
    monkeypatch.setattr(
        "jobpilot.core.queue_builder.load_queue",
        lambda: [_queue_job()],
    )

    result = runner.invoke(cli.app, ["queue", "--no-board"])

    assert result.exit_code == 0
    assert "jobpilot serve" in result.stdout
    assert "http://127.0.0.1:8767/" in result.stdout
    assert "dashboard.html" not in result.stdout
