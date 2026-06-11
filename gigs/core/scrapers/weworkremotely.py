"""WeWorkRemotely — public RSS per category. No auth."""

from __future__ import annotations

import re
from html import unescape

import feedparser  # pyright: ignore[reportMissingImports]
import requests  # pyright: ignore[reportMissingModuleSource]

from jobpilot.gigs.core.logger import get_logger
from jobpilot.gigs.core.models import Gig
from jobpilot.gigs.core.scrapers.comp import parse_comp as _parse_comp
from jobpilot.gigs.core.scrapers.ids import stable_url_suffix

log = get_logger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept": "application/rss+xml, application/xml, text/xml",
}

# Categories that match the target profile (tech + remote + no physical presence required)
WWR_CATEGORIES = {
    "programming": "https://weworkremotely.com/categories/remote-programming-jobs.rss",
    "fullstack": "https://weworkremotely.com/categories/remote-full-stack-programming-jobs.rss",
    "backend": "https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss",
    "devops": "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss",
    "customer-support": "https://weworkremotely.com/categories/remote-customer-support-jobs.rss",
    "management": "https://weworkremotely.com/categories/remote-management-and-finance-jobs.rss",
}

def _strip_html(s: str) -> str:
    return unescape(re.sub(r"<[^>]+>", " ", s or "")).strip()


def _id_from_url(url: str) -> str:
    m = re.search(r"/listings?/([a-z0-9-]+)/?$", url or "")
    return f"wwr-{m.group(1) if m else stable_url_suffix(url)}"


_HREF_RE = re.compile(r'<a\s+[^>]*href="([^"]+)"[^>]*>([^<]*)</a>', re.IGNORECASE)
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_DESC_URL_RE = re.compile(r"URL:\s*(https?://[^\s<>\"')]+)", re.IGNORECASE)
_PLAIN_URL_RE = re.compile(r"https?://[^\s<>\"')]+", re.IGNORECASE)
_APPLY_HOST_HINTS = (
    "boards.greenhouse.io", "jobs.lever.co", "jobs.ashbyhq.com", "ashbyhq.com",
    "apply.workable.com", "myworkdayjobs.com", "wellfound.com",
    "smartrecruiters.com", "bamboohr.com", "rippling.com", "join.com",
    "personio.com",
)
_APPLY_PATH_HINTS = (
    "/careers", "/career", "/jobs", "/job/", "/apply",
    "/hiring", "/work-with-us", "/openings", "/positions",
)
_APPLY_INBOX_HINTS = ("jobs@", "hiring@", "careers@", "apply@", "hr@", "recruiting@", "recruit@", "talent@")


def _score_apply_target(href: str, label: str) -> int:
    href_l = href.lower()
    label_l = label.lower()
    if href_l.startswith("mailto:"):
        if any(t in href_l for t in _APPLY_INBOX_HINTS):
            return 100
        if any(t in label_l for t in ("apply", "jobs", "hiring", "careers", "email")):
            return 90
        return 80
    if any(h in href_l for h in _APPLY_HOST_HINTS):
        return 70
    if any(p in href_l for p in _APPLY_PATH_HINTS):
        return 60
    if any(t in label_l for t in ("apply", "careers", "open positions", "we're hiring")):
        return 40
    return 0


def extract_apply_url_from_listing(html: str) -> str:
    """Pull the most promising apply target out of a WWR listing page HTML.

    WWR's listing page has the company's external apply link (mailto, ATS, or
    careers page) embedded in the body — usually wrapped in an "Apply for
    this position" anchor. Same scoring heuristic as the HN extractor.
    """
    if not html:
        return ""

    # Skip WWR's own login/account pages — they're internal navigation
    def _is_internal(href: str) -> bool:
        h = href.lower()
        return (
            "weworkremotely.com" in h
            or h.startswith("/")
            or h.startswith("#")
            or "linkedin.com/share" in h
            or "twitter.com/intent" in h
            or "facebook.com/sharer" in h
        )

    best_href = ""
    best_score = 0
    for m in _HREF_RE.finditer(html):
        href = m.group(1).strip()
        label = m.group(2).strip()
        if _is_internal(href):
            continue
        score = _score_apply_target(href, label)
        if score > best_score:
            best_href = href
            best_score = score

    if best_score >= 60:
        return best_href

    # Fallback: any plaintext jobs@ inbox in the page body
    for email in _EMAIL_RE.findall(html):
        local = email.split("@", 1)[0].lower() + "@"
        if any(local == hint or local.startswith(hint[:-1]) for hint in _APPLY_INBOX_HINTS):
            return f"mailto:{email}"

    if best_score > 0:
        return best_href

    emails = _EMAIL_RE.findall(html)
    if emails:
        return f"mailto:{emails[0]}"

    return ""


def _extract_from_description(desc: str) -> str:
    """Find the company's URL or jobs email from the WWR description text.

    WWR formats descriptions with a `URL: https://company.com` line for most
    listings, plus the company prose. We prefer plaintext jobs@ inboxes,
    then careers/jobs paths in any URL, then the company homepage URL.
    """
    if not desc:
        return ""

    # Plaintext jobs@ inbox (rare but high-signal)
    for email in _EMAIL_RE.findall(desc):
        local = email.split("@", 1)[0].lower() + "@"
        if any(local == hint or local.startswith(hint[:-1]) for hint in _APPLY_INBOX_HINTS):
            return f"mailto:{email}"

    # Find the structured `URL:` line first
    m = _DESC_URL_RE.search(desc)
    structured_url = m.group(1).rstrip(".,);:") if m else ""

    # Plaintext URLs anywhere in the description, ranked by apply-y hints
    best_href = ""
    best_score = 0
    for url in _PLAIN_URL_RE.findall(desc):
        url = url.rstrip(".,);:")
        score = _score_apply_target(url, "")
        if score > best_score:
            best_href = url
            best_score = score

    if best_score >= 60:
        return best_href
    if structured_url:
        return structured_url
    return best_href  # may be empty


def _google_careers_search(company: str) -> str:
    """Last-resort apply URL: a Google search for '{company} careers'.

    Beats the WWR paywall every time — user lands on Google with a relevant
    query, taps the first result, finds the real apply path. ~3 extra taps
    vs. a direct apply URL, but still better than the WWR aggregator.
    """
    from urllib.parse import quote
    return f"https://www.google.com/search?q={quote(company + ' careers OR jobs OR apply')}"


def enrich_apply_urls(gigs: list[Gig], max_fetch: int = 20) -> list[Gig]:
    """Mutate WWR gigs in place with apply_url.

    Strategy (cheap → expensive):
      1. Description-based extraction (no network call) — catches the
         common `URL: https://company.com` pattern and any jobs@ email
      2. Listing page HTML fetch + parse — catches mailto/ATS links not
         already in the description; only useful for the ~1/8 of WWR
         pages that don't paywall the apply target
      3. Google search fallback when we know the company name — beats
         the WWR paywall by sending the user to a search result page
    """
    fetched = 0
    for g in gigs:
        if g.source != "wwr" or g.apply_url:
            continue

        # Step 1 — free: parse the description we already have
        from_desc = _extract_from_description(g.description)
        if from_desc:
            g.apply_url = from_desc
            continue

        # Step 2 — paid: fetch the listing page (capped)
        if fetched < max_fetch:
            try:
                r = requests.get(g.url, headers=HEADERS, timeout=10)
                r.raise_for_status()
                apply = extract_apply_url_from_listing(r.text)
                fetched += 1
                if apply:
                    g.apply_url = apply
                    continue
            except Exception as e:
                log.info("WWR enrich failed for %s: %s", g.id, e)

        # Step 3 — free fallback: Google search for company careers
        if g.company:
            g.apply_url = _google_careers_search(g.company)

    return gigs


def scrape_weworkremotely() -> list[Gig]:
    """A failed category degrades gracefully (others still count), but if
    EVERY category fails the whole source is dead — raise so collect_all
    records a failure instead of an innocent-looking zero."""
    gigs: list[Gig] = []
    seen_ids: set[str] = set()
    ok_categories = 0
    errors: list[str] = []

    for cat, url in WWR_CATEGORIES.items():
        log.info("WWR: fetching %s", cat)
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            r.raise_for_status()
            parsed = feedparser.parse(r.text)
        except Exception as e:
            log.warning("  %s failed: %s", cat, e)
            errors.append(f"{cat}: {e}")
            continue
        ok_categories += 1

        for entry in parsed.entries or []:
            title = (entry.get("title") or "").strip()
            link = (entry.get("link") or "").strip()
            if not title or not link:
                continue

            gid = _id_from_url(link)
            if gid in seen_ids:
                continue
            seen_ids.add(gid)

            desc = _strip_html(entry.get("summary", entry.get("description", "")))
            combined = f"{title} {desc}"
            sal_min, sal_max, hourly = _parse_comp(combined)

            # Title on WWR is usually "Company: Position"
            company = ""
            pos_title = title
            if ": " in title:
                company, pos_title = title.split(": ", 1)

            gigs.append(Gig(
                id=gid,
                source="wwr",
                title=pos_title.strip(),
                company=company.strip(),
                url=link,
                description=desc[:800],
                location="Remote",
                posted_at=entry.get("published", ""),
                salary_min=sal_min,
                salary_max=sal_max,
                pay_hourly_est=hourly,
                tags=[cat],
            ))

    if not ok_categories:
        raise RuntimeError(
            "all WeWorkRemotely categories failed — " + "; ".join(errors[:3]),
        )

    log.info("WWR total: %d unique gigs", len(gigs))
    return gigs
