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

def _veteran_answer() -> str:
    """Veteran-status answer, resolved truthfully from the jobpilot profile's
    demographics (declines if unset). Reversible: clear/edit
    demographics.veteran in data/profile.json to change it. The stored value
    may carry ATS answer-variants ('A||B||C'); display the first.
    """
    try:
        from jobpilot.core import profile_store
        prof = profile_store.get_profile_store().load()
        val = (getattr(prof, "demographics", {}) or {}).get("veteran", "")
    except Exception:
        val = ""
    val = (val or "").split("||", 1)[0].strip()
    return val or "I don't wish to answer"


def _standard_answers() -> list[tuple[str, str]]:
    """Standard ATS answers, resolved from preferences/profile so nothing
    personal is hardcoded. Location + relocate + in-office come from the user's
    data; demographics default to declining (veteran resolves from profile)."""
    ident = preferences.identity()
    ws = preferences.work_style()
    city = (ident.get("city") or "").strip()
    location = f"{city}, USA" if city and "usa" not in city.lower() else (city or "Your City, ST, USA")
    return [
        ("Phone country", "+1"),
        ("Location (city)", location),
        ("Country based in", "USA"),
        ("Authorized to work in the US?", "Yes"),
        ("Require visa sponsorship?", "No"),
        ("Willing to relocate?", ws.get("relocate_default", "Open to relocation for the right role")),
        ("In-office acknowledgment", ws.get("in_office_default", "Open to remote, hybrid, or onsite")),
        ("Privacy consent", "Consent"),
        ("Gender", "Decline To Self Identify"),
        ("Hispanic/Latino?", "Decline To Self Identify"),
        ("Race/Ethnicity", "Decline To Self Identify"),
        ("Veteran status", _veteran_answer()),
        ("Disability status", "I do not want to answer"),
        ("How did you hear about us?", "LinkedIn"),
    ]


def _is_home_metro_role(gig: Gig) -> bool:
    """True when the gig looks located in the user's home metro
    (preferences `location.home_metro_tags`; empty by default)."""
    tags = preferences.home_metro_tags()
    if not tags:
        return False
    haystack = " ".join([gig.location or "", gig.title or "", gig.description or ""]).lower()
    return any(tag in haystack for tag in tags)


def _relocate_answer(gig: Gig) -> str:
    """Per-lead relocate line. Pulls the user's data-defined answer; appends a
    'local' note for home-metro roles. No location is hardcoded here."""
    base = preferences.work_style().get(
        "relocate_default", "Open to relocation for the right role"
    )
    if _is_home_metro_role(gig):
        return f"{base} — local to this role"
    return base


def _cur(gig: Gig) -> str:
    c = (gig.currency or "USD").upper()
    return "" if c == "USD" else f" {c}"


def _salary_candidate(gig: Gig) -> str:
    """Candidate-facing 'Desired salary' value — safe to paste into an ATS
    field. Carries no anchoring strategy (don't tip the negotiation)."""
    if gig.salary_max and gig.salary_min:
        return (
            f"Open to the role; comfortable within your "
            f"${gig.salary_min/1000:.0f}–${gig.salary_max/1000:.0f}K{_cur(gig)} band"
        )
    if gig.salary_max:
        return f"Open / competitive — your posted ${gig.salary_max/1000:.0f}K{_cur(gig)} ceiling works"
    if gig.pay_hourly_est:
        return f"${gig.pay_hourly_est:.0f}{_cur(gig)}/hr"
    return "Open / competitive — happy to discuss range for the role"


def _salary_note(gig: Gig) -> str:
    """Private anchoring note for the user's eyes only — never paste this."""
    pay_prefs = preferences.pay()
    pct = pay_prefs.get("anchor_within_band_pct", 85) / 100.0
    if gig.salary_max and gig.salary_min:
        target = int(gig.salary_min + pct * (gig.salary_max - gig.salary_min))
        return f"anchor ~${target/1000:.0f}K{_cur(gig)} ({int(pct*100)}% into the ${gig.salary_min/1000:.0f}–${gig.salary_max/1000:.0f}K band)"
    if gig.salary_max:
        return f"anchor ~${int(0.95 * gig.salary_max)/1000:.0f}K{_cur(gig)} (just under the ${gig.salary_max/1000:.0f}K ceiling)"
    if gig.pay_hourly_est:
        return f"rate ${gig.pay_hourly_est:.0f}{_cur(gig)}/hr"
    target_yr = pay_prefs.get("target_annual_usd", 175000)
    target_hr = pay_prefs.get("target_hourly_usd", 90)
    return f"target ${target_yr/1000:.0f}K / ${target_hr}/hr equivalent"


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
        f"- **Desired salary (paste):** {_salary_candidate(gig)}",
        f"- **Your anchor (don't paste):** {_salary_note(gig)}",
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
    for label, answer in _standard_answers():
        lines.append(f"| {label} | {answer} |")
    lines += [
        "",
        "**Resume + cover-letter uploads stay manual.** Hard line.",
        "",
        "## Background snapshot (copy-paste source for ATS essay questions)",
        "",
        "Edit `data/gigs/preferences.json` → `background_bullets` to update.",
        "",
    ]
    bullets = preferences.background_bullets()
    default_bullets = preferences.DEFAULTS["background_bullets"]
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
        val = bullets.get(key, "")
        # Skip empty AND still-at-placeholder bullets — never paste an
        # instructional default like "Describe a full-stack project" into an
        # ATS essay field.
        if not val or val == default_bullets.get(key):
            continue
        lines.append(f"### {label}")
        lines.append("")
        lines.append(f"> {val}")
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
