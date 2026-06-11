"""macOS Reminders integration.

When the user `save`s a lead from commands.txt, gigpilot creates a Reminder
in the 'GigPilot Apply Queue' list with a Friday 5pm due date. The reminder
syncs to iPhone/iPad via iCloud automatically — no extra setup needed.

This module is best-effort. Failures are logged and never block the main
save-lead flow. Calling this module on a non-macOS system or with Reminders
locked behind a permission prompt simply returns False.
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import datetime, timedelta

from jobpilot.gigs.core.logger import get_logger
from jobpilot.gigs.core.models import Gig

log = get_logger(__name__)

REMINDER_LIST_NAME = "GigPilot Apply Queue"


def _next_friday_5pm(now: datetime | None = None) -> datetime:
    """Next Friday at 5pm local time (today if it's pre-5pm Friday)."""
    now = now or datetime.now()
    days_ahead = (4 - now.weekday()) % 7  # 4 = Friday
    if days_ahead == 0 and now.hour >= 17:
        days_ahead = 7
    target = (now + timedelta(days=days_ahead)).replace(
        hour=17, minute=0, second=0, microsecond=0
    )
    return target


def _applescript_str(s: str) -> str:
    """Escape a Python string for safe inclusion in an AppleScript double-quoted literal."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _format_for_applescript(dt: datetime) -> str:
    """AppleScript-friendly date string. Uses POSIX-style numeric date components
    to avoid locale-dependent month/day name parsing differences."""
    # macOS osascript accepts: date "5/8/2026 5:00:00 PM"
    return dt.strftime("%-m/%-d/%Y %-I:%M:00 %p")


def create_reminder_for_gig(gig: Gig, now: datetime | None = None) -> bool:
    """Create a macOS Reminder for following up on a saved lead.

    Returns True on success, False if osascript is missing, the AppleScript
    fails, or any other error occurs. Never raises.
    """
    if shutil.which("osascript") is None:
        log.info("osascript not available; skipping reminder creation")
        return False

    role = (gig.title or "").split("|")[0].strip()[:80] or "the role"
    company = gig.company or gig.source or "this company"
    apply_target = gig.apply_url or gig.url or ""

    name = _applescript_str(f"Apply to {company} — {role}")
    body_lines = [
        f"Apply: {apply_target}",
        f"Source: {gig.url}" if gig.url and gig.url != apply_target else "",
        f"Pay: {_pay_str(gig)}",
        f"Fit: {gig.fit_score}/100",
    ]
    body = _applescript_str("\n".join(line for line in body_lines if line))
    list_name = _applescript_str(REMINDER_LIST_NAME)
    due_str = _format_for_applescript(_next_friday_5pm(now))

    script = (
        'tell application "Reminders"\n'
        '  try\n'
        f'    set targetList to list "{list_name}"\n'
        '  on error\n'
        f'    set targetList to make new list with properties {{name:"{list_name}"}}\n'
        '  end try\n'
        '  tell targetList\n'
        '    make new reminder with properties {'
        f'name:"{name}", body:"{body}", due date:date "{due_str}"'
        '}\n'
        '  end tell\n'
        'end tell\n'
    )

    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            check=True,
            timeout=10,
            capture_output=True,
            text=True,
        )
        log.info("Reminder created for %s (due %s)", company, due_str)
        return True
    except subprocess.CalledProcessError as e:
        log.warning("Reminder creation failed: %s", (e.stderr or "").strip())
        return False
    except Exception as e:
        log.warning("Reminder creation errored: %s", e)
        return False


def _pay_str(gig: Gig) -> str:
    if gig.salary_max and gig.salary_min:
        return f"${gig.salary_min/1000:.0f}-${gig.salary_max/1000:.0f}K/yr"
    if gig.salary_max:
        return f"up to ${gig.salary_max/1000:.0f}K/yr"
    if gig.pay_hourly_est:
        return f"${gig.pay_hourly_est:.0f}/hr"
    return "pay not stated"
