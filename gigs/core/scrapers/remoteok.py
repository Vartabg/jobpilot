"""RemoteOK — public JSON API. No auth, generous rate limits."""

from __future__ import annotations

import requests  # pyright: ignore[reportMissingModuleSource]

from jobpilot.gigs.core.logger import get_logger
from jobpilot.gigs.core.models import Gig

log = get_logger(__name__)

REMOTEOK_API = "https://remoteok.com/api"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept": "application/json",
}


def scrape_remoteok(tag_filter: list[str] | None = None) -> list[Gig]:
    """Pull jobs from RemoteOK API. Optional tag filter (e.g. ['python', 'ai']).

    Fetch errors propagate — collect_all records them as a source failure
    instead of letting a dead source masquerade as "no gigs today".
    """
    r = requests.get(REMOTEOK_API, headers=HEADERS, timeout=15)
    r.raise_for_status()
    data = r.json()

    gigs: list[Gig] = []
    for j in data:
        if not isinstance(j, dict) or not j.get("id"):
            continue

        tags = [str(t).lower() for t in (j.get("tags") or [])]
        if tag_filter and not any(tf.lower() in tags for tf in tag_filter):
            continue

        gigs.append(Gig(
            id=f"remoteok-{j.get('id')}",
            source="remoteok",
            title=str(j.get("position", "")).strip() or "?",
            company=str(j.get("company", "")).strip(),
            url=str(j.get("url", "") or j.get("apply_url", "")).strip(),
            description=str(j.get("description", ""))[:800],
            location=str(j.get("location", "") or "Remote").strip(),
            posted_at=str(j.get("date", "")),
            salary_min=float(j.get("salary_min") or 0),
            salary_max=float(j.get("salary_max") or 0),
            tags=tags[:10],
        ))

    log.info("RemoteOK: %d jobs (filter=%s)", len(gigs), tag_filter or "none")
    return gigs
