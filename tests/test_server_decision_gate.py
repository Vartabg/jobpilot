"""Application actions fail closed with a useful next step."""

import importlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import jobpilot.core.server as server
from jobpilot.core.queue_builder import QueueJob


@pytest.fixture(autouse=True)
def _reset_server_access():
    server.configure_server_access("127.0.0.1")
    yield
    server.configure_server_access("127.0.0.1")


def _job(**overrides) -> QueueJob:
    values = {
        "id": "role-1",
        "company": "Acme",
        "title": "Customer Engineer",
        "url": "https://jobs.example.test/acme/1",
        "location": "Austin, Texas",
        "portal": "greenhouse",
        "track": "both",
        "fit_score": 78,
        "keywords": ["customer"],
        "status": "queued",
        "decision": "investigate",
        "assessment_status": "unscorable",
        "legitimacy_state": "hold",
        "evidence_grade": "F",
        "verified_at": "",
        "biggest_gap": "Full job description unavailable",
    }
    values.update(overrides)
    return QueueJob(**values)


def test_direct_asgi_import_denies_until_loopback_is_configured() -> None:
    imported_server = importlib.reload(server)
    client = TestClient(imported_server.app)

    assert imported_server.app.state.remote_auth_required is True
    assert imported_server.app.state.remote_token is None
    assert client.get("/api/latest-draft").status_code == 401

    assert imported_server.configure_server_access("127.0.0.1") is False
    assert client.get("/api/latest-draft").status_code == 200


def test_opened_action_explains_why_investigation_is_required(monkeypatch) -> None:
    monkeypatch.setattr(server, "get_job", lambda _job_id: _job())
    client = TestClient(server.app)

    response = client.post("/api/job/role-1/opened")

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "cannot be treated as apply-ready" in detail["message"]
    assert detail["next_action"] == "Refresh the source and review the evidence card."


def test_current_recommendation_can_enter_application_flow(monkeypatch) -> None:
    ready = _job(
        decision="apply_now",
        assessment_status="assessed",
        legitimacy_state="recommend",
        evidence_grade="A",
        verified_at=datetime.now(UTC).isoformat(),
    )
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(server, "get_job", lambda _job_id: ready)
    monkeypatch.setattr(server, "has_current_action_provenance", lambda _job: True)
    monkeypatch.setattr(
        server,
        "update_job_status",
        lambda job_id, status: calls.append((job_id, status)) or True,
    )
    client = TestClient(server.app)

    response = client.post("/api/job/role-1/opened")

    assert response.status_code == 200
    assert calls == [("role-1", "viewing")]


def test_orphaned_cached_recommendation_cannot_enter_application_flow(
    monkeypatch,
) -> None:
    ready = _job(
        decision="apply_now",
        assessment_status="assessed",
        legitimacy_state="recommend",
        evidence_grade="A",
        verified_at=datetime.now(UTC).isoformat(),
    )
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(server, "get_job", lambda _job_id: ready)
    monkeypatch.setattr(server, "has_current_action_provenance", lambda _job: False)
    monkeypatch.setattr(
        server,
        "update_job_status",
        lambda job_id, status: calls.append((job_id, status)) or True,
    )

    response = TestClient(server.app).post("/api/job/role-1/opened")

    assert response.status_code == 409
    assert calls == []


def test_queue_refresh_uses_serialized_refresh_operation(monkeypatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr(
        server,
        "refresh_queue",
        lambda *, limit: calls.append(limit) or [_job(), _job(id="role-2")],
    )

    response = TestClient(server.app).post("/api/queue/refresh")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "count": 2}
    assert calls == [100]


def test_dashboard_metrics_endpoint_is_read_only_and_privacy_safe(monkeypatch) -> None:
    job = _job(relocation_state="offered")
    monkeypatch.setattr(server, "load_queue", lambda: [job])
    monkeypatch.setattr(
        server,
        "restrict_queue_for_incomplete_history",
        lambda _jobs: 0,
    )
    monkeypatch.setattr(
        server,
        "restrict_queue_for_action_provenance",
        lambda _jobs: 0,
    )
    monkeypatch.setattr(
        server,
        "get_application_tracker",
        lambda: SimpleNamespace(get_stats=lambda: {"total": 7}),
    )
    calls = []

    def collect(jobs, *, tracker_stats):
        calls.append((jobs, tracker_stats))
        return {
            "queue": {"supported_relocation": 1},
            "relocation": {"offered": 1},
            "source_health": {"healthy": 4, "total": 5},
            "outcomes": {"decided": 2},
            "tracker": tracker_stats,
        }

    monkeypatch.setattr(server, "collect_dashboard_metrics", collect)

    response = TestClient(server.app).get("/api/dashboard")

    assert response.status_code == 200
    assert response.json()["queue"]["supported_relocation"] == 1
    assert response.json()["tracker"] == {"total": 7}
    assert calls == [([job], {"total": 7})]


def test_live_form_fill_surfaces_are_retired() -> None:
    client = TestClient(server.app)

    install = client.get("/install")
    bookmarklet = client.get("/api/bookmarklet")

    assert install.status_code == 410
    assert bookmarklet.status_code == 410
    assert "paste" in install.json()["detail"]["next_action"].lower()
    assert "submit by hand" in bookmarklet.json()["detail"]["next_action"].lower()


def test_server_contains_no_live_form_fill_payload() -> None:
    source = (Path(__file__).parents[1] / "core" / "server.py").read_text()

    assert "BOOKMARKLET_TEMPLATE" not in source
    assert "__PROFILE__" not in source
    assert "Fill Application" not in source


def test_latest_draft_api_never_exposes_local_paths(monkeypatch, tmp_path) -> None:
    project = tmp_path / "project"
    manifest = project / "data" / "resumes" / "latest_draft.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({
        "markdown_path": "/Users/example/private/resume.md",
        "html_path": "/Users/example/private/resume.html",
        "pdf_path": "/Users/example/private/resume.pdf",
        "title": "Implementation Engineer",
        "company": "Acme",
        "recommendation": "Review",
        "matched_skills": ["Python"],
    }))
    monkeypatch.setattr(server, "PROJECT_ROOT", project)

    response = TestClient(server.app).get("/api/latest-draft")
    body = response.json()

    assert response.status_code == 200
    assert body["company"] == "Acme"
    assert all(not key.endswith("_path") for key in body)
    assert "/Users/" not in response.text


@pytest.mark.parametrize(
    "host",
    ["0.0.0.0", "::", "192.168.1.20", "10.0.0.8", "100.1.2.3", "8.8.8.8"],
)
def test_server_rejects_wildcard_and_lan_binds(host: str) -> None:
    with pytest.raises(ValueError):
        server.configure_server_access(host, "a" * 40)
    assert server.app.state.remote_auth_required is True
    assert server.app.state.remote_token is None


@pytest.mark.parametrize("token", [None, "short", "x" * 31, "x" * 31 + "!"])
def test_remote_bind_requires_long_url_safe_token(token: str | None) -> None:
    with pytest.raises(ValueError, match="JOBPILOT_REMOTE_TOKEN"):
        server.configure_server_access("100.64.10.20", token)


def test_remote_dashboard_authenticates_html_and_api_requests() -> None:
    token = "remote-token_abcdefghijklmnopqrstuvwxyz012345"
    assert server.configure_server_access("100.64.10.20", token) is True
    client = TestClient(server.app)

    assert client.get("/").status_code == 401
    assert client.get("/api/latest-draft").status_code == 401
    assert client.post("/api/queue/reconcile").status_code == 401
    dashboard = client.get("/", params={"token": token})
    draft = client.get(
        "/api/latest-draft",
        headers={server.REMOTE_TOKEN_HEADER: token},
    )
    assert dashboard.status_code == 200
    assert draft.status_code == 200
    assert dashboard.headers["Cache-Control"] == "no-store"
    assert draft.headers["Cache-Control"] == "no-store"


def test_loopback_rejects_cross_site_origin_and_dns_rebinding() -> None:
    client = TestClient(server.app, base_url="http://127.0.0.1:8767")

    rebound = client.get("/api/profile", headers={"Host": "evil.example"})
    cross_site = client.post(
        "/api/queue/reconcile",
        headers={"Origin": "https://evil.example"},
    )
    same_origin = client.get(
        "/api/profile",
        headers={"Origin": "http://127.0.0.1:8767"},
    )

    assert rebound.status_code == 403
    assert cross_site.status_code == 403
    assert same_origin.status_code == 200
    assert same_origin.headers["Cache-Control"] == "no-store"


def test_loopback_rejects_cross_site_fetch_metadata_without_origin(monkeypatch) -> None:
    called = False

    def load():
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(server, "load_queue", load)
    client = TestClient(server.app, base_url="http://127.0.0.1:8767")

    response = client.get(
        "/api/queue",
        headers={
            "Sec-Fetch-Site": "cross-site",
            "Sec-Fetch-Mode": "no-cors",
            "Sec-Fetch-Dest": "image",
        },
    )

    assert response.status_code == 403
    assert called is False


def test_queue_get_is_read_only(monkeypatch) -> None:
    monkeypatch.setattr(server, "load_queue", lambda: [_job()])
    reconcile = pytest.fail
    monkeypatch.setattr(server, "reconcile_queue_with_tracker", reconcile)
    monkeypatch.setattr(
        server,
        "restrict_queue_for_incomplete_history",
        lambda _jobs: 0,
    )
    monkeypatch.setattr(
        server,
        "restrict_queue_for_action_provenance",
        lambda _jobs: 0,
    )

    response = TestClient(server.app).get("/api/queue")

    assert response.status_code == 200
    assert response.json()[0]["id"] == "role-1"


def test_queue_get_hides_cached_apply_action_when_history_is_incomplete(
    monkeypatch,
) -> None:
    job = _job()
    job.decision = "apply_now"
    job.assessment_status = "assessed"
    job.legitimacy_state = "recommend"
    monkeypatch.setattr(server, "load_queue", lambda: [job])

    def restrict(jobs):
        jobs[0].decision = "investigate"
        jobs[0].suppression_reason = "Application history is incomplete."
        return 1

    monkeypatch.setattr(server, "restrict_queue_for_incomplete_history", restrict)

    response = TestClient(server.app).get("/api/queue")

    assert response.status_code == 200
    assert response.json()[0]["decision"] == "investigate"
    assert "history is incomplete" in response.json()[0]["suppression_reason"]


def test_queue_get_hides_cached_apply_action_without_ledger_provenance(
    monkeypatch,
) -> None:
    job = _job()
    job.decision = "apply_now"
    job.assessment_status = "assessed"
    job.legitimacy_state = "recommend"
    job.verified_at = datetime.now(UTC).isoformat()
    monkeypatch.setattr(server, "load_queue", lambda: [job])
    monkeypatch.setattr(
        server,
        "restrict_queue_for_incomplete_history",
        lambda _jobs: 0,
    )

    def restrict(jobs):
        jobs[0].decision = "investigate"
        jobs[0].suppression_reason = "Current ledger corroboration is unavailable."
        return 1

    monkeypatch.setattr(
        server,
        "restrict_queue_for_action_provenance",
        restrict,
    )

    response = TestClient(server.app).get("/api/queue")

    assert response.status_code == 200
    assert response.json()[0]["decision"] == "investigate"
    assert "ledger corroboration" in response.json()[0]["suppression_reason"]


def test_remote_server_disables_access_logging(monkeypatch, capsys) -> None:
    token = "remote-token_abcdefghijklmnopqrstuvwxyz012345"
    calls: list[dict] = []
    monkeypatch.setenv(server.REMOTE_TOKEN_ENV, token)
    monkeypatch.setitem(
        sys.modules,
        "uvicorn",
        SimpleNamespace(run=lambda *_args, **kwargs: calls.append(kwargs)),
    )

    server.run_server("100.64.10.20", 8767)

    assert calls == [{
        "host": "100.64.10.20",
        "port": 8767,
        "log_level": "info",
        "access_log": False,
    }]
    assert token not in capsys.readouterr().out


def test_dashboard_removes_bootstrap_token_and_authenticates_api_calls() -> None:
    source = (Path(__file__).parents[1] / "ui" / "dashboard.html").read_text()

    assert "sessionStorage.setItem('jobpilotRemoteToken', queryToken)" in source
    assert "url.searchParams.delete('token')" in source
    assert "history.replaceState(history.state" in source
    assert "headers.set('X-JobPilot-Token', remoteToken)" in source
