"""Application and outreach packet generation."""

from __future__ import annotations

from jobpilot.gigs.core import preferences
from jobpilot.gigs.core.models import Gig
from jobpilot.gigs.core.proposals import build_revenue_brief


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
    brief = build_revenue_brief(gig)
    apply_target = gig.apply_url or gig.url
    # Portfolio/identity values come from preferences (data/preferences.json,
    # gitignored); shipped defaults are neutral placeholders.
    pages = preferences.links()
    ident = preferences.identity()
    floor_hourly = preferences.pay().get("floor_hourly_usd", 65)
    snapshot = [
        f"## Prep packet — lead {index}: {gig.title}",
        "",
        "### Snapshot",
        "",
        f"- Fit: {gig.fit_score}/100",
        f"- Source: {gig.source}",
        f"- Pay: {fmt_pay(gig)}",
        f"- Offer angle: {brief.offer}",
        f"- Apply: {apply_target}",
    ]
    if gig.apply_url and gig.apply_url != gig.url:
        snapshot.append(f"- Post: {gig.url}")
    snapshot.append("")
    return snapshot + [
        "### Why This Fits",
        "",
        "- AI workflow/RAG/automation language matches your current service offer.",
        "- Python + React + local-first AI portfolio proof maps to the likely implementation path.",
        "- Systems background supports reliability, documentation, and human review.",
        "",
        "### Risk Check",
        "",
        "- Confirm it is not a pure PM, sales, support, internship, or onsite-only role.",
        "- Confirm compensation is stated or worth a discovery call.",
        "- Confirm the buyer/company has a real product and reachable contact path.",
        "",
        "### Portfolio Proof",
        "",
        f"- {pages['work_page']}",
        f"- {pages['service_page']}",
        "- Mention local-first AI, document retrieval, workflow orchestration, and React work.",
        "",
        "### Two-Sentence Pitch",
        "",
        (
            "I build practical AI workflow systems: retrieval, tool use, automation, "
            "and human-in-the-loop interfaces. I would start by mapping the current "
            "workflow, shipping the smallest useful path, and keeping sensitive data "
            "out of cloud AI unless it is explicitly safe."
        ),
        "",
        "### Resume Bullet Set",
        "",
        "- Built local-first AI/RAG workspace tooling with scheduled ingestion, retrieval, and review loops.",
        "- Built React/Three.js portfolio and visualization surfaces with mobile constraints.",
        "- Built Python automation tools for scoring, drafting, and human-approved workflow execution.",
        "- Applied high-reliability troubleshooting habits from prior hands-on engineering work.",
        "",
        "### Likely Form Answers",
        "",
        "- Work authorization: authorized to work in the U.S.",
        f"- Location: {ident['city']}; open to remote/hybrid depending on role.",
        "- Availability: available for contract discovery and bounded sprint work.",
        f"- Salary/rate: confirm based on scope; prioritize ${floor_hourly}/hr+ equivalent.",
        "",
        "### Draft",
        "",
        brief.draft,
        "",
    ]
