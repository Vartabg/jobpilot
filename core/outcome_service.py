"""Confirmed-outcome persistence shared by agent transports."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from jobpilot.core.agent_contract import AgentEnvelope, AgentServiceError
from jobpilot.core.application_tracker import get_application_tracker
from jobpilot.core.opportunity_ledger import EVENT_TYPES, OpportunityLedger
from jobpilot.core.queue_builder import reconcile_queue_with_tracker


@dataclass(frozen=True)
class OutcomeInput:
    """A human-observed funnel outcome supplied through any transport."""

    company: str
    title: str
    status: str
    human_confirmed: bool
    url: str = ""
    occurred_at: str | None = None
    acquisition_channel: str = "unknown"
    source: str = "agent-contract"


def record_confirmed_outcome(request: OutcomeInput) -> dict[str, Any]:
    """Persist one explicit outcome to tracker and evidence ledger."""
    tracker = get_application_tracker()
    row = tracker.log_application(
        company=request.company,
        title=request.title,
        url=request.url,
        status=request.status,
        applied_at=request.occurred_at,
        source=request.source,
    )
    event = {"submitted": "applied", "abandoned": "withdrawn"}.get(
        row.status, row.status
    )
    if event in EVENT_TYPES:
        ledger = OpportunityLedger()
        try:
            opportunity_id = ledger.upsert_opportunity(
                row.company,
                row.job_title,
                canonical_url=row.job_url,
            )
            ledger.append_event(
                opportunity_id,
                event,
                row.applied_at,
                request.source,
                {
                    "acquisition_channel": (
                        request.acquisition_channel.strip().lower() or "unknown"
                    )
                },
            )
        finally:
            ledger.close()
    tracker_total = tracker.get_stats()["total"]
    changed, total = reconcile_queue_with_tracker()
    return {
        "application": asdict(row),
        "queue_changed": changed,
        "queue_total": total,
        "tracker_total": tracker_total,
    }


class OutcomeServiceMixin:
    """Outcome operation separated from read-only search orchestration."""

    def record_outcome(self, request: OutcomeInput) -> AgentEnvelope:
        if not request.human_confirmed:
            raise AgentServiceError(
                "human_confirmation_required",
                "JobPilot will not infer or record an application outcome.",
                "Confirm the real human-observed outcome and retry.",
            )
        try:
            data = record_confirmed_outcome(request)
        except ValueError as exc:
            raise AgentServiceError(
                "invalid_outcome",
                "The confirmed outcome could not be recorded.",
                str(exc),
            ) from exc
        return AgentEnvelope(
            operation="outcome.record",
            data=data,
            human_action_required=True,
            human_actions=("confirm_recorded_outcome",),
        )
