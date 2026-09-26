import json
import sqlite3

import pytest
from typer.testing import CliRunner

from jobpilot.bridge.cli import app

PIPELINE = """# Pipeline

| Status | Score | Company — Role | Pay | Apply | Saved | Last touched | Next action | Notes |
|---|---|---|---|---|---|---|---|---|
| sent | 72 | Beta — Automation build | $80/hr | https://example.com/gigs/1 | 2026-09-20 | 2026-09-22 |  |  | <!-- gig_id:hn-1 -->
| new | 60 | Gamma — Data cleanup |  |  |  |  |  |  | <!-- gig_id:wwr-2 -->
"""


@pytest.fixture
def stores(tmp_path):
    queue = tmp_path / "queue.json"
    queue.write_text(
        json.dumps(
            [
                {
                    "id": "q1",
                    "company": "Acme",
                    "title": "Solutions Engineer",
                    "url": "https://jobs.ashbyhq.com/acme/uuid-9",
                    "location": "Remote",
                    "portal": "ashby",
                    "track": "tech",
                    "fit_score": 66,
                    "keywords": [],
                    "status": "applied",
                    "queued_at": "2026-09-25T11:56:45",
                }
            ]
        )
    )
    tracker = tmp_path / "applications.db"
    with sqlite3.connect(tracker) as db:
        db.execute(
            "CREATE TABLE applications (id INTEGER PRIMARY KEY, job_url TEXT, job_title TEXT, company TEXT, "
            "applied_at TEXT, status TEXT, step_reached INTEGER, fields_filled INTEGER, updated_at TEXT)"
        )
        db.execute(
            "INSERT INTO applications (job_url, job_title, company, applied_at, status, updated_at) "
            "VALUES ('https://jobs.ashbyhq.com/acme/uuid-9', 'Solutions Engineer', 'Acme', '2026-09-25', 'applied', '2026-09-25T13:07:47')"
        )
    pipeline = tmp_path / "pipeline.md"
    pipeline.write_text(PIPELINE)
    return {
        "--queue": queue,
        "--tracker": tracker,
        "--pipeline": pipeline,
        "--first-seen": tmp_path / "first_seen.json",
        "--ledger": tmp_path / "ledger" / "opportunities.db",
    }


def args(stores, *extra):
    flat = [str(part) for pair in stores.items() for part in pair]
    return ["import", *flat, *extra]


def test_dry_run_reports_without_writing(stores):
    result = CliRunner().invoke(app, args(stores, "--dry-run"))
    assert result.exit_code == 0, result.output
    assert "Dry run: nothing was written." in result.output
    assert not stores["--ledger"].exists()


def test_import_then_status(stores):
    runner = CliRunner()
    first = runner.invoke(app, args(stores))
    assert first.exit_code == 0, first.output
    assert stores["--ledger"].exists()

    again = runner.invoke(app, args(stores))
    assert again.exit_code == 0, again.output

    status = runner.invoke(app, ["status", "--ledger", str(stores["--ledger"])])
    assert status.exit_code == 0, status.output
    # Acme was applied in both tracker and queue: one role, status applied.
    # Beta was sent (applied); Gamma is new.
    assert "applied" in status.output and "new" in status.output


def test_status_without_a_ledger(tmp_path):
    result = CliRunner().invoke(
        app, ["status", "--ledger", str(tmp_path / "missing.db")]
    )
    assert result.exit_code == 1
    assert "ledger import" in result.output


def test_unreadable_source_is_reported_and_fails(stores):
    stores["--queue"].write_text("{not json")
    result = CliRunner().invoke(app, args(stores, "--dry-run"))
    assert result.exit_code == 1
    assert "could not read" in result.output
