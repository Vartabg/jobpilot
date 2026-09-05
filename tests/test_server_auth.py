"""Authentication contracts shared by both dashboard servers."""

import pytest
from fastapi.testclient import TestClient

from jobpilot.core import config, server
from jobpilot.core.profile_store import ProfileStore, UserProfile
from jobpilot.gigs import server as gigs_server

TEST_TOKEN = "test-only-dashboard-access"
REQUEST_HEADERS = {"X-JobPilot-Request": "1"}


@pytest.fixture(params=[server, gigs_server], ids=["dashboard", "swipe"])
def client(request, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SERVER_AUTH_TOKEN", TEST_TOKEN, raising=False)
    store = ProfileStore(data_dir=tmp_path)
    store.save(UserProfile(first_name="Test", email="test@invalid.test"))
    monkeypatch.setattr(server, "get_profile_store", lambda: store)
    monkeypatch.setattr(gigs_server.swipe, "session_meta", lambda: {"test": True})
    monkeypatch.setattr(gigs_server.swipe, "build_queue", lambda **kwargs: [])
    with TestClient(request.param.app, base_url="https://testserver") as result:
        result.protected_path = "/api/profile" if request.param is server else "/api/meta"
        yield result


@pytest.mark.parametrize("path", ["/", "/install", "/api/queue", "/docs"])
def test_anonymous_requests_cannot_get_credentials_or_data(client, path):
    response = client.get(path)
    assert response.status_code == 401
    assert TEST_TOKEN not in response.text
    assert "action" in response.json()


def test_private_access_link_sets_session_and_removes_token(client):
    response = client.get("/", params={"token": TEST_TOKEN}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "secure" in cookie
    shell = client.get("/")
    assert shell.status_code == 200
    assert TEST_TOKEN not in shell.text
    assert "__AUTH_TOKEN__" not in shell.text
    assert shell.headers["cache-control"] == "no-store"
    assert shell.headers["referrer-policy"] == "no-referrer"
    assert client.get(client.protected_path, headers=REQUEST_HEADERS).status_code == 200


def test_session_api_requires_custom_header(client):
    client.get("/", params={"token": TEST_TOKEN})
    assert client.get(client.protected_path).status_code == 403
    assert client.post("/api/queue/refresh", json={}).status_code == 403


def test_explicit_token_authentication_and_invalid_tokens(client):
    assert client.get(client.protected_path, headers={"X-JobPilot-Token": TEST_TOKEN}).status_code == 200
    assert client.get(client.protected_path, headers={"X-JobPilot-Token": "wrong"}).status_code == 401
    assert client.get("/", params={"token": "wrong"}).status_code == 401


def test_token_rotation_invalidates_existing_session(client, monkeypatch):
    client.get("/", params={"token": TEST_TOKEN})
    monkeypatch.setattr(config, "SERVER_AUTH_TOKEN", "replacement-test-only")
    assert client.get(client.protected_path, headers=REQUEST_HEADERS).status_code == 401


def test_dashboard_fill_button_works_after_sign_in(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SERVER_AUTH_TOKEN", TEST_TOKEN, raising=False)
    store = ProfileStore(data_dir=tmp_path)
    monkeypatch.setattr(server, "get_profile_store", lambda: store)
    with TestClient(server.app) as client:
        page = client.get("/", params={"token": TEST_TOKEN})
        assert 'href="/install"' in page.text
        install = client.get("/install")
        assert install.status_code == 200
        assert "Install the Fill Button" in install.text


def test_queue_corruption_returns_actionable_error_on_every_request(monkeypatch, tmp_path):
    from jobpilot.core import queue_builder

    monkeypatch.setattr(config, "SERVER_AUTH_TOKEN", TEST_TOKEN)
    path = tmp_path / "queue.json"
    path.write_text("broken")
    monkeypatch.setattr(queue_builder, "QUEUE_PATH", path)
    with TestClient(server.app, headers={"X-JobPilot-Token": TEST_TOKEN}) as client:
        for _ in range(2):
            response = client.get("/api/queue")
            assert response.status_code == 503
            assert "recovery" in response.json()["action"]
            assert path.read_text() == "broken"


def test_serve_requires_deliberate_remote_bind(monkeypatch):
    from typer.testing import CliRunner

    from jobpilot import cli

    calls = []
    monkeypatch.setattr(server, "run_server", lambda **kwargs: calls.append(kwargs))
    runner = CliRunner()
    denied = runner.invoke(cli.app, ["serve", "--host", "100.64.0.1"])
    assert denied.exit_code == 1
    assert not calls
    allowed = runner.invoke(cli.app, ["serve", "--host", "100.64.0.1", "--allow-lan"])
    assert allowed.exit_code == 0
    assert calls == [{"host": "100.64.0.1", "port": None}]
