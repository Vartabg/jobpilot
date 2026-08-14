"""Truth-bounded role assessment operation for the agent facade."""

from __future__ import annotations

from jobpilot.core.agent_contract import AgentEnvelope, AgentServiceError, json_safe
from jobpilot.core.config import DATA_DIR
from jobpilot.core.profile_store import get_profile_store
from jobpilot.core.role_decision import RoleDecisionEngine


class RoleAssessmentMixin:
    """Keep role assessment independent from queue and outcome orchestration."""

    def assess_role(self, *, title: str, job_description: str) -> AgentEnvelope:
        if not job_description.strip():
            raise AgentServiceError(
                "missing_job_description",
                "The role cannot be assessed without its full job description.",
                "Provide the full job description text and retry.",
            )
        accounts = DATA_DIR / "true_accounts.json"
        result = RoleDecisionEngine().assess(
            title=title.strip(),
            jd_text=job_description,
            profile=get_profile_store().load(),
            accounts_path=accounts if accounts.is_file() else None,
        )
        return AgentEnvelope(operation="role.assess", data=json_safe(result))
