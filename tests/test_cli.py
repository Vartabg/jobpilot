"""CLI smoke tests for JobPilot."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from typer.testing import CliRunner

import jobpilot.cli as cli
from jobpilot.cli import app

runner = CliRunner()


def test_doctor_reports_ready_stack():
    fake_bridge = MagicMock()
    fake_bridge.get_active_page = AsyncMock(return_value=MagicMock())
    fake_bridge.get_page_info = AsyncMock(
        return_value=MagicMock(
            url="https://www.linkedin.com/jobs/view/123",
            title="Senior Frontend Engineer",
            is_linkedin=True,
            is_job_application=True,
        )
    )
    fake_bridge.disconnect = AsyncMock()

    with (
        patch(
            "jobpilot.cli.get_health",
            return_value={
                "status": "ok",
                "whisper": "ready",
                "ollama_models": ["mistral"],
            },
        ),
        patch("jobpilot.cli.connect_to_chrome", new=AsyncMock(return_value=fake_bridge)),
    ):
        result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "Chrome CDP" in result.stdout
    assert "Bro API" in result.stdout
    assert "LinkedIn" in result.stdout


def test_doctor_fails_when_chrome_unreachable():
    with (
        patch(
            "jobpilot.cli.get_health",
            return_value={"status": "unreachable", "whisper": "unknown"},
        ),
        patch("jobpilot.cli.connect_to_chrome", new=AsyncMock(return_value=None)),
    ):
        result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1
    assert "launch_chrome.sh" in result.stdout


def test_resume_command_generates_output(tmp_path: Path):
    output = tmp_path / "resume.md"
    with patch("jobpilot.core.resume_tailor.OUTPUT_DIR", tmp_path / "resumes"):
        result = runner.invoke(
            app,
            [
                "resume",
                "Senior Frontend Engineer\nAcme AI\nRequirements\n- React\n- TypeScript\n- Remote",
                "--output",
                str(output),
                "--no-bro",
            ],
        )

    assert result.exit_code == 0
    assert output.exists()
    assert output.with_suffix(".html").exists()
    assert "ATS Resume Draft Ready" in result.stdout


def test_apply_claim_lock_blocks_non_ready_target(tmp_path: Path):
    claim_file = tmp_path / "claude-vetted-targets.json"
    claim_file.write_text(json.dumps({
        "targets": [{
            "company": "Huntress",
            "title": "Forward Deployed Engineer",
            "decision": "keep",
            "materials_status": "claude-preparing",
        }]
    }))
    job = SimpleNamespace(
        id="abc123",
        company="Huntress",
        title="Forward Deployed Engineer",
        url="https://job-boards.greenhouse.io/huntress/jobs/7711271003",
        location="Remote US",
        track="both",
        fit_score=78,
        status="queued",
    )

    with (
        patch.object(cli, "CLAUDE_VETTED_TARGETS_PATH", claim_file),
        patch("jobpilot.core.queue_builder.get_job", return_value=job),
        patch("jobpilot.core.form_filler.fill_application", new=AsyncMock()) as fill,
    ):
        result = runner.invoke(app, ["apply", "abc123"])

    assert result.exit_code == 1
    assert "Claim-lock blocked staging" in result.stdout
    fill.assert_not_awaited()
