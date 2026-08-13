"""Regression checks for the dedicated system-Chrome launcher."""

from pathlib import Path


def test_launcher_enables_automation_for_playwright_cdp_attach():
    script = Path(__file__).resolve().parents[1] / "scripts" / "launch_chrome.sh"

    assert "--enable-automation" in script.read_text()


def test_launcher_exposes_devtools_on_loopback_only():
    script = Path(__file__).resolve().parents[1] / "scripts" / "launch_chrome.sh"
    source = script.read_text()

    assert "--remote-debugging-address=127.0.0.1" in source
    assert "--remote-allow-origins=*" not in source
