"""Versioned, transport-neutral response contract for JobPilot agents."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any

CONTRACT_VERSION = "jobpilot.agent/v1"


class AgentServiceError(RuntimeError):
    """A safe error that tells an agent what failed and what to do next."""

    def __init__(
        self,
        code: str,
        message: str,
        action: str,
        *,
        status_code: int = 400,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.action = action
        self.status_code = status_code

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": CONTRACT_VERSION,
            "ok": False,
            "error": {
                "code": self.code,
                "message": self.message,
                "action": self.action,
            },
        }


def json_safe(value: Any) -> Any:
    """Convert domain values to deterministic JSON-compatible primitives."""
    if is_dataclass(value):
        return json_safe(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if hasattr(value, "__dict__"):
        return json_safe(vars(value))
    return value


@dataclass(frozen=True)
class AgentEnvelope:
    """One response shape shared by Python, HTTP, and CLI transports."""

    operation: str
    data: dict[str, Any]
    warnings: tuple[str, ...] = ()
    human_action_required: bool = False
    human_actions: tuple[str, ...] = ()
    generated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        generated = self.generated_at or datetime.now(UTC).isoformat()
        return {
            "contract_version": CONTRACT_VERSION,
            "operation": self.operation,
            "ok": True,
            "generated_at": generated,
            "data": json_safe(self.data),
            "warnings": list(self.warnings),
            "human_action_required": self.human_action_required,
            "human_actions": list(self.human_actions),
        }


def opportunity_payload(
    job: Any,
    *,
    ready: bool,
    review_state: str,
) -> dict[str, Any]:
    """Add transport-neutral action semantics to a queue-domain record."""
    review_ready = review_state in {"unconfigured", "ready"}
    state = (
        "apply_ready"
        if ready
        else "target_review_required"
        if not review_ready
        else getattr(job, "decision", "investigate")
    )
    human_actions = ["verify_company", "review_evidence"]
    human_actions.append(
        "submit_application"
        if ready
        else "complete_target_review"
        if not review_ready
        else "resolve_biggest_gap"
    )
    payload = json_safe(job)
    payload["target_review_state"] = review_state
    payload["claim_state"] = review_state  # Compatibility alias for existing agents.
    payload["action_readiness"] = {
        "state": state,
        "may_prepare": ready,
        "may_submit_automatically": False,
    }
    payload["human_actions"] = human_actions
    return payload
