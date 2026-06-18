"""Per-digest 'crib sheet' for ATS form-fill.

Goal: minimize phone taps + thinking when the user lands on a Greenhouse/
Lever/Ashby application form. Renders a single iCloud markdown file with:

  1. Standard answers (work auth, location, EEOC, salary, etc.) ready to copy.
  2. Per-gig section tuned to the gig (location-aware relocate answer,
     salary anchor based on the gig's pay band, draft cover-letter snippet).

This file does NOT submit anything. It is reference content the user reads
on their phone while filling the form themselves. CAPTCHA + screening Qs
remain manual by design (autofill was evaluated and rejected as unreliable).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from jobpilot.gigs.core import preferences
from jobpilot.gigs.core.models import Gig
from jobpilot.gigs.core.paths import away_dir
from jobpilot.gigs.core.proposals import build_revenue_brief

CRIB_DIR = away_dir()

# Standard ATS answers. Neutral placeholder defaults only — edit to match
# your own answers (a data/preferences.json override is a phase-3 follow-up).
# Demographic/EEOC rows default to declining so nothing personal ships.
STANDARD_ANSWERS: list[tuple[str, str]] = [
    ("Phone country", "+1"),
    ("Location (city)", "Your City, ST, USA"),
    ("Country based in", "USA"),
    ("Authorized to work in the US?", "Yes"),
    ("Require visa sponsorship?", "No"),
    ("Willing to relocate?", "Yes — returning to Austin; remote/async until settled"),
    ("In-office acknowledgment", "Prefer remote/async; milestone on-site OK"),
    ("Privacy consent", "Consent"),
    ("Gender", "Decline To Self Identify"),
    ("Hispanic/Latino?", "Decline To Self Identify"),
    ("Race/Ethnicity", "Decline To Self Identify"),
    ("Veteran status", "I don't wish to answer"),
    ("Disability status", "I do not want to answer"),
    ("How did you hear about us?", "LinkedIn"),
]

# Identity now comes from preferences.json so the user can edit without code.
# These constants are kept only as last-line fallbacks for tests that don't load prefs.


def _is_home_metro_role(gig: Gig) -> bool:
    """True when the gig looks located in the user's home metro
    (preferences `location.home_metro_tags`; empty by default)."""
    tags = preferences.home_metro_tags()
    if not tags:
        return False
    haystack = " ".join([gig.location or "", gig.title or "", gig.description or ""]).lower()
    return any(tag in haystack for tag in tags)


def _relocate_answer(gig: Gig) -> str:
    return (
        "Yes, I'm currently located here"
        if _is_home_metro_role(gig)
        else "Yes, I'd relocate prior to the start of the role"
    )


def _salary_anchor(gig: Gig) -> str:
    """Best salary/rate ask given the gig's stated pay band, using
    preferences for the within-band anchor pct and unstated-pay default.
    """
    pay_prefs = preferences.pay()
    pct = pay_prefs.get("anchor_within_band_pct", 85) / 100.0
    if gig.salary_max and gig.salary_min:
        target = int(gig.salary_min + pct * (gig.salary_max - gig.salary_min))
        return f"${target/1000:.0f}K (within stated ${gig.salary_min/1000:.0f}–${gig.salary_max/1000:.0f}K band)"
    if gig.salary_max:
        target = int(0.95 * gig.salary_max)
        return f"${target/1000:.0f}K (just under stated ceiling ${gig.salary_max/1000:.0f}K)"
    if gig.pay_hourly_est:
        return f"${gig.pay_hourly_est:.0f}/hr"
    target_yr = pay_prefs.get("target_annual_usd", 175000)
    target_hr = pay_prefs.get("target_hourly_usd", 90)
    return f"${target_yr/1000:.0f}K base / ${target_hr}/hr equivalent (open to scope)"


def _gig_section(index: int, gig: Gig) -> list[str]:
    brief = build_revenue_brief(gig)
    apply_target = gig.apply_url or gig.url
    company = gig.company or "the company"
    role = (gig.title or "the role").split("|")[0].strip()
    return [
        f"### {index}. [{gig.fit_score}/100] {company} — {role}",
        "",
        f"- **Apply:** {apply_target}",
        f"- **Source post:** {gig.url}",
        f"- **Salary ask:** {_salary_anchor(gig)}",
        f"- **Relocate answer:** {_relocate_answer(gig)} (this role is in {gig.location or 'unspecified'})",
        f"- **Offer angle:** {brief.offer}",
        "",
        "**Cover-letter / first-message body** (copy as-is, edit if needed):",
        "",
        "```text",
        brief.draft.split("\n\nReview before sending:", 1)[0].strip(),
        "```",
        "",
    ]


def write_crib_sheet(gigs: list[Gig], crib_dir: Path = CRIB_DIR) -> Path:
    """Write today's form-fill crib sheet to iCloud Drive."""
    crib_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d %H:%M")
    path = crib_dir / "crib_sheet.md"

    ident = preferences.identity()
    lines = [
        f"# GigPilot Crib Sheet — {today}",
        "",
        "Reference for ATS form-fill on phone. Copy values; do not auto-submit.",
        "",
        "## Identity (top of every form)",
        "",
        f"- **First name:** {ident['first_name']}",
        f"- **Last name:** {ident['last_name']}",
        f"- **Email:** {ident['email']}",
        f"- **Phone:** {ident['phone']} {ident.get('phone_note','')}".rstrip(),
        f"- **LinkedIn:** {ident['linkedin']}",
        f"- **GitHub:** {ident['github']}",
        f"- **Portfolio:** {ident['portfolio']}",
        "",
        "## Standard Greenhouse / Lever / Ashby answers",
        "",
        "| Field | Answer |",
        "| --- | --- |",
    ]
    for label, answer in STANDARD_ANSWERS:
        lines.append(f"| {label} | {answer} |")
    lines += [
        "",
        "**Resume + cover-letter uploads stay manual.** Hard line.",
        "",
        "## Background snapshot (copy-paste source for ATS essay questions)",
        "",
        "Edit `data/preferences.json` → `background_bullets` to update.",
        "",
    ]
    bullets = preferences.background_bullets()
    label_map = {
        "elevator_pitch": "Elevator pitch",
        "ai_agent_systems": "AI agent systems",
        "full_stack_web": "Full-stack web",
        "browser_automation": "Browser automation",
        "developer_tooling": "Developer tooling",
        "field_engineering": "Field engineering",
        "education": "Education",
    }
    for key, label in label_map.items():
        if key in bullets and bullets[key]:
            lines.append(f"### {label}")
            lines.append("")
            lines.append(f"> {bullets[key]}")
            lines.append("")
    lines += [
        "## Per-lead crib (top 8)",
        "",
    ]
    if not gigs:
        lines.append("No leads in this digest.")
    for i, gig in enumerate(gigs[:8], 1):
        lines.extend(_gig_section(i, gig))

    path.write_text("\n".join(lines))
    return path
