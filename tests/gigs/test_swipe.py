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
    # No network: stub the scan + the pipeline-writing record/undo.
    monkeypatch.setattr(swipe, "build_queue", lambda **k: [_gig(), _gig(id="hn-2", company="Globex")])
    recorded, undone = [], []
    monkeypatch.setattr(swipe, "record_decision",
                        lambda gig, action, reason="": recorded.append((gig.id, action)) or "sent")
    monkeypatch.setattr(swipe, "undo_decision", lambda gig: undone.append(gig.id))
    server._GIGS.clear()
    c = TestClient(server.app)
    c._recorded, c._undone = recorded, undone
    return c


def test_queue_endpoint_returns_cards(client):
    r = client.get("/api/queue")
    assert r.status_code == 200
    data = r.json()
    assert data["count"] == 2
    assert {c["company"] for c in data["cards"]} == {"Acme", "Globex"}


def test_decision_records_and_keeps_gig_for_undo(client):
    client.get("/api/queue")  # populate
    r = client.post("/api/decision", json={"id": "hn-1", "action": "apply"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["status"] == "sent"
    assert client._recorded == [("hn-1", "apply")]
    # gig is kept in the session so it can be undone
    u = client.post("/api/undo", json={"id": "hn-1"})
    assert u.status_code == 200 and u.json()["ok"] is True
    assert client._undone == ["hn-1"]


def test_decision_rejects_bad_action(client):
    client.get("/api/queue")
    r = client.post("/api/decision", json={"id": "hn-1", "action": "maybe"})
    assert r.status_code == 422  # Literal['apply','pass'] enforced


def test_decision_unknown_id_404(client):
    client.get("/api/queue")
    r = client.post("/api/decision", json={"id": "nope", "action": "pass"})
    assert r.status_code == 404


def test_index_serves_the_mobile_page(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "GigPilot" in r.text and "Get jobs" in r.text


# --- decision persistence (the showstopper: status must reach pipeline.md) ---

def _seed_pipeline_with_new(gig_id: str):
    from jobpilot.gigs.core import pipeline
    from jobpilot.gigs.core.pipeline import Row
    pipeline.PIPELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    pipeline.PIPELINE_PATH.unlink(missing_ok=True)
    pipeline.write([Row(status="new", company="Acme", role="Senior AI Engineer", gig_id=gig_id)])


def test_apply_swipe_persists_sent_to_pipeline():
    from jobpilot.gigs.core import pipeline, swipe
    _seed_pipeline_with_new("sw-apply")
    assert swipe.record_decision(_gig(id="sw-apply"), "apply") == "sent"
    rows = {r.gig_id: r for r in pipeline.parse()}
    assert rows["sw-apply"].status == "sent"  # not silently reverted to 'new'


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
