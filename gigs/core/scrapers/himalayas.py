"""Himalayas — public RSS. Remote jobs only."""

from __future__ import annotations

import re
from html import unescape

import feedparser  # pyright: ignore[reportMissingImports]
import requests  # pyright: ignore[reportMissingModuleSource]

from jobpilot.gigs.core.logger import get_logger
from jobpilot.gigs.core.models import Gig
from jobpilot.gigs.core.scrapers.comp import detect_currency, parse_comp
from jobpilot.gigs.core.scrapers.ids import stable_url_suffix

log = get_logger(__name__)

HIMALAYAS_FEED = "https://himalayas.app/jobs/rss"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept": "application/rss+xml, application/xml",
}


def _strip_html(s: str) -> str:
    return unescape(re.sub(r"<[^>]+>", " ", s or "")).strip()


def _id_from_url(url: str) -> str:
    m = re.search(r"/jobs?/([a-z0-9-]+)/?$", url or "")
    return f"himalayas-{m.group(1) if m else stable_url_suffix(url)}"


def scrape_himalayas() -> list[Gig]:
    """Fetch errors propagate — collect_all records them as a source failure."""
    r = requests.get(HIMALAYAS_FEED, headers=HEADERS, timeout=15)
    r.raise_for_status()
    parsed = feedparser.parse(r.text)

    gigs: list[Gig] = []
    for entry in parsed.entries or []:
        title = (entry.get("title") or "").strip()
        link = (entry.get("link") or "").strip()
        if not title or not link:
            continue

        desc = _strip_html(entry.get("summary", entry.get("description", "")))
        combined = f"{title} {desc}"
        sal_min, sal_max, hourly = parse_comp(combined)
        currency = detect_currency(combined)

        # Himalayas title pattern: "Position at Company"
        company = ""
        pos_title = title
        m = re.search(r"^(.+?)\s+at\s+(.+?)$", title)
        if m:
            pos_title = m.group(1).strip()
            company = m.group(2).strip()

        gigs.append(Gig(
            id=_id_from_url(link),
            source="himalayas",
            title=pos_title,
            company=company,
            url=link,
            description=desc[:800],
            location="Remote",
            posted_at=entry.get("published", ""),
            salary_min=sal_min,
            salary_max=sal_max,
            pay_hourly_est=hourly,
            currency=currency,
            tags=[],
        ))

    log.info("Himalayas: %d jobs", len(gigs))
    return gigs
