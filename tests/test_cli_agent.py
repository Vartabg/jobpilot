"""JSON-only CLI adapter for agents."""

import json

from typer.testing import CliRunner

import jobpilot.cli_agent as cli_agent
from jobpilot.cli import app as root_app
from jobpilot.core.agent_contract import AgentEnvelope, AgentServiceError

runner = CliRunner()


class FakeService:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.failure: AgentServiceError | None = None

    def _result(self, operation: str) -> AgentEnvelope:
        if self.failure:
            raise self.failure
        return AgentEnvelope(operation=operation, data={"service": "jobpilot"})

    def capabilities(self) -> AgentEnvelope:
        return self._result("capabilities.read")

    def list_opportunities(self, *, ready_only: bool, limit: int) -> AgentEnvelope:
        self.calls.append(("list", ready_only, limit))
        return self._result("opportunities.list")

    def refresh_opportunities(self, *, ready_only: bool, limit: int) -> AgentEnvelope:
        self.calls.append(("refresh", ready_only, limit))
        return self._result("opportunities.refresh")

    def assess_role(self, *, title: str, job_description: str) -> AgentEnvelope:
        self.calls.append(("assess", title, job_description))
        return self._result("role.assess")

    def preflight(self, job_id: str) -> AgentEnvelope:
        self.calls.append(("preflight", job_id))
        return self._result("role.preflight")


def test_capabilities_is_clean_json(monkeypatch) -> None:
    service = FakeService()
    monkeypatch.setattr(cli_agent, "get_service", lambda: service)

    result = runner.invoke(cli_agent.agent_app, ["capabilities"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["contract_version"] == "jobpilot.agent/v1"
    assert payload["operation"] == "capabilities.read"


def test_agent_contract_is_registered_on_the_installed_root_cli(monkeypatch) -> None:
    service = FakeService()
    monkeypatch.setattr(cli_agent, "get_service", lambda: service)

    result = runner.invoke(root_app, ["agent", "capabilities"])

    assert result.exit_code == 0
    assert json.loads(result.stdout)["operation"] == "capabilities.read"


def test_opportunities_supports_read_only_and_refresh_modes(monkeypatch) -> None:
    service = FakeService()
    monkeypatch.setattr(cli_agent, "get_service", lambda: service)

    listed = runner.invoke(
        cli_agent.agent_app, ["opportunities", "--ready-only", "--limit", "7"]
    )
    refreshed = runner.invoke(
        cli_agent.agent_app, ["opportunities", "--refresh", "--limit", "9"]
    )

    assert listed.exit_code == refreshed.exit_code == 0
    assert service.calls == [("list", True, 7), ("refresh", False, 9)]


def test_assess_reads_a_job_description_file(monkeypatch, tmp_path) -> None:
    service = FakeService()
    monkeypatch.setattr(cli_agent, "get_service", lambda: service)
    jd = tmp_path / "role.txt"
    jd.write_text("Required qualifications: Python")

    result = runner.invoke(
        cli_agent.agent_app,
        ["assess", str(jd), "--title", "Engineer"],
    )

    assert result.exit_code == 0
    assert service.calls == [("assess", "Engineer", "Required qualifications: Python")]


def test_service_error_is_json_without_a_traceback(monkeypatch) -> None:
    service = FakeService()
    service.failure = AgentServiceError(
        "not_ready",
        "The role is not ready.",
        "Review the evidence and retry.",
        status_code=409,
    )
    monkeypatch.setattr(cli_agent, "get_service", lambda: service)

    result = runner.invoke(cli_agent.agent_app, ["preflight", "role-1"])

    assert result.exit_code == 2
    assert (
        json.loads(result.stdout)["error"]["action"] == "Review the evidence and retry."
    )
    assert "Traceback" not in result.stdout
