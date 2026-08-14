"""Model-neutral service contract for AI agents."""

from datetime import UTC, datetime

import pytest

import jobpilot.core.jobpilot_service as service_module
from jobpilot.core.agent_contract import CONTRACT_VERSION, AgentServiceError
from jobpilot.core.application_evidence import EvidenceSourceError
from jobpilot.core.jobpilot_service import JobPilotService
from jobpilot.core.outcome_service import OutcomeInput
from jobpilot.core.queue_builder import QueueJob


def _job(**overrides) -> QueueJob:
    values = {
        "id": "role-1",
        "company": "Acme",
        "title": "Customer Engineer",
        "url": "https://jobs.example.test/acme/1",
        "location": "Denver, CO",
        "portal": "ashby",
        "track": "both",
        "fit_score": 80,
        "keywords": ["customer"],
        "decision": "apply_now",
        "assessment_status": "assessed",
        "legitimacy_state": "recommend",
        "evidence_grade": "A",
        "verified_at": datetime.now(UTC).isoformat(),
        "relocation_state": "offered",
        "relocation_destination": "Denver, CO",
        "relocation_evidence": ["Relocation assistance is available."],
    }
    values.update(overrides)
    return QueueJob(**values)


def test_capabilities_are_versioned_model_neutral_and_human_gated() -> None:
    result = JobPilotService().capabilities().to_dict()

    assert result["contract_version"] == CONTRACT_VERSION
    assert result["operation"] == "capabilities.read"
    assert result["data"]["service"] == "jobpilot"
    assert result["data"]["boundaries"] == {
        "live_ats_fill": False,
        "live_ats_submit": False,
        "human_review_required": True,
        "human_submit_required": True,
    }
    encoded = str(result).casefold()
    assert "claude" not in encoded
    assert "openai" not in encoded
    assert "gemini" not in encoded


def test_list_opportunities_is_read_only_and_has_one_agent_shape(monkeypatch) -> None:
    job = _job()
    calls: list[str] = []
    monkeypatch.setattr(
        service_module.queue_builder,
        "load_queue",
        lambda: calls.append("load") or [job],
    )
    monkeypatch.setattr(
        service_module.queue_builder,
        "restrict_queue_for_incomplete_history",
        lambda jobs: calls.append("history") or 0,
    )
    monkeypatch.setattr(
        service_module.queue_builder,
        "restrict_queue_for_action_provenance",
        lambda jobs: calls.append("provenance") or 0,
    )
    monkeypatch.setattr(
        service_module.queue_builder, "is_apply_ready", lambda _job: True
    )
    result = (
        JobPilotService(review_resolver=lambda _job: "ready")
        .list_opportunities(ready_only=True)
        .to_dict()
    )

    assert calls == ["load", "history", "provenance"]
    assert result["operation"] == "opportunities.list"
    assert result["data"]["count"] == 1
    item = result["data"]["items"][0]
    assert item["relocation_state"] == "offered"
    assert item["target_review_state"] == "ready"
    assert item["claim_state"] == "ready"
    assert item["action_readiness"]["state"] == "apply_ready"
    assert item["human_actions"][-1] == "submit_application"


def test_refresh_and_list_is_the_only_network_mutation(monkeypatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr(
        service_module.queue_builder,
        "refresh_queue",
        lambda *, limit: calls.append(limit) or [_job()],
    )
    monkeypatch.setattr(
        service_module.queue_builder, "is_apply_ready", lambda _job: True
    )

    result = JobPilotService().refresh_opportunities(limit=25).to_dict()

    assert calls == [25]
    assert result["operation"] == "opportunities.refresh"
    assert result["data"]["count"] == 1
    assert result["data"]["side_effect"] == "network_and_local_state"


def test_refresh_converts_evidence_failure_to_actionable_contract_error(
    monkeypatch,
) -> None:
    def fail(*, limit):
        raise EvidenceSourceError("The configured history export is stale.")

    monkeypatch.setattr(service_module.queue_builder, "refresh_queue", fail)

    with pytest.raises(AgentServiceError) as error:
        JobPilotService().refresh_opportunities(limit=10)

    assert error.value.code == "application_history_incomplete"
    assert "gmail-sync" in error.value.action


def test_assess_role_requires_a_full_description(monkeypatch) -> None:
    with pytest.raises(AgentServiceError) as error:
        JobPilotService().assess_role(title="Engineer", job_description=" ")

    assert error.value.code == "missing_job_description"
    assert "full job description" in error.value.action.casefold()


def test_preflight_fails_closed_without_action_provenance(monkeypatch) -> None:
    monkeypatch.setattr(service_module.queue_builder, "load_queue", lambda: [_job()])
    monkeypatch.setattr(
        service_module.queue_builder,
        "restrict_queue_for_incomplete_history",
        lambda jobs: 0,
    )
    monkeypatch.setattr(
        service_module.queue_builder,
        "restrict_queue_for_action_provenance",
        lambda jobs: 0,
    )
    monkeypatch.setattr(
        service_module.queue_builder, "is_apply_ready", lambda _job: True
    )
    monkeypatch.setattr(
        service_module.queue_builder,
        "has_current_action_provenance",
        lambda _job: False,
    )
    monkeypatch.setattr(
        service_module,
        "read_tracker_status",
        lambda *_args: (True, None),
    )

    result = JobPilotService().preflight("role-1").to_dict()

    assert result["data"]["state"] == "investigate"
    assert result["data"]["may_prepare"] is False
    assert result["human_action_required"] is True


def test_configured_target_review_must_clear_before_preparation(monkeypatch) -> None:
    job = _job()
    monkeypatch.setattr(service_module.queue_builder, "load_queue", lambda: [job])
    monkeypatch.setattr(
        service_module.queue_builder,
        "restrict_queue_for_incomplete_history",
        lambda jobs: 0,
    )
    monkeypatch.setattr(
        service_module.queue_builder,
        "restrict_queue_for_action_provenance",
        lambda jobs: 0,
    )
    monkeypatch.setattr(
        service_module.queue_builder, "is_apply_ready", lambda _job: True
    )

    result = (
        JobPilotService(review_resolver=lambda _job: "unvetted")
        .list_opportunities(ready_only=True)
        .to_dict()
    )

    assert result["data"]["count"] == 0


def test_preflight_reads_tracker_and_blocks_an_existing_application(
    monkeypatch,
) -> None:
    job = _job()
    monkeypatch.setattr(service_module.queue_builder, "load_queue", lambda: [job])
    monkeypatch.setattr(
        service_module.queue_builder,
        "restrict_queue_for_incomplete_history",
        lambda jobs: 0,
    )
    monkeypatch.setattr(
        service_module.queue_builder,
        "restrict_queue_for_action_provenance",
        lambda jobs: 0,
    )
    monkeypatch.setattr(
        service_module.queue_builder, "is_apply_ready", lambda _job: True
    )
    monkeypatch.setattr(
        service_module.queue_builder,
        "has_current_action_provenance",
        lambda _job: True,
    )
    monkeypatch.setattr(
        service_module,
        "read_tracker_status",
        lambda *_args: (True, "applied"),
    )

    result = JobPilotService().preflight("role-1").to_dict()

    assert result["data"]["may_prepare"] is False
    assert result["data"]["tracked_status"] == "applied"


def test_preflight_fails_closed_when_tracker_history_is_unreadable(monkeypatch) -> None:
    monkeypatch.setattr(service_module.queue_builder, "load_queue", lambda: [_job()])
    monkeypatch.setattr(
        service_module.queue_builder,
        "restrict_queue_for_incomplete_history",
        lambda jobs: 0,
    )
    monkeypatch.setattr(
        service_module.queue_builder,
        "restrict_queue_for_action_provenance",
        lambda jobs: 0,
    )
    monkeypatch.setattr(
        service_module.queue_builder, "is_apply_ready", lambda _job: True
    )
    monkeypatch.setattr(
        service_module.queue_builder,
        "has_current_action_provenance",
        lambda _job: True,
    )
    monkeypatch.setattr(
        service_module,
        "read_tracker_status",
        lambda *_args: (False, None),
    )

    result = JobPilotService().preflight("role-1").to_dict()

    assert result["data"]["may_prepare"] is False
    assert result["data"]["tracker_history_available"] is False


def test_dashboard_uses_privacy_safe_metrics(monkeypatch) -> None:
    job = _job()
    monkeypatch.setattr(service_module.queue_builder, "load_queue", lambda: [job])
    monkeypatch.setattr(
        service_module.queue_builder,
        "restrict_queue_for_incomplete_history",
        lambda jobs: 0,
    )
    monkeypatch.setattr(
        service_module.queue_builder,
        "restrict_queue_for_action_provenance",
        lambda jobs: 0,
    )
    monkeypatch.setattr(
        service_module,
        "read_tracker_stats",
        lambda: {"total": 7},
    )
    monkeypatch.setattr(
        service_module,
        "collect_dashboard_metrics",
        lambda jobs, tracker_stats: {
            "queue": {"roles": len(jobs)},
            "tracker": tracker_stats,
        },
    )

    result = JobPilotService().dashboard().to_dict()

    assert result["operation"] == "dashboard.read"
    assert result["data"] == {"queue": {"roles": 1}, "tracker": {"total": 7}}


def test_record_outcome_requires_explicit_human_confirmation() -> None:
    with pytest.raises(AgentServiceError) as error:
        JobPilotService().record_outcome(
            OutcomeInput(
                company="Acme",
                title="Customer Engineer",
                status="applied",
                human_confirmed=False,
            )
        )

    assert error.value.code == "human_confirmation_required"
