"""Cross-source deduplication before ranking.

The same role often appears on RemoteOK, WWR, and HN with different IDs.
Collapse on normalized company+title (or ATS host+title when apply URL is
known). The same key machinery also guards the pipeline against reposts:
a listing that re-appears with a fresh ID must match the row it already
has, so `listing_keys` exposes every key a listing can be known under.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from jobpilot.gigs.core.models import Gig
from jobpilot.gigs.core.scorer import _source_priority, apply_friction, score_gig

_ATS_HOST_HINTS = ("greenhouse.io", "lever.co", "ashbyhq.com", "jobs.ashbyhq.com")


def _normalize_text(value: str) -> str:
    text = re.sub(r"[^\w\s]", " ", (value or "").lower())
    return " ".join(text.split())


def _norm_title(title: str) -> str:
    """Normalize a title the same way merge_new_gigs builds Row.role
    (pre-`|` segment, stripped, capped at 80 chars) so a key computed from
    a pipeline row equals the key computed from the original gig."""
    return _normalize_text((title or "").split("|")[0].strip()[:80])


def _ats_host(apply_url: str) -> str:
    """The ATS host of an apply URL, or "" when it isn't a known ATS."""
    apply = (apply_url or "").strip()
    if not apply.lower().startswith("http"):
        return ""
    host = urlparse(apply).netloc.lower()
    if host and any(hint in host for hint in _ATS_HOST_HINTS):
        return host
    return ""


def company_title_key(company: str, title: str, source: str = "") -> str:
    """Normalized company+title key (company falls back to source)."""
    return f"co:{_normalize_text(company) or _normalize_text(source)}|{_norm_title(title)}"


def cross_source_key(gig: Gig) -> str:
    """Stable key for duplicate detection across sources."""
    host = _ats_host(gig.apply_url or gig.url)
    if host:
        return f"ats:{host}|{_norm_title(gig.title)}"
    return company_title_key(gig.company, gig.title, gig.source)


def listing_keys(
    *, title: str, company: str = "", apply_url: str = "", source: str = "",
) -> set[str]:
    """Every key this listing can match under: always the company+title
    form, plus the ATS host+title form when the apply URL is a known ATS.

    A pipeline row saved with an enriched ATS link must still match the
    same role scraped un-enriched (and vice versa), so repost detection
    compares key SETS instead of single keys."""
    keys = {company_title_key(company, title, source)}
    host = _ats_host(apply_url)
    if host:
        keys.add(f"ats:{host}|{_norm_title(title)}")
    return keys


def gig_keys(gig: Gig) -> set[str]:
    """`listing_keys` for a Gig."""
    return listing_keys(
        title=gig.title,
        company=gig.company,
        apply_url=gig.apply_url or gig.url,
        source=gig.source,
    )


def _pick_representative(group: list[Gig]) -> Gig:
    """Keep the best variant: higher fit, lower apply friction, better source."""
    scored = [score_gig(g) for g in group]
    return min(
        scored,
        key=lambda g: (
            -g.fit_score,
            apply_friction(g),
            -_source_priority(g),
            g.id,
        ),
    )


def dedupe_cross_source(gigs: list[Gig]) -> tuple[list[Gig], int]:
    """Collapse duplicate listings. Returns (unique gigs, duplicates removed)."""
    unique, removed, _ = dedupe_cross_source_with_groups(gigs)
    return unique, removed


def dedupe_cross_source_with_groups(
    gigs: list[Gig],
) -> tuple[list[Gig], int, dict[str, list[str]]]:
    """Like dedupe_cross_source, plus {representative id: [member ids]} so
    callers can mark EVERY collapsed variant as seen — marking only the
    representative lets the collapsed variants resurface as `new` next run."""
    buckets: dict[str, list[Gig]] = {}
    for gig in gigs:
        buckets.setdefault(cross_source_key(gig), []).append(gig)

    unique: list[Gig] = []
    groups: dict[str, list[str]] = {}
    for group in buckets.values():
        rep = _pick_representative(group)
        unique.append(rep)
        groups[rep.id] = [g.id for g in group]
    removed = len(gigs) - len(unique)
    return unique, removed, groups
