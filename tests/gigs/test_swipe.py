"""Swipe engine + mobile server API (no network: build_queue is stubbed)."""

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from jobpilot.gigs import server
from jobpilot.gigs.core import swipe
from jobpilot.gigs.core.models import Gig


@pytest.fixture(autouse=True)
def _reset_server_access():
    server.configure_server_access("127.0.0.1")
    yield
    server.configure_server_access("127.0.0.1")


def _gig(**kw):
    base = dict(id="hn-1", source="hn", title="Senior AI Engineer", company="Acme",
                description="rag agentic python", url="https://post.test/1",
                apply_url="mailto:jobs@acme.test", fit_score=95, location="Remote",
                salary_min=160000, salary_max=200000)
    base.update(kw)
    return Gig(**base)


def test_direct_asgi_import_denies_until_loopback_is_configured():
    imported_server = importlib.reload(server)
    client = TestClient(imported_server.app)

    assert imported_server.app.state.remote_auth_required is True
    assert imported_server.app.state.remote_token is None
    assert client.get("/").status_code == 401

    assert imported_server.configure_server_access("127.0.0.1") is False
    assert client.get("/").status_code == 200


def test_card_has_everything_the_phone_needs():
    c = swipe.card(_gig())
    for key in ("id", "company", "role", "score", "pay", "location",
                "offer", "subject", "draft", "apply_target", "is_mailto", "crib"):
        assert key in c
    assert c["is_mailto"] is True
    assert c["apply_target"].startswith("mailto:jobs@acme.test?subject=")
    assert "example.com" not in c["draft"]
    crib = c["crib"]
    assert "salary_paste" in crib and "relocate" in crib
    assert "identity" in crib and "ats_answers" in crib
    assert isinstance(crib["ats_answers"], list) and crib["ats_answers"]


def test_session_meta_has_criteria_pills():
    meta = swipe.session_meta()
    assert "criteria_pills" in meta
    assert isinstance(meta["criteria_pills"], list)
    assert meta.get("on_demand") is True


def test_card_non_mailto_uses_apply_url():
    c = swipe.card(_gig(apply_url="https://boards.greenhouse.io/acme/jobs/1"))
    assert c["is_mailto"] is False
    assert c["apply_target"].startswith("https://boards.greenhouse.io")


@pytest.fixture
def client(monkeypatch, tmp_path):
    # No network: stub the scan + the pipeline-writing record/undo.
    monkeypatch.setattr(swipe, "build_queue", lambda **k: [_gig(), _gig(id="hn-2", company="Globex")])
    recorded, undone = [], []
    monkeypatch.setattr(swipe, "record_decision",
                        lambda gig, action, reason="": recorded.append((gig.id, action)) or "drafted")
    monkeypatch.setattr(swipe, "undo_decision", lambda gig: undone.append(gig.id))
    server._GIGS.clear()
    c = TestClient(server.app)
    c._recorded, c._undone = recorded, undone
    return c


def test_queue_endpoint_returns_cards(client):
    r = client.post("/api/queue/refresh")
    assert r.status_code == 200
    data = r.json()
    assert data["count"] == 2
    assert {c["company"] for c in data["cards"]} == {"Acme", "Globex"}
    assert "meta" in data and "criteria_pills" in data["meta"]
    assert data["cards"][0].get("crib")


def test_meta_endpoint_no_scan(client):
    r = client.get("/api/meta")
    assert r.status_code == 200
    body = r.json()
    assert "criteria_pills" in body
    assert "resumes" in body


def test_decision_records_and_keeps_gig_for_undo(client):
    client.post("/api/queue/refresh")  # populate
    r = client.post("/api/decision", json={"id": "hn-1", "action": "apply"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["status"] == "drafted"
    assert client._recorded == [("hn-1", "apply")]
    # gig is kept in the session so it can be undone
    u = client.post("/api/undo", json={"id": "hn-1"})
    assert u.status_code == 200 and u.json()["ok"] is True
    assert client._undone == ["hn-1"]


def test_decision_rejects_bad_action(client):
    client.post("/api/queue/refresh")
    r = client.post("/api/decision", json={"id": "hn-1", "action": "maybe"})
    assert r.status_code == 422  # Literal['apply','pass'] enforced


def test_decision_unknown_id_404(client):
    client.post("/api/queue/refresh")
    r = client.post("/api/decision", json={"id": "nope", "action": "pass"})
    assert r.status_code == 404


def test_index_serves_the_mobile_page(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "GigPilot" in r.text and "Get jobs" in r.text
    assert "Crib" in r.text or "crib" in r.text
    assert "/api/meta" in r.text


@pytest.mark.parametrize(
    "host",
    ["0.0.0.0", "::", "192.168.1.20", "10.0.0.8", "100.1.2.3", "8.8.8.8"],
)
def test_swipe_server_rejects_wildcard_and_lan_binds(host):
    with pytest.raises(ValueError):
        server.configure_server_access(host, "a" * 40)
    assert server.app.state.remote_auth_required is True
    assert server.app.state.remote_token is None


def test_remote_swipe_requires_token_for_html_reads_and_writes(client):
    token = "remote-token_abcdefghijklmnopqrstuvwxyz012345"
    assert server.configure_server_access("100.64.10.20", token) is True

    assert client.get("/").status_code == 401
    assert client.get("/api/meta").status_code == 401
    assert client.post("/api/undo", json={"id": "hn-1"}).status_code == 401
    index = client.get("/", params={"token": token})
    meta = client.get(
        "/api/meta",
        headers={server.REMOTE_TOKEN_HEADER: token},
    )
    assert index.status_code == 200
    assert meta.status_code == 200
    assert index.headers["Cache-Control"] == "no-store"
    assert meta.headers["Cache-Control"] == "no-store"


def test_loopback_swipe_rejects_cross_site_origin_and_dns_rebinding() -> None:
    client = TestClient(server.app, base_url="http://127.0.0.1:8799")

    rebound = client.get("/api/meta", headers={"Host": "evil.example"})
    cross_site = client.post(
        "/api/undo",
        json={"id": "missing"},
        headers={"Origin": "https://evil.example"},
    )
    same_origin = client.get(
        "/api/meta",
        headers={"Origin": "http://127.0.0.1:8799"},
    )

    assert rebound.status_code == 403
    assert cross_site.status_code == 403
    assert same_origin.status_code == 200
    assert same_origin.headers["Cache-Control"] == "no-store"


def test_cross_site_fetch_metadata_cannot_trigger_gig_scan(monkeypatch) -> None:
    called = False

    def build_queue(**_kwargs):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(swipe, "build_queue", build_queue)
    client = TestClient(server.app, base_url="http://127.0.0.1:8799")

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


def test_gig_queue_get_is_read_only_and_refresh_is_post(monkeypatch) -> None:
    calls: list[bool] = []
    monkeypatch.setattr(
        swipe,
        "build_queue",
        lambda **_kwargs: calls.append(True) or [_gig()],
    )
    server._GIGS.clear()
    client = TestClient(server.app)

    assert client.get("/api/queue").json()["count"] == 0
    assert calls == []
    assert client.post("/api/queue/refresh").json()["count"] == 1
    assert calls == [True]


def test_swipe_remote_server_disables_access_logging(monkeypatch, capsys):
    token = "remote-token_abcdefghijklmnopqrstuvwxyz012345"
    calls = []
    qr_urls = []
    monkeypatch.setenv(server.REMOTE_TOKEN_ENV, token)
    monkeypatch.setattr(server, "_tailscale_ip", lambda: "100.64.10.20")
    monkeypatch.setattr(server, "_print_qr", qr_urls.append)
    monkeypatch.setitem(
        sys.modules,
        "uvicorn",
        SimpleNamespace(run=lambda *_args, **kwargs: calls.append(kwargs)),
    )

    server.run_server("100.64.10.20", 8799)

    assert calls == [{
        "host": "100.64.10.20",
        "port": 8799,
        "log_level": "info",
        "access_log": False,
    }]
    assert qr_urls == [f"http://100.64.10.20:8799/?token={token}"]
    assert token not in capsys.readouterr().out


def test_swipe_page_removes_bootstrap_token_and_authenticates_every_api_call():
    source = (Path(__file__).parents[2] / "gigs" / "swipe.html").read_text()

    assert 'sessionStorage.setItem("jobpilotRemoteToken", queryToken)' in source
    assert 'url.searchParams.delete("token")' in source
    assert 'history.replaceState(history.state' in source
    assert 'headers.set("X-JobPilot-Token", remoteToken)' in source
    assert source.count("remoteFetch(") == 6
    assert source.count("fetch(") == 1


# --- decision persistence (the showstopper: status must reach pipeline.md) ---

def _seed_pipeline_with_new(gig_id: str):
    from jobpilot.gigs.core import pipeline
    from jobpilot.gigs.core.pipeline import Row
    pipeline.PIPELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    pipeline.PIPELINE_PATH.unlink(missing_ok=True)
    pipeline.write([Row(status="new", company="Acme", role="Senior AI Engineer", gig_id=gig_id)])


def test_apply_swipe_persists_drafted_until_human_confirms_sent():
    from jobpilot.gigs.core import pipeline, swipe
    _seed_pipeline_with_new("sw-apply")
    assert swipe.record_decision(_gig(id="sw-apply"), "apply") == "drafted"
    rows = {r.gig_id: r for r in pipeline.parse()}
    assert rows["sw-apply"].status == "drafted"


def test_pass_swipe_persists_passed_with_reason():
    from jobpilot.gigs.core import pipeline, swipe
    _seed_pipeline_with_new("sw-pass")
    assert swipe.record_decision(_gig(id="sw-pass"), "pass", "low-pay") == "passed"
    rows = {r.gig_id: r for r in pipeline.parse()}
    assert rows["sw-pass"].status == "passed"
    assert "pass:low-pay" in rows["sw-pass"].notes


def test_refused_write_raises_and_does_not_mark_seen(monkeypatch):
    from jobpilot.gigs.core import pipeline, swipe
    _seed_pipeline_with_new("sw-refused")
    monkeypatch.setattr(pipeline, "write",
                        lambda rows, **k: pipeline.WriteResult(pipeline.PIPELINE_PATH, refused=True))
    seen: list[str] = []
    monkeypatch.setattr(swipe, "mark_seen", lambda ids: seen.extend(ids))
    with pytest.raises(RuntimeError):
        swipe.record_decision(_gig(id="sw-refused"), "apply")
    assert seen == []  # a refused write must not mark the gig seen (would lose it)
