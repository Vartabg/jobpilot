import pytest
from fastapi.testclient import TestClient

from product.api import create_app
from product.models import Listing, SearchResult, SourceResult


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        client.get("/")
        yield client


def test_profile_import_and_auth(client):
    profile = {
        "full_name": "Alex Example",
        "skills": ["Python"],
        "resume_text": "Built Python tools for a volunteer group.",
        "boards": [{"provider": "ashby", "slug": "example"}],
    }
    assert client.put("/api/profile", json=profile).status_code == 200
    assert client.get("/api/profile").json()["full_name"] == "Alex Example"
    assert client.put("/api/profile", json={"invented": True}).status_code == 422
    assert (
        client.post(
            "/api/resume-text",
            content=b"Some real resume text to import.",
            headers={"Content-Type": "text/plain"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/resume-text",
            content=b"bad",
            headers={"Content-Type": "application/pdf"},
        ).status_code
        == 400
    )
    assert client.get("/api/export").json()["profile"]["full_name"] == "Alex Example"
    assert client.get("/api/fill").json()["url"].startswith("javascript:")
    assert (
        client.get(
            "/api/profile", headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    assert (
        client.get("/api/profile", headers={"Host": "evil.example"}).status_code == 403
    )
    assert (
        client.post("/api/search", headers={"Sec-Fetch-Site": "cross-site"}).status_code
        == 403
    )
    client.cookies.clear()
    for path in ["profile", "jobs", "export", "fill"]:
        assert client.get("/api/" + path).status_code == 401
    assert client.post("/api/quit").status_code == 401
    token = client.app.state.token
    assert (
        client.get(
            "/api/profile", headers={"Authorization": "Bearer " + token}
        ).status_code
        == 200
    )


def test_text_pdf_import(client):
    from io import BytesIO

    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=600, height=800)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    content = DecodedStreamObject()
    content.set_data(
        b"BT /F1 12 Tf 50 700 Td (I helped volunteers with scheduling and customer support.) Tj ET"
    )
    page[NameObject("/Contents")] = content
    output = BytesIO()
    writer.write(output)
    response = client.post(
        "/api/resume-text",
        content=output.getvalue(),
        headers={"Content-Type": "application/pdf"},
    )
    assert response.status_code == 200
    assert "helped volunteers" in response.json()["text"]


def test_search_reports_failures_and_keeps_tracking(client, monkeypatch):
    assert client.post("/api/search").status_code == 400
    client.put(
        "/api/profile", json={"boards": [{"provider": "ashby", "slug": "example"}]}
    )
    job = Listing(
        id="example-1",
        company="Example",
        title="Support",
        provider="ashby",
        board="example",
        url="https://jobs.ashbyhq.com/example/1",
    )
    result = SearchResult(
        jobs=[job],
        sources=[
            SourceResult(provider="ashby", slug="example", count=1),
            SourceResult(
                provider="lever", slug="failed", error="Board unavailable; try again."
            ),
        ],
    )
    monkeypatch.setattr("product.api.scan_boards", lambda boards: result)
    assert client.post("/api/search").json()["found"] == 1
    assert (
        client.patch(
            "/api/jobs/example-1", json={"status": "applied", "note": "Sent manually"}
        ).status_code
        == 200
    )
    client.post("/api/search")
    assert client.get("/api/jobs").json()[0]["status"] == "applied"
    assert (
        client.patch("/api/jobs/missing", json={"status": "applied"}).status_code == 404
    )
    assert (
        client.patch(
            "/api/jobs/example-1", json={"status": "applied", "follow_up": "2026-02-31"}
        ).status_code
        == 422
    )


def test_manual_posting_editable_draft_and_no_send(client):
    bad = client.post(
        "/api/jobs",
        json={"title": "Title", "company": "Example", "description": "too short"},
    )
    assert bad.status_code == 422
    job = client.post(
        "/api/jobs",
        json={
            "title": "Support",
            "company": "Example",
            "description": "Required: clear communication and customer support.",
        },
    ).json()
    path = f"/api/jobs/{job['id']}/draft"
    assert client.post(path).status_code == 400
    client.put(
        "/api/profile",
        json={
            "full_name": "Alex",
            "resume_text": "Wrote instructions to help volunteers with customer support.",
            "skills": ["customer support"],
        },
    )
    assert "volunteers" in client.post(path).json()["text"]
    assert client.put(path, json={"text": "My edited draft"}).status_code == 200
    assert client.post(path).json()["text"] == "My edited draft"
    assert client.put(path, json={"text": ""}).status_code == 422
    assert client.post("/api/jobs/missing/draft").status_code == 404
    assert client.post("/api/quit").status_code == 200
