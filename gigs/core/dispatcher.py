"""
Dispatch a digest to the user.

Two channels, both free and keyless:
  1. Write a markdown digest to iCloud Drive (always, as archive)
  2. Push a short summary to ntfy.sh (if NTFY_TOPIC env var is set) for phone

ntfy.sh is a free pub/sub service. The user installs the ntfy iOS app, subscribes
to a topic (a random URL-safe string they pick), and then we POST to that topic.
No signups, no API keys, no rate limits for reasonable use.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import requests  # pyright: ignore[reportMissingModuleSource]
from requests.utils import requote_uri  # pyright: ignore[reportMissingModuleSource]

from jobpilot.gigs.core.away import save_latest_leads
from jobpilot.gigs.core.crib import write_crib_sheet
from jobpilot.gigs.core.logger import get_logger
from jobpilot.gigs.core.models import Gig
from jobpilot.gigs.core import pipeline
from jobpilot.gigs.core.paths import digests_dir
from jobpilot.gigs.core.proposals import build_revenue_brief, email_body, email_subject

log = get_logger(__name__)

ICLOUD_DIR = digests_dir()
NTFY_BASE = "https://ntfy.sh"


def _fmt_pay(g: Gig) -> str:
    if g.salary_max and g.salary_min:
        return f"${g.salary_min/1000:.0f}–${g.salary_max/1000:.0f}K/yr"
    if g.salary_max:
        return f"up to ${g.salary_max/1000:.0f}K/yr"
    if g.pay_hourly_est:
        return f"${g.pay_hourly_est:.0f}/hr"
    return "pay not stated"


def _apply_target(g: Gig) -> str:
    """Mobile-tappable target if we extracted one, else the source post URL.

    For mailto: targets, returns a prefilled mailto URL with subject + body
    so the iOS Mail composer opens with the proposal already typed.

    If GIGPILOT_IOS_SHORTCUT is set to a Shortcut name, mailto targets are
    wrapped in shortcuts://run-shortcut?... so iOS launches the named
    Shortcut, which can attach the resume PDF before opening Mail. See
    docs/IOS_SHORTCUT.md for the one-time setup.
    """
    base = g.apply_url or g.url
    if not base.lower().startswith("mailto:"):
        return base

    addr = base[len("mailto:"):].split("?", 1)[0]
    subject = quote(email_subject(g))
    body = quote(email_body(g))
    mailto = f"mailto:{addr}?subject={subject}&body={body}"

    shortcut_name = os.getenv("GIGPILOT_IOS_SHORTCUT", "").strip()
    if shortcut_name:
        return f"shortcuts://run-shortcut?name={quote(shortcut_name)}&input={quote(mailto)}"
    return mailto


def _short_url(u: str, n: int = 60) -> str:
    return u if len(u) <= n else u[: n - 1] + "…"


def _header_safe(value: str) -> str:
    """Make a string safe for an HTTP header.

    requests encodes header values as Latin-1, so a company name with an
    em-dash or non-Latin script would crash the whole push with
    UnicodeEncodeError. Collapse newlines and replace what can't encode.
    """
    cleaned = " ".join(value.split())
    return cleaned.encode("latin-1", errors="replace").decode("latin-1")


def _header_safe_url(url: str) -> str:
    """Percent-encode non-ASCII in a URL so it survives a Latin-1 header
    without corrupting the link the way _header_safe's '?' replacement would."""
    return requote_uri(url)


def _build_actions(top: Gig) -> str:
    """Build the ntfy `Actions` header. Single button: Apply (`Click` header)."""
    target = _header_safe_url(_apply_target(top))
    return f"view, Apply, {target}, clear=true"


def write_markdown(gigs: list[Gig], *, source_warning: str = "") -> Path:
    """Write today's digest as markdown to iCloud Drive. Returns path."""
    ICLOUD_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    out = ICLOUD_DIR / f"digest_{today}.md"

    lines = [
        f"# Gigpilot digest — {today}",
        "",
    ]
    if source_warning:
        lines.append(f"> ⚠️ {source_warning}")
        lines.append("")
    lines += [
        f"**{len(gigs)} fresh opportunities**, sorted by fit + pay.",
        "",
        "---",
        "",
    ]

    for i, g in enumerate(gigs, 1):
        pay = _fmt_pay(g)
        company = f"**{g.company}** — " if g.company else ""
        lines.append(f"## {i}. [{g.fit_score}/100] {company}{g.title}")
        lines.append("")
        lines.append(f"- **Pay:** {pay}")
        lines.append(f"- **Location:** {g.location or 'Unknown'}")
        lines.append(f"- **Source:** {g.source}")
        if g.tags:
            lines.append(f"- **Tags:** {', '.join(g.tags[:8])}")
        if g.fit_reasons:
            lines.append(f"- **Why:** {', '.join(g.fit_reasons[:5])}")
        lines.append(f"- **Apply:** {_apply_target(g)}")
        if g.apply_url and g.apply_url != g.url:
            lines.append(f"- **Source post:** {g.url}")
        brief = build_revenue_brief(g)
        lines.append(f"- **Offer:** {brief.offer}")
        lines.append(f"- **Action:** {brief.action}")
        if g.description:
            snippet = g.description[:280].replace("\n", " ")
            lines.append("")
            lines.append(f"> {snippet}…")
        lines.append("")
        lines.append("### Draft")
        lines.append("")
        lines.append(brief.draft)
        lines.append("")
        lines.append("---")
        lines.append("")

    out.write_text("\n".join(lines))
    log.info("Wrote digest: %s (%d gigs)", out, len(gigs))
    return out


def push_ntfy(gigs: list[Gig], topic: str | None = None, *, source_warning: str = "") -> bool:
    """Send a short summary push to the user's ntfy topic (phone)."""
    topic = topic or os.getenv("NTFY_TOPIC", "")
    if not topic:
        log.info("NTFY_TOPIC not set — skipping push. To enable: install ntfy app on phone, pick a topic, export NTFY_TOPIC=that-topic")
        return False

    if not gigs:
        return False

    today = datetime.now().strftime("%a %b %d")
    top = gigs[0]
    subtitle = f"{top.fit_score}/100 {top.company or top.title[:40]}"

    body_lines = [
        f"{today}: {len(gigs)} new — tap Apply for top gig, or open GigPilot/pipeline.md",
        "",
    ]
    for g in gigs[:5]:
        pay = _fmt_pay(g)
        co = g.company or g.source
        body_lines.append(f"[{g.fit_score}] {co}: {g.title[:50]} — {pay}")
    if source_warning:
        body_lines.append("")
        body_lines.append(f"⚠ {source_warning}")

    try:
        r = requests.post(
            f"{NTFY_BASE}/{topic}",
            data="\n".join(body_lines).encode("utf-8"),
            headers={
                "Title": _header_safe(f"Gigpilot: {subtitle}"),
                "Priority": "default",
                "Tags": "briefcase,dollar",
                "Click": _header_safe_url(_apply_target(top)),
                "Actions": _build_actions(top),
            },
            timeout=10,
        )
        r.raise_for_status()
        log.info("ntfy push OK")
        return True
    except Exception as e:
        log.warning("ntfy push failed: %s", e)
        return False


def push_failure(message: str, *, title: str = "GigPilot digest FAILED", topic: str | None = None) -> bool:
    """Push a failure/heartbeat alert to the phone. Best-effort: never raises.

    Skips (returns False) when NTFY_TOPIC is unset, matching push_ntfy.
    """
    topic = topic or os.getenv("NTFY_TOPIC", "")
    if not topic:
        log.info("NTFY_TOPIC not set — skipping failure push")
        return False

    try:
        r = requests.post(
            f"{NTFY_BASE}/{topic}",
            data=message.encode("utf-8"),
            headers={
                "Title": _header_safe(title),
                "Priority": "high",
                "Tags": "rotating_light",
            },
            timeout=10,
        )
        r.raise_for_status()
        log.info("ntfy failure push OK")
        return True
    except Exception as e:
        log.warning("ntfy failure push failed: %s", e)
        return False


def dispatch(
    gigs: list[Gig],
    pipeline_rows: list[pipeline.Row] | None = None,
    *,
    source_warning: str = "",
) -> dict:
    """Write artifacts + push. The pipeline has already been written by the
    caller (cli.digest) so this just refreshes the supplementary files
    (digest archive, latest_leads, crib) and triggers the ntfy push.

    `source_warning` (from source_health.warning_line) lands in both the
    digest markdown header and the push body, so dead sources are visible
    where the user actually looks.

    Cut from previous version: daily_brief.md (replaced by pipeline.md),
    cover_letters/ PDFs (recruiters spot bulk-generated content as AI tell).
    """
    md_path = write_markdown(gigs, source_warning=source_warning)
    save_latest_leads(gigs)
    crib_path = write_crib_sheet(gigs)
    pushed = push_ntfy(gigs, source_warning=source_warning)
    return {
        "gigs": len(gigs),
        "digest_path": str(md_path),
        "crib_path": str(crib_path),
        "pushed": pushed,
    }
