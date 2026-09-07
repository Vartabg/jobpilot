"""Provider shapes for the three supported public boards."""

from typing import Any
from urllib.parse import quote

from .models import Listing
from .source_text import SourceError, _lever_text, _listing, _text, _with_remote

MAX_JOBS = 1000


def _parse_greenhouse(payload: Any, slug: str) -> list[Listing]:
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise SourceError(
            f"Greenhouse board '{slug}' returned an unexpected format. Try again later."
        )
    raw = payload["jobs"]
    if len(raw) > MAX_JOBS:
        raise SourceError(
            "This board exceeds the search limit. Add a specific posting instead."
        )
    out: list[Listing] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        url = _text(item.get("absolute_url"))
        if not url and item.get("id") is not None:
            url = f"https://boards.greenhouse.io/{slug}/jobs/{quote(str(item.get('id')), safe='')}"
        loc = _text(item.get("location"))
        if not loc and isinstance(item.get("offices"), list):
            loc = next(
                (
                    _text(o.get("name")) or _text(o.get("location"))
                    for o in item["offices"]
                    if isinstance(o, dict)
                ),
                "",
            )
        job = _listing(
            "greenhouse",
            slug,
            item.get("id"),
            slug,
            item.get("title"),
            url,
            _with_remote(loc, item),
            item.get("content") or item.get("description"),
        )
        if job is not None:
            out.append(job)
    if raw and not out:
        raise SourceError(
            f"Greenhouse board '{slug}' returned postings but none had usable titles or links. Try again later."
        )
    return out


def _parse_lever(payload: Any, slug: str) -> list[Listing]:
    if not isinstance(payload, list):
        raise SourceError(
            f"Lever board '{slug}' returned an unexpected format. Try again later."
        )
    raw = payload
    if len(raw) > MAX_JOBS:
        raise SourceError(
            "This board exceeds the search limit. Add a specific posting instead."
        )
    out: list[Listing] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        cats = (
            item.get("categories") if isinstance(item.get("categories"), dict) else {}
        )
        loc = _text(cats.get("location")) or _text(item.get("location"))
        job = _listing(
            "lever",
            slug,
            item.get("id"),
            item.get("company") or item.get("companyName") or slug,
            item.get("text"),
            item.get("hostedUrl") or item.get("applyUrl"),
            _with_remote(loc, item),
            _lever_text(item),
        )
        if job is not None:
            out.append(job)
    if raw and not out:
        raise SourceError(
            f"Lever board '{slug}' returned postings but none had usable titles or links. Try again later."
        )
    return out


def _parse_ashby(payload: Any, slug: str) -> list[Listing]:
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise SourceError(
            f"Ashby board '{slug}' returned an unexpected format. Try again later."
        )
    raw = payload["jobs"]
    if len(raw) > MAX_JOBS:
        raise SourceError(
            "This board exceeds the search limit. Add a specific posting instead."
        )
    out: list[Listing] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        url = _text(item.get("jobUrl")) or _text(item.get("applyUrl"))
        if not url and item.get("id") is not None:
            url = (
                f"https://jobs.ashbyhq.com/{slug}/{quote(str(item.get('id')), safe='')}"
            )
        locs = [
            p
            for p in [_text(item.get("location")) or _text(item.get("locationName"))]
            if p
        ]
        for sec in item.get("secondaryLocations") or []:
            if isinstance(sec, dict):
                val = _text(sec.get("location")) or _text(sec.get("locationName"))
                if val and val not in locs:
                    locs.append(val)
        job = _listing(
            "ashby",
            slug,
            item.get("id"),
            item.get("company")
            or item.get("companyName")
            or item.get("organizationName")
            or slug,
            item.get("title"),
            url,
            _with_remote("; ".join(locs), item),
            item.get("descriptionPlain")
            or item.get("descriptionHtml")
            or item.get("description"),
        )
        if job is not None:
            out.append(job)
    if raw and not out:
        raise SourceError(
            f"Ashby board '{slug}' returned postings but none had usable titles or links. Try again later."
        )
    return out
