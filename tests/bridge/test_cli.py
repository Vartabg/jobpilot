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


# ----- refresh and screened ---------------------------------------------------------

from rich.console import Console  # noqa: E402

from jobpilot.bridge import cli as ledger_cli  # noqa: E402
from jobpilot.engine.domain import (  # noqa: E402
    BoardResult,
    Listing,
    Posting,
    Workplace,
)

SETTINGS_TOML = """
[profile]
languages = ["English", "Spanish"]
[rules.location]
home = ["Austin, TX"]
remote = "us"
[rules.level]
exclude_titles = ["senior"]
max_years_required = 4
"""


def board_posting(job_id, title, locations, workplace, description=""):
    listing = Listing(
        "Gamma",
        title,
        url=f"https://jobs.ashbyhq.com/gamma/{job_id}",
        provider="ashby",
        tenant="gamma",
        provider_job_id=job_id,
    )
    return Posting(listing, description, workplace, locations)


class FakeBoards:
    def fetch(self, target):
        return BoardResult(
            target,
            (
                board_posting(
                    "1",
                    "Forward Deployed Engineer",
                    ("United States",),
                    Workplace.REMOTE,
                ),
                board_posting(
                    "2", "Senior Engineer", ("United States",), Workplace.REMOTE
                ),
                board_posting(
                    "3",
                    "Deployment Engineer",
                    ("San Francisco",),
                    Workplace.ONSITE,
                    "- 5+ years of experience",
                ),
            ),
        )


@pytest.fixture
def board_setup(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger_cli, "_make_boards", FakeBoards)
    # A wide console keeps Rich from folding long reasons across table rows.
    monkeypatch.setattr(ledger_cli, "console", Console(width=200))
    portals = tmp_path / "portals.json"
    portals.write_text(
        json.dumps(
            [
                {
                    "portal": "ashby",
                    "value": "gamma",
                    "label": "Gamma",
                    "enabled": True,
                },
                {"portal": "ashby", "value": "off", "enabled": False},
                {"portal": "indeed", "value": "q", "enabled": True},
            ]
        )
    )
    settings = tmp_path / "jobpilot.toml"
    settings.write_text(SETTINGS_TOML)
    ledger = tmp_path / "opportunities.db"
    return [
        "--portals",
        str(portals),
        "--settings",
        str(settings),
        "--ledger",
        str(ledger),
    ], ledger


def test_board_targets_keep_enabled_supported_boards(tmp_path):
    portals = tmp_path / "portals.json"
    portals.write_text(
        json.dumps(
            [
                {"portal": "greenhouse", "value": "acme", "label": "Acme"},
                {"portal": "lever", "value": "beta", "enabled": False},
                {"portal": "adzuna", "value": "x"},
            ]
        )
    )
    targets = ledger_cli.board_targets(portals)
    assert [(t.provider, t.token, t.company) for t in targets] == [
        ("greenhouse", "acme", "Acme")
    ]
    assert ledger_cli.board_targets(portals, only="greenhouse:acme") == targets
    assert ledger_cli.board_targets(portals, only="nobody") == []


def test_refresh_dry_run_writes_nothing(board_setup):
    options, ledger = board_setup
    result = CliRunner().invoke(ledger_cli.app, ["refresh", "--dry-run", *options])
    assert result.exit_code == 0, result.output
    assert "Dry run" in result.output
    assert not ledger.exists()


def test_refresh_then_screened(board_setup):
    options, ledger = board_setup
    runner = CliRunner()
    result = runner.invoke(ledger_cli.app, ["refresh", *options])
    assert result.exit_code == 0, result.output
    assert "Failed by rule:" in result.output

    passed = runner.invoke(ledger_cli.app, ["screened", "--ledger", str(ledger)])
    assert passed.exit_code == 0, passed.output
    assert "Forward Deployed Engineer" in passed.output
    assert "Senior Engineer" not in passed.output

    failed = runner.invoke(
        ledger_cli.app, ["screened", "--failed", "--ledger", str(ledger)]
    )
    assert "Senior Engineer" in failed.output and "Title includes" in failed.output
    assert "your home is Austin, TX" in failed.output


def test_refresh_stops_on_broken_settings(board_setup, tmp_path):
    options, _ = board_setup
    bad = tmp_path / "bad.toml"
    bad.write_text('[rules.location]\nremote = "mars"\n')
    options[options.index("--settings") + 1] = str(bad)
    result = CliRunner().invoke(ledger_cli.app, ["refresh", *options])
    assert result.exit_code == 1
    assert "rules.location.remote" in result.output
