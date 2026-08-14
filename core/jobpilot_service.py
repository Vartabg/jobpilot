"""Model-neutral orchestration facade over JobPilot domain capabilities."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from jobpilot.core import queue_builder
from jobpilot.core.agent_contract import (
    AgentEnvelope,
    AgentServiceError,
    opportunity_payload,
)
from jobpilot.core.application_evidence import (
    EvidenceSourceError,
    configured_external_evidence_errors,
)
from jobpilot.core.assessment_service import RoleAssessmentMixin
from jobpilot.core.config import resolve_data_path
from jobpilot.core.dashboard_metrics import collect_dashboard_metrics
from jobpilot.core.outcome_service import OutcomeServiceMixin
from jobpilot.core.policy_config import get_policy
from jobpilot.core.target_review import review_state_for_job
from jobpilot.core.tracker_service import read_tracker_stats, read_tracker_status


def _configured_path(value: str) -> Path | None:
    return resolve_data_path(value) if value else None


def _history_warnings() -> tuple[str, ...]:
    policy = get_policy().application_evidence
    if not policy.fail_closed:
        return ()
    return configured_external_evidence_errors(
        employment_dir=_configured_path(policy.employment_dir),
        gmail_cache_path=_configured_path(policy.gmail_cache_path),
        gmail_cache_max_age_hours=policy.gmail_cache_max_age_hours,
    )


class JobPilotService(RoleAssessmentMixin, OutcomeServiceMixin):
    """Stable application layer used by every JobPilot transport."""

    def __init__(self, review_resolver: Callable[[object], str] = review_state_for_job):
        self._review_resolver = review_resolver

    def capabilities(self) -> AgentEnvelope:
        operations = [
            ("opportunities.list", "read_only", False),
            ("opportunities.refresh", "network_and_local_state", False),
            ("role.assess", "read_only", False),
            ("role.preflight", "read_only", True),
            ("dashboard.read", "read_only", False),
            ("outcome.record", "local_state", True),
        ]
        return AgentEnvelope(
            operation="capabilities.read",
            data={
                "service": "jobpilot",
                "transports": ["python", "http", "cli_json"],
                "operations": [
                    {
                        "name": name,
                        "side_effect": side_effect,
                        "human_confirmation_required": confirmation,
                    }
                    for name, side_effect, confirmation in operations
                ],
                "boundaries": {
                    "live_ats_fill": False,
                    "live_ats_submit": False,
                    "human_review_required": True,
                    "human_submit_required": True,
                },
            },
        )

    def list_opportunities(
        self,
        *,
        ready_only: bool = False,
        limit: int = 50,
    ) -> AgentEnvelope:
        jobs = queue_builder.load_queue()
        queue_builder.restrict_queue_for_incomplete_history(jobs)
        queue_builder.restrict_queue_for_action_provenance(jobs)
        return self._opportunity_envelope(
            "opportunities.list", jobs, ready_only=ready_only, limit=limit
        )

    def refresh_opportunities(
        self,
        *,
        ready_only: bool = False,
        limit: int = 50,
    ) -> AgentEnvelope:
        try:
            jobs = queue_builder.refresh_queue(limit=limit)
        except EvidenceSourceError as exc:
            raise AgentServiceError(
                "application_history_incomplete",
                "The opportunity refresh stopped because application history is incomplete.",
                "Run jobpilot gmail-sync with a fresh full-history export, then retry.",
                status_code=409,
            ) from exc
        result = self._opportunity_envelope(
            "opportunities.refresh", jobs, ready_only=ready_only, limit=limit
        )
        result.data["side_effect"] = "network_and_local_state"
        return result

    def _opportunity_envelope(
        self,
        operation: str,
        jobs: list[queue_builder.QueueJob],
        *,
        ready_only: bool,
        limit: int,
    ) -> AgentEnvelope:
        evaluated = [(job, self._review_resolver(job)) for job in jobs]
        view = [
            (job, review)
            for job, review in evaluated
            if not ready_only
            or (
                queue_builder.is_apply_ready(job)
                and review in {"unconfigured", "ready"}
            )
        ]
        items = [
            opportunity_payload(
                job,
                ready=(
                    queue_builder.is_apply_ready(job)
                    and review in {"unconfigured", "ready"}
                ),
                review_state=review,
            )
            for job, review in view[: max(0, limit)]
        ]
        return AgentEnvelope(
            operation=operation,
            data={"count": len(items), "ready_only": ready_only, "items": items},
            warnings=_history_warnings(),
        )

    def preflight(self, job_id: str) -> AgentEnvelope:
        jobs = queue_builder.load_queue()
        queue_builder.restrict_queue_for_incomplete_history(jobs)
        queue_builder.restrict_queue_for_action_provenance(jobs)
        job = next((item for item in jobs if item.id == job_id), None)
        if job is None:
            raise AgentServiceError(
                "opportunity_not_found",
                f"No queued opportunity has ID '{job_id}'.",
                "Refresh or list opportunities, then retry with a current ID.",
                status_code=404,
            )
        review = self._review_resolver(job)
        tracker_available, tracked_status = read_tracker_status(
            job.company, job.title, job.url
        )
        ready = (
            queue_builder.is_apply_ready(job)
            and queue_builder.has_current_action_provenance(job)
            and review in {"unconfigured", "ready"}
            and tracked_status is None
            and tracker_available
        )
        return AgentEnvelope(
            operation="role.preflight",
            data={
                "state": "apply_ready" if ready else "investigate",
                "may_prepare": ready,
                "tracked_status": tracked_status,
                "tracker_history_available": tracker_available,
                "opportunity": opportunity_payload(
                    job,
                    ready=ready,
                    review_state=review,
                ),
            },
            human_action_required=True,
            human_actions=(
                "verify_company",
                "review_evidence",
                "submit_application" if ready else "resolve_biggest_gap",
            ),
        )

    def dashboard(self) -> AgentEnvelope:
        jobs = queue_builder.load_queue()
        queue_builder.restrict_queue_for_incomplete_history(jobs)
        queue_builder.restrict_queue_for_action_provenance(jobs)
        return AgentEnvelope(
            operation="dashboard.read",
            data=collect_dashboard_metrics(jobs, tracker_stats=read_tracker_stats()),
        )
