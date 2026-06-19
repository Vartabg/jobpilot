"""
Hacker News — 'Who's Hiring' monthly thread.

Each month, HN posts a thread where companies post hiring comments. Many
include AI, remote, and specific salary ranges. We search for the most
recent thread via Algolia's search API, then pull its comments.
"""

from __future__ import annotations

import re
from datetime import datetime
from html import unescape

import requests  # pyright: ignore[reportMissingModuleSource]

from jobpilot.gigs.core.logger import get_logger
from jobpilot.gigs.core.models import Gig
from jobpilot.gigs.core.scrapers.comp import detect_currency, parse_comp

log = get_logger(__name__)

HN_ALGOLIA_SEARCH = "https://hn.algolia.com/api/v1/search"
HN_ITEM = "https://hn.algolia.com/api/v1/items/{id}"

HEADERS = {
    "User-Agent": "Mozilla/5.0 gigpilot/0.1",
    "Accept": "application/json",
}

KEYWORDS_ANY = [
    # Tech + AI tags the target profile matches
    "ai", "ml", "python", "automation", "devops", "sre", "infrastructure",
    "backend", "systems", "field", "deployed", "forward", "security",
    "claude", "llm", "agent",
]
REMOTE_TAGS = ["remote", "distributed", "anywhere", "nyc", "new york"]
SEEKING_WORK_SIGNALS = (
    "résumé/cv:",
    "resume/cv:",
    "willing to relocate:",
    "technologies:",
    "location:",
    "remote:",
)

_HREF_RE = re.compile(r'<a\s+[^>]*href="([^"]+)"[^>]*>([^<]*)</a>', re.IGNORECASE)
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PLAIN_URL_RE = re.compile(r"https?://[^\s<>\"')]+", re.IGNORECASE)

_APPLY_HOST_HINTS = (
    "boards.greenhouse.io",
    "jobs.lever.co",
    "jobs.ashbyhq.com",
    "ashbyhq.com",
    "apply.workable.com",
    "myworkdayjobs.com",
    "wellfound.com",
    "smartrecruiters.com",
    "bamboohr.com",
    "rippling.com",
    "join.com",
)
_APPLY_PATH_HINTS = (
    "/careers", "/career", "/jobs", "/job/", "/apply",
    "/hiring", "/work-with-us", "/openings", "/positions", "/join-us",
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


def _extract_apply_url(html: str, plain_text: str) -> str:
    """Pick the most promising apply target from a hiring post.

    Priority: tagged <a> hrefs (mailto/ATS/careers) > plaintext apply-inbox emails
    > plaintext careers/ATS URLs > any plaintext email. Falls back to "" so the
    caller keeps the HN comment URL as the click target.
    """
    best_href = ""
    best_score = 0
    for m in _HREF_RE.finditer(html or ""):
        href = unescape(m.group(1)).strip()
        label = unescape(m.group(2)).strip()
        score = _score_apply_target(href, label)
        if score > best_score:
            best_href = href
            best_score = score

    if best_score >= 60:
        return best_href

    for email in _EMAIL_RE.findall(plain_text or ""):
        local = email.split("@", 1)[0].lower() + "@"
        if any(local == hint or local.startswith(hint[:-1]) for hint in _APPLY_INBOX_HINTS):
            return f"mailto:{email}"

    for url in _PLAIN_URL_RE.findall(plain_text or ""):
        u = url.rstrip(".,);:")
        u_l = u.lower()
        if any(h in u_l for h in _APPLY_HOST_HINTS) or any(p in u_l for p in _APPLY_PATH_HINTS):
            return u

    if best_score > 0:
        return best_href

    emails = _EMAIL_RE.findall(plain_text or "")
    if emails:
        return f"mailto:{emails[0]}"

    return ""


def _find_latest_thread_id() -> int | None:
    """Find the most recent 'Who is hiring?' thread. whoishiring user posts
    one at the start of each month, so use search_by_date to get the newest.

    Search errors propagate — collect_all records them as a source failure.
    """
    # Dedicated endpoint that sorts by date — /search_by_date not /search
    url = "https://hn.algolia.com/api/v1/search_by_date"
    params = {
        "tags": "story,author_whoishiring",
        "hitsPerPage": 5,
    }
    r = requests.get(url, params=params, headers=HEADERS, timeout=10)
    r.raise_for_status()
    hits = r.json().get("hits", [])

    for hit in hits:
        title = hit.get("title", "")
        if "who is hiring" in title.lower() and "freelancer" not in title.lower():
            log.info("Latest Who's Hiring thread: %s (id=%s, %s)",
                     title, hit.get("objectID"), hit.get("created_at"))
            return int(hit.get("objectID"))
    return None


def _strip_html(s: str) -> str:
    if not s:
        return ""
    # Preserve paragraph / line-break boundaries so first-line parsing works
    s = re.sub(r"<\s*/?\s*(p|br|div|li)[^>]*>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"<[^>]+>", " ", s)
    return unescape(s).strip()


def _first_meaningful_line(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if len(line) >= 8:
            return line
    return text.strip().split(".")[0]


def _find_header_line(text: str) -> str:
    """Return the first line that looks like 'Company | Role | ...'.

    Falls back to the first meaningful line if no pipe-formatted header
    is present (some posts are prose-only).
    """
    for line in text.splitlines():
        line = line.strip()
        if line.count("|") >= 2 and len(line) >= 12:
            return line
    return _first_meaningful_line(text)


def _parse_header_line(line: str) -> tuple[str, str]:
    """Extract (company, role) from a 'Company | Role | Location | ...' line."""
    parts = [p.strip() for p in line.split("|") if p.strip()]
    if len(parts) >= 2:
        return parts[0][:80], parts[1][:120]
    if len(parts) == 1:
        return "", parts[0][:120]
    return "", ""


def _parse_post(text: str) -> tuple[float, float, float, bool]:
    """Returns (salary_min, salary_max, hourly, is_remote)."""
    t = text.lower()
    is_remote = any(tag in t for tag in REMOTE_TAGS)
    sal_min, sal_max, hourly = parse_comp(text)
    return sal_min, sal_max, hourly, is_remote


def _keyword_hits(text: str) -> list[str]:
    t = text.lower()
    return [k for k in KEYWORDS_ANY if k in t]


def _looks_like_candidate_post(text: str) -> bool:
    """Detect HN-style 'seeking work' comments mixed into hiring feeds."""
    lower = text.lower()
    hits = sum(1 for signal in SEEKING_WORK_SIGNALS if signal in lower)
    return hits >= 3


def scrape_hn_hiring() -> list[Gig]:
    """Pull latest Who's Hiring thread, return each top-level comment as a Gig.

    Fetch errors propagate, and a missing thread raises — the thread always
    exists, so not finding it is a failure, not an empty result.
    """
    thread_id = _find_latest_thread_id()
    if not thread_id:
        raise RuntimeError("No Who's Hiring thread found in Algolia results")

    r = requests.get(HN_ITEM.format(id=thread_id), headers=HEADERS, timeout=15)
    r.raise_for_status()
    thread = r.json()

    gigs: list[Gig] = []
    for child in thread.get("children", []):
        if not child or child.get("deleted"):
            continue
        raw_html = child.get("text", "")
        text = _strip_html(raw_html)
        if not text or len(text) < 40:
            continue
        if _looks_like_candidate_post(text):
            continue

        sal_min, sal_max, hourly, remote = _parse_post(text)
        hits = _keyword_hits(text)

        header = _find_header_line(text)
        company, role = _parse_header_line(header)
        title = role or header[:120]
        apply_url = _extract_apply_url(raw_html, text)

        gigs.append(Gig(
            id=f"hn-{child.get('id')}",
            source="hn",
            title=title or "HN hiring post",
            company=company,
            url=f"https://news.ycombinator.com/item?id={child.get('id')}",
            apply_url=apply_url,
            description=text[:1200],
            location="Remote" if remote else "See post",
            posted_at=datetime.fromtimestamp(child.get("created_at_i", 0)).isoformat() if child.get("created_at_i") else "",
            salary_min=sal_min,
            salary_max=sal_max,
            pay_hourly_est=hourly,
            currency=detect_currency(text),
            tags=hits + (["remote"] if remote else []),
        ))

    log.info("HN: %d posts in thread %s", len(gigs), thread_id)
    return gigs
