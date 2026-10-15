"""Privacy scans must not need or disclose a saved candidate profile."""

from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from product.api import create_app
from product.privacy import PrivacyScanError


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path), base_url="http://127.0.0.1") as client:
        client.get("/privacy")
        yield client


def test_page_and_metadata_analyze_without_profile(client):
    page = client.get("/privacy")
    assert page.status_code == 200
    assert 'type="module"' in page.text
    report = client.post("/api/privacy/analyze", json={
        "source_url": "https://jobs.example/apply?token=secret#personal",
        "fields": [{"label": "Legal Address", "required": True}],
    })
    assert report.status_code == 200
    data = report.json()
    assert data["source_url"] == "https://jobs.example/apply"
    assert data["complete"] is False
    assert any(f["label"] == "Legal Address" for f in data["findings"])
    assert client.get("/api/profile").json()["full_name"] == ""


def test_reject_answers_unknown_fields_and_excessive_jobs(client):
    for data in [
        {"fields": [{"label": "Legal Address", "value": "private address"}]},
        {"fields": [], "answers": {"gender": "private"}},
        {"fields": [{"label": "Gender", "required": "true"}]},
    ]:
        response = client.post("/api/privacy/analyze", json=data)
        assert response.status_code == 422
        assert "private" not in response.text
    for urls in [[], ["https://example.test"] * 11]:
        assert client.post("/api/privacy/scan", json={"urls": urls}).status_code == 422


def test_scan_preserves_success_and_incomplete_failure(client, monkeypatch):
    def scan(url):
        if "blocked" in url:
            raise PrivacyScanError("Sign in and use the visible-form capture instead.")
        return {"source_url": url, "complete": False, "findings": [], "field_count": 2}

    monkeypatch.setattr("product.privacy_routes.scan_greenhouse_url", scan)
    result = client.post("/api/privacy/scan", json={"urls": [
        "https://job-boards.greenhouse.io/example/jobs/12",
        "https://user:password@blocked.example/apply?token=private",
    ]})
    assert result.status_code == 200
    reports = result.json()["reports"]
    assert len(reports) == 2 and reports[0]["field_count"] == 2
    assert reports[1]["complete"] is False and "error" in reports[1]
    assert reports[1]["source_url"] == "https://blocked.example/apply"
    assert "password" not in result.text and "private" not in result.text


def test_auth_and_no_cross_site_capture(client):
    assert client.post("/api/privacy/analyze", json={"fields": []}, headers={"Origin": "https://employer.example"}).status_code == 403
    client.cookies.clear()
    assert client.post("/api/privacy/scan", json={"urls": ["https://example.test"]}).status_code == 401
    assert client.get("/api/privacy/bookmark").status_code == 401


def test_bookmark_contains_collector_without_candidate_data(client):
    client.put("/api/profile", json={"full_name": "Secret Candidate", "email": "secret@example.test"})
    result = client.get("/api/privacy/bookmark")
    assert result.status_code == 200
    code = unquote(result.json()["url"])
    assert code.startswith("javascript:")
    assert "Secret Candidate" not in code and "secret@example.test" not in code
    assert "fetch(" not in code and "XMLHttpRequest" not in code
    assert "location.origin + location.pathname" in code


def test_capture_keeps_source_port_but_drops_token(client):
    report = client.post("/api/privacy/analyze", json={
        "source_url": "http://127.0.0.1:3189/form?token=private",
        "fields": [],
    }).json()
    assert report["source_url"] == "http://127.0.0.1:3189/form"
