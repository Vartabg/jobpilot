"""Reminder sync from pipeline state — dedup bookkeeping lives in the
data/reminder_flags.json sidecar (never the user-facing Notes cell), so each
actively-pursued row gets exactly one Reminder. No osascript, no real
iCloud pipeline.md."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from jobpilot.gigs.core import away, pipeline
from jobpilot.gigs.core.pipeline import Row


def _sidecar(tmp_path: Path, monkeypatch) -> Path:
    path = tmp_path / "reminder_flags.json"
    monkeypatch.setattr(away, "REMINDER_FLAGS_PATH", path)
    return path


def test_sync_creates_once_and_records_sidecar_flag(tmp_path: Path, monkeypatch) -> None:
    flags_path = _sidecar(tmp_path, monkeypatch)
    rows = [
        Row(status="saved", score=90, company="Acme", role="X",
            apply="mailto:jobs@acme.io", gig_id="hn-1"),
        Row(status="new", score=80, company="Beta", role="Y", gig_id="hn-2"),
    ]
    with patch("jobpilot.gigs.core.away.create_reminder_for_gig", return_value=True) as create, \
            patch.object(pipeline, "write", MagicMock()) as write:
        assert away.sync_reminders_from_pipeline(rows) == 1
        assert create.call_count == 1

        # Flag landed in the sidecar, keyed by gig_id; Notes stays untouched.
        assert "hn-1" in json.loads(flags_path.read_text())
        assert rows[0].notes == ""
        write.assert_not_called()  # the pipeline file is never written from here

        # Next run: sidecar flag persists — no duplicate Reminder.
        assert away.sync_reminders_from_pipeline(rows) == 0
        assert create.call_count == 1


def test_sync_skips_non_pursued_and_already_flagged_rows(tmp_path: Path, monkeypatch) -> None:
    flags_path = _sidecar(tmp_path, monkeypatch)
    flags_path.write_text(json.dumps({"c": "2026-06-11T07:00:00"}))
    rows = [
        Row(status="new", company="A", role="X", gig_id="a"),
        Row(status="passed", company="B", role="Y", gig_id="b"),
        Row(status="saved", company="C", role="Z", gig_id="c"),
    ]
    with patch("jobpilot.gigs.core.away.create_reminder_for_gig", return_value=True) as create:
        assert away.sync_reminders_from_pipeline(rows) == 0
    create.assert_not_called()


def test_sync_does_not_flag_rows_when_creation_fails(tmp_path: Path, monkeypatch) -> None:
    flags_path = _sidecar(tmp_path, monkeypatch)
    rows = [Row(status="saved", company="Acme", role="X", gig_id="hn-1")]
    with patch("jobpilot.gigs.core.away.create_reminder_for_gig", return_value=False):
        assert away.sync_reminders_from_pipeline(rows) == 0
    assert not flags_path.exists()  # retried next run instead of silently lost


def test_legacy_notes_flag_migrates_to_sidecar(tmp_path: Path, monkeypatch) -> None:
    flags_path = _sidecar(tmp_path, monkeypatch)
    # Pre-sidecar rows carried "reminder_created" in Notes; pipeline.parse
    # strips the literal and raises legacy_reminder_flag instead.
    rows = [
        Row(status="saved", company="Acme", role="X", gig_id="hn-1",
            notes="pass:low-pay", legacy_reminder_flag=True),
        Row(status="passed", company="Beta", role="Y", gig_id="hn-2",
            legacy_reminder_flag=True),  # migrated even when not pursued
    ]
    with patch("jobpilot.gigs.core.away.create_reminder_for_gig", return_value=True) as create:
        assert away.sync_reminders_from_pipeline(rows) == 0
    create.assert_not_called()  # no duplicate for already-flagged rows
    flags = json.loads(flags_path.read_text())
    assert set(flags) == {"hn-1", "hn-2"}


def test_rows_without_gig_id_dedupe_on_company_role(tmp_path: Path, monkeypatch) -> None:
    flags_path = _sidecar(tmp_path, monkeypatch)
    rows = [Row(status="saved", company="Acme", role="X")]
    with patch("jobpilot.gigs.core.away.create_reminder_for_gig", return_value=True) as create:
        assert away.sync_reminders_from_pipeline(rows) == 1
        assert away.sync_reminders_from_pipeline(rows) == 0
    assert create.call_count == 1
    assert "Acme|X" in json.loads(flags_path.read_text())


def test_parse_to_sync_round_trip_strips_literal_and_stays_deduped(
    tmp_path: Path, monkeypatch,
) -> None:
    """End-to-end migration: a pipeline.md with the legacy literal in Notes
    parses clean, the flag reaches the sidecar, the next write scrubs the
    file, and no duplicate Reminder is ever created."""
    flags_path = _sidecar(tmp_path, monkeypatch)
    md = tmp_path / "pipeline.md"
    md.write_text(
        "| Status | Score | Company — Role | Pay | Apply | Saved | Last touched | Next action | Notes |\n"
        "|---|---|---|---|---|---|---|---|---|\n"
        "| saved | 90 | Acme — X | | | | | | ping Sam reminder_created | <!-- gig_id:hn-1 -->\n"
    )
    rows = pipeline.parse(md)
    assert rows[0].notes == "ping Sam"
    assert rows[0].legacy_reminder_flag is True

    with patch("jobpilot.gigs.core.away.create_reminder_for_gig", return_value=True) as create:
        assert away.sync_reminders_from_pipeline(rows) == 0
    create.assert_not_called()
    assert "hn-1" in json.loads(flags_path.read_text())

    # The next regular pipeline write scrubs the literal from the file.
    pipeline.write(rows, md)
    assert "reminder_created" not in md.read_text()
    assert pipeline.parse(md)[0].notes == "ping Sam"
