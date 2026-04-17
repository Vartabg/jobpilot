"""CLI smoke tests for JobPilot."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from typer.testing import CliRunner

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
