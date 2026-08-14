"""Versioned HTTP adapter for the model-neutral agent service."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from jobpilot.core.agent_contract import AgentEnvelope, AgentServiceError
from jobpilot.core.outcome_service import OutcomeInput


class FakeService:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.failure: AgentServiceError | None = None

    def _result(self, operation: str, data: dict) -> AgentEnvelope:
        if self.failure:
            raise self.failure
        return AgentEnvelope(operation=operation, data=data)

    def capabilities(self) -> AgentEnvelope:
        return self._result("capabilities.read", {"service": "jobpilot"})

    def list_opportunities(self, *, ready_only: bool, limit: int) -> AgentEnvelope:
        self.calls.append(("list", ready_only, limit))
        return self._result("opportunities.list", {"items": [], "count": 0})

    def refresh_opportunities(self, *, ready_only: bool, limit: int) -> AgentEnvelope:
        self.calls.append(("refresh", ready_only, limit))
        return self._result("opportunities.refresh", {"items": [], "count": 0})

    def assess_role(self, *, title: str, job_description: str) -> AgentEnvelope:
        self.calls.append(("assess", title, job_description))
        return self._result("role.assess", {"decision": "investigate"})

    def preflight(self, job_id: str) -> AgentEnvelope:
        self.calls.append(("preflight", job_id))
        return self._result("role.preflight", {"may_prepare": False})

    def dashboard(self) -> AgentEnvelope:
        return self._result("dashboard.read", {"queue": {"roles": 2}})

    def record_outcome(self, request: OutcomeInput) -> AgentEnvelope:
        self.calls.append(("outcome", request))
        return self._result(
            "outcome.record", {"application": {"status": request.status}}
        )


def _client(monkeypatch) -> tuple[TestClient, FakeService]:
    import jobpilot.core.agent_api as agent_api

    service = FakeService()
    monkeypatch.setattr(agent_api, "get_service", lambda: service)
    app = FastAPI()
    app.include_router(agent_api.router)
    return TestClient(app), service


def test_capabilities_and_list_share_the_versioned_envelope(monkeypatch) -> None:
    client, service = _client(monkeypatch)

    capabilities = client.get("/api/agent/v1/capabilities")
    opportunities = client.get(
        "/api/agent/v1/opportunities", params={"ready_only": True, "limit": 12}
    )

    assert capabilities.status_code == 200
    assert capabilities.json()["contract_version"] == "jobpilot.agent/v1"
    assert opportunities.json()["operation"] == "opportunities.list"
    assert service.calls == [("list", True, 12)]


def test_refresh_and_assess_route_to_the_same_service(monkeypatch) -> None:
    client, service = _client(monkeypatch)

    refreshed = client.post(
        "/api/agent/v1/opportunities/refresh",
        params={"ready_only": False, "limit": 9},
    )
    assessed = client.post(
        "/api/agent/v1/assess",
        json={"title": "Engineer", "job_description": "Required: Python"},
    )

    assert refreshed.status_code == 200
    assert assessed.status_code == 200
    assert service.calls == [
        ("refresh", False, 9),
        ("assess", "Engineer", "Required: Python"),
    ]


def test_safe_service_error_has_a_plain_english_next_action(monkeypatch) -> None:
    client, service = _client(monkeypatch)
    service.failure = AgentServiceError(
        "missing_evidence",
        "The role does not have enough evidence.",
        "Refresh the role and review its evidence card.",
        status_code=409,
    )

    response = client.get("/api/agent/v1/preflight/role-1")

    assert response.status_code == 409
    assert response.json()["error"] == {
        "code": "missing_evidence",
        "message": "The role does not have enough evidence.",
        "action": "Refresh the role and review its evidence card.",
    }


def test_outcome_endpoint_requires_explicit_confirmation_field(monkeypatch) -> None:
    client, service = _client(monkeypatch)

    invalid = client.post(
        "/api/agent/v1/outcomes",
        json={"company": "Acme", "title": "Engineer", "status": "applied"},
    )
    valid = client.post(
        "/api/agent/v1/outcomes",
        json={
            "company": "Acme",
            "title": "Engineer",
            "status": "applied",
            "human_confirmed": True,
        },
    )

    assert invalid.status_code == 422
    assert valid.status_code == 200
    assert service.calls[0][0] == "outcome"
    assert service.calls[0][1].human_confirmed is True


def test_real_contract_refuses_an_unconfirmed_outcome() -> None:
    import jobpilot.core.agent_api as agent_api

    app = FastAPI()
    app.include_router(agent_api.router)

    response = TestClient(app).post(
        "/api/agent/v1/outcomes",
        json={
            "company": "Acme",
            "title": "Engineer",
            "status": "applied",
            "human_confirmed": False,
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "human_confirmation_required"


def test_openapi_publishes_success_and_safe_error_schemas() -> None:
    import jobpilot.core.agent_api as agent_api

    app = FastAPI()
    app.include_router(agent_api.router)
    responses = app.openapi()["paths"]["/api/agent/v1/preflight/{job_id}"]["get"][
        "responses"
    ]

    assert responses["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/AgentSuccessResponse"
    )
    assert responses["404"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/AgentErrorResponse"
    )
