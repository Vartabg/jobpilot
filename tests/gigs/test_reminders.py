"""Reminders integration tests — verifies date math and string escaping
without actually invoking osascript (which would create real reminders)."""
from __future__ import annotations

import subprocess
from datetime import datetime
from unittest.mock import patch

from jobpilot.gigs.core.models import Gig
from jobpilot.gigs.core.reminders import (
    _applescript_str,
    _format_for_applescript,
    _next_friday_5pm,
    create_reminder_for_gig,
)


def test_next_friday_returns_today_5pm_if_friday_morning() -> None:
    # Friday May 1, 2026 at 9am
    fri_morning = datetime(2026, 5, 1, 9, 0)
    target = _next_friday_5pm(fri_morning)
    assert target.year == 2026 and target.month == 5 and target.day == 1
    assert target.hour == 17 and target.minute == 0


def test_next_friday_skips_to_next_week_if_friday_evening() -> None:
    # Friday May 1, 2026 at 6pm — already past 5pm, push to following Friday
    fri_evening = datetime(2026, 5, 1, 18, 0)
    target = _next_friday_5pm(fri_evening)
    assert target.day == 8  # May 8, the following Friday


def test_next_friday_from_tuesday() -> None:
    tue = datetime(2026, 5, 5, 10, 0)
    target = _next_friday_5pm(tue)
    assert target.weekday() == 4  # Friday
    assert target.day == 8 and target.hour == 17


def test_applescript_string_escaping_handles_quotes_and_backslashes() -> None:
    assert _applescript_str('Acme "AI" Co\\') == r'Acme \"AI\" Co\\'


def test_format_for_applescript_is_locale_safe_numeric() -> None:
    dt = datetime(2026, 5, 8, 17, 0)
    formatted = _format_for_applescript(dt)
    # Expect numeric form: 5/8/2026 5:00:00 PM
    assert "5/8/2026" in formatted
    assert "5:00:00 PM" in formatted


def test_create_reminder_skips_when_osascript_missing() -> None:
    with patch("jobpilot.gigs.core.reminders.shutil.which", return_value=None):
        result = create_reminder_for_gig(_gig())
    assert result is False


def test_create_reminder_returns_false_on_subprocess_error() -> None:
    err = subprocess.CalledProcessError(1, ["osascript"], stderr="boom")
    with patch("jobpilot.gigs.core.reminders.subprocess.run", side_effect=err):
        result = create_reminder_for_gig(_gig())
    assert result is False


def test_create_reminder_invokes_osascript_with_expected_payload() -> None:
    with patch("jobpilot.gigs.core.reminders.subprocess.run") as run:
        result = create_reminder_for_gig(_gig())
    assert result is True
    args, _kwargs = run.call_args
    cmd = args[0]
    assert cmd[0] == "osascript" and cmd[1] == "-e"
    script = cmd[2]
    assert 'tell application "Reminders"' in script
    assert "GigPilot Apply Queue" in script
    assert "Apply to Acme — Senior Engineer" in script
    assert "https://acme.ai/jobs/1" in script


def _gig() -> Gig:
    return Gig(
        id="hn-1",
        source="hn",
        title="Senior Engineer",
        url="https://news.ycombinator.com/item?id=1",
        apply_url="https://acme.ai/jobs/1",
        company="Acme",
        description="LLM work.",
        location="NYC",
        salary_min=180000,
        salary_max=200000,
        fit_score=95,
    )
