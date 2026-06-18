"""Run all scrapers and record per-source health."""

from __future__ import annotations

import os
from collections.abc import Callable

from jobpilot.gigs.core.logger import get_logger
from jobpilot.gigs.core.models import Gig
from jobpilot.gigs.core.scrapers.hackernews import scrape_hn_hiring
from jobpilot.gigs.core.scrapers.himalayas import scrape_himalayas
from jobpilot.gigs.core.scrapers.remoteok import scrape_remoteok
from jobpilot.gigs.core.scrapers.upwork_exports import scrape_upwork_exports
from jobpilot.gigs.core.scrapers.weworkremotely import scrape_weworkremotely
from jobpilot.gigs.core.source_health import SourceResult, record_results

log = get_logger(__name__)


def include_upwork_exports() -> bool:
    return os.getenv("GIGPILOT_INCLUDE_UPWORK", "").lower() in {"1", "true", "yes", "on"}


def scraper_registry() -> list[tuple[str, Callable[[], list[Gig]]]]:
    scrapers: list[tuple[str, Callable[[], list[Gig]]]] = [
        ("RemoteOK", lambda: scrape_remoteok()),
        ("WeWorkRemotely", scrape_weworkremotely),
        ("Himalayas", scrape_himalayas),
        ("HN Who's Hiring", scrape_hn_hiring),
    ]
    if include_upwork_exports():
        scrapers.append(("Upwork exports", scrape_upwork_exports))
    return scrapers


def collect_all(
    on_progress: Callable[[str], None] | None = None,
) -> tuple[list[Gig], list[SourceResult]]:
    """Run every scraper. Failures are non-fatal; health is always recorded."""
    gigs: list[Gig] = []
    results: list[SourceResult] = []

    for name, fn in scraper_registry():
        try:
            batch = fn() or []
            gigs.extend(batch)
            results.append(SourceResult(name=name, fetched=len(batch), ok=True))
            log.info("%s: %d gigs", name, len(batch))
            if on_progress:
                on_progress(f"[green]✓[/] {name}: {len(batch)} gigs")
        except Exception as exc:
            log.warning("%s failed: %s", name, exc)
            results.append(
                SourceResult(name=name, fetched=0, ok=False, error=str(exc)),
            )
            if on_progress:
                on_progress(f"[red]✗[/] {name}: {exc}")

    record_results(results)
    return gigs, results