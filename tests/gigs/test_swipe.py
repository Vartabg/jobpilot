"""Swipe engine + mobile server API (no network: build_queue is stubbed)."""

import pytest
from fastapi.testclient import TestClient

from jobpilot.gigs import server
from jobpilot.gigs.core import swipe
from jobpilot.gigs.core.models import Gig


def _gig(**kw):
    base = dict(id="hn-1", source="hn", title="Senior AI Engineer", company="Acme",
                description="rag agentic python", url="https://post.test/1",
                apply_url="mailto:jobs@acme.test", fit_score=95, location="Remote",
                salary_min=160000, salary_max=200000)
    base.update(kw)
    return Gig(**base)


def test_card_has_everything_the_phone_needs():
    c = swipe.card(_gig())
    for key in ("id", "company", "role", "score", "pay", "location",
                "offer", "subject", "draft", "apply_target", "is_mailto"):
        assert key in c
    assert c["is_mailto"] is True
    assert c["apply_target"].startswith("mailto:jobs@acme.test?subject=")
    assert "example.com" not in c["draft"]


def test_card_non_mailto_uses_apply_url():
    c = swipe.card(_gig(apply_url="https://boards.greenhouse.io/acme/jobs/1"))
    assert c["is_mailto"] is False
    assert c["apply_target"].startswith("https://boards.greenhouse.io")


@pytest.fixture
def client(monkeypatch, tmp_path):
    # No network: stub the scan. Isolate pipeline writes to a tmp file.
    monkeypatch.setattr(swipe, "build_queue", lambda **k: [_gig(), _gig(id="hn-2", company="Globex")])
    recorded = []
    monkeypatch.setattr(swipe, "record_decision",
                        lambda gig, action, reason="": recorded.append((gig.id, action)) or "sent")
    server._GIGS.clear()
    c = TestClient(server.app)
    c._recorded = recorded
    return c


def test_queue_endpoint_returns_cards(client):
    r = client.get("/api/queue")
    assert r.status_code == 200
    data = r.json()
    assert data["count"] == 2
    assert {c["company"] for c in data["cards"]} == {"Acme", "Globex"}


def test_decision_records_and_drops_from_session(client):
    client.get("/api/queue")  # populate
    r = client.post("/api/decision", json={"id": "hn-1", "action": "apply"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["remaining"] == 1
    assert client._recorded == [("hn-1", "apply")]
    # second decision on the same id is now unknown (dropped)
    r2 = client.post("/api/decision", json={"id": "hn-1", "action": "pass"})
    assert r2.status_code == 404


def test_index_serves_the_mobile_page(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "GigPilot" in r.text and "Get jobs" in r.text
