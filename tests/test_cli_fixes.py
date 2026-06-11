"""Tests for CLI bug fixes: binary score sources, claim-lock gating, YoE parsing."""

import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import typer
from typer.testing import CliRunner

import jobpilot.cli as cli
from jobpilot.cli import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Fix 2: score command on non-UTF-8 / PDF files
# ---------------------------------------------------------------------------

def test_score_binary_file_friendly_error(tmp_path: Path):
    binary = tmp_path / "job-description.txt"
    binary.write_bytes(b"\x89PNG\r\n\x1a\n\x00\xff\xfe\x80binary garbage")

    result = runner.invoke(app, ["score", str(binary)])

    assert result.exit_code == 1
    assert "binary file" in result.stdout
    assert "Traceback" not in result.stdout


def test_score_pdf_file_tailored_error(tmp_path: Path):
    pdf = tmp_path / "job-description.pdf"
    pdf.write_bytes(b"%PDF-1.4\x00\xff\xfe")

    result = runner.invoke(app, ["score", str(pdf)])

    assert result.exit_code == 1
    assert "PDF" in result.stdout
    assert "Traceback" not in result.stdout


# ---------------------------------------------------------------------------
# Fix 3: claim-lock — missing file skips the gate; glob picks newest by mtime
# ---------------------------------------------------------------------------

def _fake_job() -> SimpleNamespace:
    return SimpleNamespace(
        id="abc123",
        company="Huntress",
        title="Forward Deployed Engineer",
        url="https://job-boards.greenhouse.io/huntress/jobs/7711271003",
    )


def test_claim_lock_skipped_when_no_vetted_targets_file(tmp_path: Path):
    with (
        patch.object(cli, "CLAUDE_VETTED_TARGETS_PATH", None),
        patch.object(cli, "CLAUDE_VETTED_TARGETS_DIR", tmp_path),
    ):
        # Must not raise typer.Exit — gate is not configured on fresh clones.
        cli._enforce_claim_lock(_fake_job(), claim_approved=False)


def test_claim_lock_still_enforced_when_file_present(tmp_path: Path):
    lock = tmp_path / "claude-vetted-targets-2026-06-10.json"
    lock.write_text(json.dumps({"targets": []}))

    with (
        patch.object(cli, "CLAUDE_VETTED_TARGETS_PATH", None),
        patch.object(cli, "CLAUDE_VETTED_TARGETS_DIR", tmp_path),
        pytest.raises(typer.Exit) as exc_info,
    ):
        cli._enforce_claim_lock(_fake_job(), claim_approved=False)
    assert exc_info.value.exit_code == 1


def test_resolve_claim_lock_path_picks_newest_by_mtime(tmp_path: Path):
    older_name = tmp_path / "claude-vetted-targets-2026-06-05.json"
    newer_mtime = tmp_path / "claude-vetted-targets-2026-06-01.json"
    ignored = tmp_path / "other-report.json"
    for f in (older_name, newer_mtime, ignored):
        f.write_text(json.dumps({"targets": []}))

    # Make the lexicographically *older* name the most recently modified file,
    # proving selection is by mtime rather than by filename.
    now = older_name.stat().st_mtime
    os.utime(older_name, (now - 600, now - 600))
    os.utime(newer_mtime, (now + 600, now + 600))

    with (
        patch.object(cli, "CLAUDE_VETTED_TARGETS_PATH", None),
        patch.object(cli, "CLAUDE_VETTED_TARGETS_DIR", tmp_path),
    ):
        assert cli._resolve_claim_lock_path() == newer_mtime


def test_resolve_claim_lock_path_none_when_dir_empty(tmp_path: Path):
    with (
        patch.object(cli, "CLAUDE_VETTED_TARGETS_PATH", None),
        patch.object(cli, "CLAUDE_VETTED_TARGETS_DIR", tmp_path),
    ):
        assert cli._resolve_claim_lock_path() is None


def test_resolve_claim_lock_path_honors_explicit_override(tmp_path: Path):
    override = tmp_path / "claude-vetted-targets.json"
    override.write_text(json.dumps({"targets": []}))

    with patch.object(cli, "CLAUDE_VETTED_TARGETS_PATH", override):
        assert cli._resolve_claim_lock_path() == override


# ---------------------------------------------------------------------------
# Fix 4: years-of-experience parsing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("5", 5),
        ("5 years", 5),
        ("  12 yrs ", 12),
        ("7+", 7),
        ("0", 0),
        ("five", None),
        ("", None),
        ("about right", None),
    ],
)
def test_parse_years_of_experience(raw: str, expected):
    assert cli._parse_years_of_experience(raw) == expected
