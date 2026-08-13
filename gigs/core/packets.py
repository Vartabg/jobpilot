"""Application and outreach packet generation."""

from __future__ import annotations

from jobpilot.core import profile_store
from jobpilot.gigs.core import preferences
from jobpilot.gigs.core.models import Gig
from jobpilot.gigs.core.proposals import build_revenue_brief, pick_offer

_BACKGROUND_LABELS = {
    "elevator_pitch": "Elevator pitch",
    "ai_agent_systems": "AI / agent systems",
    "full_stack_web": "Full-stack / web",
    "browser_automation": "Browser automation / integrations",
    "developer_tooling": "Developer tooling / infrastructure",
    "field_engineering": "Field engineering / domain experience",
    "education": "Education",
}


def _explicit_background(prefs: dict) -> list[tuple[str, str]]:
    """Return only background statements the user replaced with real facts."""
    configured = preferences.background_bullets(prefs)
    defaults = preferences.DEFAULTS["background_bullets"]
    return [
        (_BACKGROUND_LABELS.get(key, key.replace("_", " ").title()), value.strip())
        for key, value in configured.items()
        if isinstance(value, str)
        and value.strip()
        and value.strip() != str(defaults.get(key, "")).strip()
    ]


def _explicit_identity(prefs: dict) -> list[tuple[str, str]]:
    """Return configured identity fields without displaying shipped placeholders."""
    ident = preferences.identity(prefs)
    defaults = preferences.DEFAULTS["identity"]
    rows: list[tuple[str, str]] = []
    name = " ".join(
        value
        for key in ("first_name", "last_name")
        if (value := ident.get(key, "").strip()) and value != defaults[key]
    )
    if name:
        rows.append(("Name", name))
    for key, label in (("email", "Email"), ("phone", "Phone"), ("city", "Location")):
        value = ident.get(key, "").strip()
        if value and value != defaults.get(key):
            rows.append((label, value))
    return rows


def _explicit_links(prefs: dict) -> list[tuple[str, str]]:
    """Return configured profile/outreach URLs, deduplicated by destination."""
    ident = preferences.identity(prefs)
    pages = preferences.links(prefs)
    candidates = [
        ("Portfolio", ident.get("portfolio", ""), preferences.DEFAULTS["identity"]["portfolio"]),
        ("LinkedIn", ident.get("linkedin", ""), preferences.DEFAULTS["identity"]["linkedin"]),
        ("GitHub", ident.get("github", ""), preferences.DEFAULTS["identity"]["github"]),
        ("Work page", pages.get("work_page", ""), preferences.DEFAULTS["links"]["work_page"]),
        ("Service page", pages.get("service_page", ""), preferences.DEFAULTS["links"]["service_page"]),
    ]
    rows: list[tuple[str, str]] = []
    seen: set[str] = set()
    for label, raw, default in candidates:
        value = str(raw or "").strip()
        if value and value != default and value not in seen:
            rows.append((label, value))
            seen.add(value)
    return rows


def _authorization_lines() -> list[str]:
    """Render only explicit tri-state answers from the local user profile."""
    try:
        profile = profile_store.get_profile_store().load()
    except Exception:
        return ["- Setup needed: work authorization and sponsorship are not configured."]
    lines: list[str] = []
    unknown: list[str] = []
    if profile.authorized_to_work is True:
        lines.append("- Work authorization: Yes (configured in local profile).")
    elif profile.authorized_to_work is False:
        lines.append("- Work authorization: No (configured in local profile).")
    else:
        unknown.append("work authorization")
    if profile.requires_sponsorship is True:
        lines.append("- Requires sponsorship: Yes (configured in local profile).")
    elif profile.requires_sponsorship is False:
        lines.append("- Requires sponsorship: No (configured in local profile).")
    else:
        unknown.append("sponsorship")
    if unknown:
        lines.append(f"- Setup needed: configure {' and '.join(unknown)} before answering.")
    return lines


def fmt_pay(gig: Gig) -> str:
    cur = "" if (gig.currency or "USD").upper() == "USD" else f" {gig.currency.upper()}"
    if gig.salary_max and gig.salary_min:
        return f"${gig.salary_min/1000:.0f}-${gig.salary_max/1000:.0f}K{cur}/yr"
    if gig.salary_max:
        return f"up to ${gig.salary_max/1000:.0f}K{cur}/yr"
    if gig.pay_hourly_est:
        return f"${gig.pay_hourly_est:.0f}{cur}/hr"
    return "pay not stated"


def lead_line(index: int, gig: Gig) -> str:
    company = f"{gig.company} - " if gig.company else ""
    return f"{index}. [{gig.fit_score}/100] {company}{gig.title} ({fmt_pay(gig)})"


def tailor_result(index: int, gig: Gig) -> list[str]:
    brief = build_revenue_brief(gig)
    apply_target = gig.apply_url or gig.url
    out = [
        f"## Lead {index}: {gig.title}",
        "",
        f"- Offer: {brief.offer}",
        f"- Source: {gig.source}",
        f"- Pay: {fmt_pay(gig)}",
        f"- Apply: {apply_target}",
    ]
    if gig.apply_url and gig.apply_url != gig.url:
        out.append(f"- Post: {gig.url}")
    out += ["", "### Draft", "", brief.draft, ""]
    return out


def prep_result(index: int, gig: Gig) -> list[str]:
    prefs = preferences.load()
    apply_target = gig.apply_url or gig.url
    background = _explicit_background(prefs)
    identity = _explicit_identity(prefs)
    links = _explicit_links(prefs)
    authorization = _authorization_lines()
    pitch = next((value for label, value in background if label == "Elevator pitch"), "")
    resume_bullets = [(label, value) for label, value in background if label != "Elevator pitch"]
    snapshot = [
        f"## Prep packet — lead {index}: {gig.title}",
        "",
        "### Snapshot",
        "",
        f"- Fit: {gig.fit_score}/100",
        f"- Source: {gig.source}",
        f"- Pay: {fmt_pay(gig)}",
        f"- Posting-derived angle (verify): {pick_offer(gig)}",
        f"- Apply: {apply_target}",
    ]
    if gig.apply_url and gig.apply_url != gig.url:
        snapshot.append(f"- Post: {gig.url}")
    snapshot.append("")
    evidence_lines = (
        [f"- {label}: {value}" for label, value in background]
        if background
        else ["- Setup needed: add verified background bullets before claiming role fit."]
    )
    identity_lines = [f"- {label}: {value}" for label, value in identity]
    link_lines = [f"- {label}: {value}" for label, value in links]
    reference_lines = identity_lines + link_lines
    if not reference_lines:
        reference_lines = ["- Setup needed: add identity and portfolio links before using this packet."]
    pitch_lines = (
        [f"- {pitch}"]
        if pitch
        else ["- Setup needed: replace the elevator-pitch placeholder with verified wording."]
    )
    resume_lines = (
        [f"- {value}" for _, value in resume_bullets]
        if resume_bullets
        else ["- Setup needed: add verified background bullets; no resume claims were inferred."]
    )
    form_lines = [
        *authorization,
        "- Review required: availability and remote, hybrid, onsite, or relocation preferences are not inferred.",
        "- Review required: confirm compensation expectations manually for this role.",
    ]
    draft_lines = [value for _, value in background]
    if links:
        draft_lines.append(f"Configured link: {links[0][1]}")
    if not draft_lines:
        draft_lines = ["Setup needed: no candidate draft was generated from an unconfigured profile."]
    draft_lines.append("Review every statement before sending. Do not auto-submit.")

    return [
        *snapshot,
        "### Configured Candidate Evidence",
        "",
        *evidence_lines,
        "",
        "### Risk Check",
        "",
        "- Confirm it is not a pure PM, sales, support, internship, or onsite-only role.",
        "- Confirm compensation is stated or worth a discovery call.",
        "- Confirm the buyer/company has a real product and reachable contact path.",
        "",
        "### Configured Identity and Links",
        "",
        *reference_lines,
        "",
        "### Pitch Source",
        "",
        *pitch_lines,
        "",
        "### Resume Bullet Candidates",
        "",
        *resume_lines,
        "",
        "### Likely Form Answers",
        "",
        *form_lines,
        "",
        "### Draft Source Material",
        "",
        *draft_lines,
        "",
    ]
