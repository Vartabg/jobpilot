import json
import socket
import threading
import time
from pathlib import Path

import pytest
import requests
import uvicorn
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright

from product.api import create_app
from product.models import Draft, JobUpdate, Listing, Profile, WorkspaceSnapshot
from product.store import Store

CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")


def backup(name="Alex Example"):
    return {
        "schema_version": "jobpilot.product/v1",
        "profile": {"full_name": name, "resume_text": "I helped a volunteer group."},
        "jobs": [
            {
                "id": "newer",
                "company": "Northwind",
                "title": "Support",
                "url": "https://example.com/jobs/new",
                "provider": "manual",
                "status": "applied",
                "note": "Sent myself",
                "follow_up": "2026-10-28",
            },
            {
                "id": "older",
                "company": "Contoso",
                "title": "Guide",
                "url": "https://example.com/jobs/old",
                "provider": "manual",
                "status": "saved",
                "note": "",
                "follow_up": "",
            },
        ],
        "drafts": [
            {
                "job_id": "newer",
                "text": "My edited draft",
                "updated_at": "2026-10-01T00:00:00+00:00",
            }
        ],
    }


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as opened:
        opened.get("/")
        yield opened


def test_replace_round_trip_keeps_order_and_rejects_bad_files(tmp_path):
    store = Store(tmp_path)
    store.save_profile(Profile(full_name="Keep me", resume_text="Existing work."))
    store.save_jobs(
        [
            Listing(
                id="local",
                company="Local",
                title="Role",
                url="https://example.com/local",
                provider="manual",
            )
        ]
    )
    store.update_job("local", JobUpdate(status="interview", note="Waiting"))
    store.save_draft(
        Draft(job_id="local", text="Do not lose this until restore works.")
    )
    original = store.export()
    snapshot = WorkspaceSnapshot.model_validate(backup())
    store.replace(snapshot)
    restored = store.export()
    assert [job["id"] for job in restored["jobs"]] == ["newer", "older"]
    assert restored["jobs"][0]["status"] == "applied"
    assert restored["jobs"][0]["note"] == "Sent myself"
    assert restored["jobs"][0]["follow_up"] == "2026-10-28"
    assert restored["drafts"] == backup()["drafts"]
    assert restored["profile"]["full_name"] == "Alex Example"
    assert "token" not in restored
    bad = json.loads(json.dumps(restored))
    bad["drafts"].append({"job_id": "missing", "text": "orphan"})
    with pytest.raises(ValueError, match="not included"):
        store.replace(WorkspaceSnapshot.model_validate(bad))
    duplicate = json.loads(json.dumps(restored))
    duplicate["jobs"].append(dict(duplicate["jobs"][0]))
    with pytest.raises(ValueError, match="own id"):
        store.replace(WorkspaceSnapshot.model_validate(duplicate))
    assert store.export() == restored
    store.replace(WorkspaceSnapshot.model_validate(original))
    assert store.export() == original


def test_import_replaces_workspace_and_keeps_bad_files_away(client, tmp_path):
    token = (tmp_path / "api-token").read_text()
    client.put(
        "/api/profile",
        json={"full_name": "Before", "resume_text": "Work already on this computer."},
    )
    assert client.post("/api/import", content=b"not json").status_code == 400
    assert client.get("/api/profile").json()["full_name"] == "Before"
    rejected = client.post(
        "/api/import", json={"schema_version": "nope", "profile": {}}
    )
    assert rejected.status_code == 422
    assert "Export my data" in rejected.json()["error"]
    assert client.get("/api/profile").json()["full_name"] == "Before"
    orphan = backup()
    orphan["drafts"][0]["job_id"] = "missing"
    assert client.post("/api/import", json=orphan).status_code == 422
    assert client.get("/api/profile").json()["full_name"] == "Before"
    response = client.post("/api/import", json=backup())
    assert response.status_code == 200
    assert response.json()["message"] == (
        "Backup restored. 2 roles and 1 draft are now on this computer."
    )
    assert client.get("/api/profile").json()["full_name"] == "Alex Example"
    jobs = client.get("/api/jobs").json()
    assert [job["id"] for job in jobs] == ["newer", "older"]
    assert jobs[0]["note"] == "Sent myself"
    assert client.get("/api/export").json()["drafts"][0]["text"] == "My edited draft"
    assert (tmp_path / "api-token").read_text() == token
    blocked = client.post(
        "/api/import",
        json=backup("Someone else"),
        headers={"Sec-Fetch-Site": "cross-site"},
    )
    assert blocked.status_code == 403
    assert client.get("/api/profile").json()["full_name"] == "Alex Example"


def test_import_rejects_an_oversized_backup(client, monkeypatch):
    monkeypatch.setattr("product.security.IMPORT_MAX_BODY", 30)
    response = client.post("/api/import", json=backup())
    assert response.status_code == 413
    assert response.json()["error"] == "This backup is larger than 16 MiB."
    assert client.get("/api/profile").json()["full_name"] == ""


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def app_url(tmp_path):
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(tmp_path),
            host="127.0.0.1",
            port=port,
            log_level="error",
            access_log=False,
        )
    )
    server.install_signal_handlers = lambda: None
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            if requests.get(url + "/health", timeout=0.2).ok:
                break
        except requests.RequestException:
            time.sleep(0.05)
    else:
        raise RuntimeError("JobPilot did not start for the browser check.")
    yield url
    server.should_exit = True
    thread.join(timeout=5)


def test_restore_button_confirms_before_replacing(app_url):
    if not CHROME.exists():
        pytest.skip("System Chrome is required for the browser check.")
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=str(CHROME), headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(app_url, wait_until="networkidle")
        restore = page.get_by_role("button", name="Restore a backup")
        restore.focus()
        assert page.evaluate("() => document.activeElement.id") == "restore"
        page.once("dialog", lambda dialog: dialog.dismiss())
        with page.expect_file_chooser() as chooser:
            restore.click()
        chooser.value.set_files(
            {
                "name": "jobpilot-data.json",
                "mimeType": "application/json",
                "buffer": json.dumps(backup()).encode(),
            }
        )
        page.get_by_text("Restore cancelled. Your saved work is unchanged.").wait_for()
        assert page.get_by_text("Northwind").count() == 0
        page.once(
            "dialog",
            lambda dialog: (
                dialog.accept()
                if "replaces the profile" in dialog.message
                else dialog.dismiss()
            ),
        )
        with page.expect_file_chooser() as chooser:
            restore.click()
        chooser.value.set_files(
            {
                "name": "jobpilot-data.json",
                "mimeType": "application/json",
                "buffer": json.dumps(backup()).encode(),
            }
        )
        page.get_by_text(
            "Backup restored. 2 roles and 1 draft are now on this computer."
        ).wait_for()
        assert page.get_by_text("Northwind").count() == 1
        for width in (1440, 320):
            page.set_viewport_size({"width": width, "height": 800})
            assert page.evaluate(
                "() => document.documentElement.scrollWidth <= window.innerWidth + 1"
            )
        page.emulate_media(forced_colors="active")
        assert restore.is_visible()
        browser.close()
    assert errors == []
