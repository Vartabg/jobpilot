"""Regression checks for the dedicated system-Chrome launcher."""

from pathlib import Path


def test_launcher_enables_automation_for_playwright_cdp_attach():
    script = Path(__file__).resolve().parents[1] / "scripts" / "launch_chrome.sh"

    assert "--enable-automation" in script.read_text()
