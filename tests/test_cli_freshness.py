"""The everyday jobs command must reconcile before emitting recommendations."""

from types import SimpleNamespace
from unittest.mock import patch

import jobpilot.cli as cli


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
