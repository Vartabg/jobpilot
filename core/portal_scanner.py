"""Portal scanner for finding roles before the application step.

Supports lightweight reads from common job board endpoints so JobPilot can
surface likely matches before opening LinkedIn or Easy Apply.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, cast

import requests  # pyright: ignore[reportMissingModuleSource]

from jobpilot.core.config import DATA_DIR, TIMEOUT_SHORT
from jobpilot.core.logger import get_logger

log = get_logger(__name__)

REPORTS_DIR = DATA_DIR / "reports"
DEFAULT_TARGETS_PATH = DATA_DIR / "portals.json"


@dataclass
class ScanTarget:
    """A single portal target to scan."""

    portal: str
    value: str
    label: str = ""
    enabled: bool = True


@dataclass
class PortalJob:
    """A normalized job listing result."""

    company: str
    title: str
    url: str
    location: str = ""
    portal: str = ""
    matched_keywords: list[str] = field(default_factory=lambda: cast(list[str], []))


class PortalScanner:
    """Fetch and filter jobs from common ATS boards."""

    def __init__(self, keywords: Optional[list[str]] = None, timeout: int = TIMEOUT_SHORT):
        cleaned = [k.strip().lower() for k in (keywords or []) if k and k.strip()]
        self.keywords = list(dict.fromkeys(cleaned))
        self.timeout = timeout

    def scan_targets(self, targets: list[ScanTarget]) -> list[PortalJob]:
        """Scan multiple targets and return deduplicated matches."""
        results: list[PortalJob] = []

        for target in targets:
            if not target.enabled:
                continue
            try:
                portal = target.portal.lower().strip()
                if portal == "greenhouse":
                    results.extend(self.scan_greenhouse_board(target.value, label=target.label))
                elif portal == "lever":
                    results.extend(self.scan_lever_board(target.value, label=target.label))
                elif portal == "ashby":
                    results.extend(self.scan_ashby_board(target.value, label=target.label))
                else:
                    log.info("Skipping unsupported portal target: %s", target.portal)
            except Exception as exc:
                log.warning("Portal scan failed for %s:%s — %s", target.portal, target.value, exc)

        deduped: dict[str, PortalJob] = {}
        for job in results:
            deduped[job.url] = job

        return list(deduped.values())

    def scan_greenhouse_board(self, board_token: str, *, label: str = "") -> list[PortalJob]:
        """Read jobs from the public Greenhouse board API."""
        url = f"https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs"
        response = requests.get(url, timeout=self.timeout)
        response.raise_for_status()
        payload: dict[str, Any] = response.json()

        jobs: list[PortalJob] = []
        company_name = label or board_token.replace("-", " ").title()
        for item in payload.get("jobs", []):
            title = item.get("title", "").strip()
            location = self._coerce_location(item.get("location"))
            matched = self._matched_keywords(title, company_name, location)
            if self.keywords and not matched:
                continue

            # Prefer the direct Greenhouse board URL (has the apply form).
            # Company-custom absolute_url often wraps the form in the company's
            # own marketing shell (e.g. mongodb.com/careers/job/?gh_jid=...),
            # which our form filler can't penetrate.
            job_id = item.get("id")
            if job_id:
                direct_url = f"https://boards.greenhouse.io/{board_token}/jobs/{job_id}"
            else:
                direct_url = item.get("absolute_url", "").strip()

            jobs.append(
                PortalJob(
                    company=company_name,
                    title=title,
                    url=direct_url,
                    location=location,
                    portal="greenhouse",
                    matched_keywords=matched,
                )
            )
        return jobs

    def scan_lever_board(self, site: str, *, label: str = "") -> list[PortalJob]:
        """Read jobs from Lever's public postings endpoint."""
        url = f"https://api.lever.co/v0/postings/{site}?mode=json"
        response = requests.get(url, timeout=self.timeout)
        response.raise_for_status()
        payload: list[dict[str, Any]] = response.json()

        jobs: list[PortalJob] = []
        company_name = label or site.replace("-", " ").title()
        for item in payload:
            title = item.get("text", "").strip()
            categories_raw: object = item.get("categories") or {}
            categories = cast(dict[str, Any], categories_raw) if isinstance(categories_raw, dict) else {}
            location = str(categories.get("location", "")).strip()
            matched = self._matched_keywords(title, company_name, location)
            if self.keywords and not matched:
                continue

            jobs.append(
                PortalJob(
                    company=item.get("company") or company_name,
                    title=title,
                    url=item.get("hostedUrl", "").strip(),
                    location=location,
                    portal="lever",
                    matched_keywords=matched,
                )
            )
        return jobs

    def scan_ashby_board(self, org_slug: str, *, label: str = "") -> list[PortalJob]:
        """Read jobs from Ashby's public posting JSON API.

        Modern Ashby boards (`jobs.ashbyhq.com/{slug}`) are SPAs — the static
        HTML has no job anchors to scrape. The posting JSON API is the
        canonical public source.
        """
        url = f"https://api.ashbyhq.com/posting-api/job-board/{org_slug}"
        response = requests.get(url, timeout=self.timeout)
        response.raise_for_status()
        payload: dict[str, Any] = response.json()

        jobs: list[PortalJob] = []
        company_name = label or org_slug.replace("-", " ").title()
        for item in payload.get("jobs", []):
            title = str(item.get("title", "")).strip()
            location = self._coerce_ashby_location(item)
            matched = self._matched_keywords(title, company_name, location)
            if self.keywords and not matched:
                continue

            # Prefer the public job page URL; fall back to apply URL.
            href = str(item.get("jobUrl") or item.get("applyUrl") or "").strip()
            if not href:
                job_id = item.get("id")
                if job_id:
                    href = f"https://jobs.ashbyhq.com/{org_slug}/{job_id}"

            jobs.append(
                PortalJob(
                    company=company_name,
                    title=title,
                    url=href,
                    location=location,
                    portal="ashby",
                    matched_keywords=matched,
                )
            )
        return jobs

    @staticmethod
    def _coerce_ashby_location(item: dict[str, Any]) -> str:
        """Return Ashby's primary + secondary location text.

        Ashby boards are not consistent: some payloads expose `locationName`,
        while newer public posting responses expose `location` plus
        `secondaryLocations`. Missing this field is dangerous because queue
        gating may otherwise fall back to company HQ and admit international
        postings.
        """
        locations: list[str] = []
        primary = str(item.get("location") or item.get("locationName") or "").strip()
        if primary:
            locations.append(primary)
        for secondary in item.get("secondaryLocations") or []:
            if isinstance(secondary, dict):
                value = str(secondary.get("location") or secondary.get("locationName") or "").strip()
                if value:
                    locations.append(value)
        return "; ".join(dict.fromkeys(locations))

    @staticmethod
    def load_targets(path: Optional[Path] = None) -> list[ScanTarget]:
        """Load scan targets from JSON if configured."""
        target_path = path or DEFAULT_TARGETS_PATH
        if not target_path.exists():
            return []

        try:
            data = json.loads(target_path.read_text())
        except Exception as exc:
            log.warning("Could not load portal targets from %s: %s", target_path, exc)
            return []

        if not isinstance(data, list):
            return []

        targets: list[ScanTarget] = []
        for item_dict in cast(list[dict[str, Any]], data):
            portal = str(item_dict.get("portal", "")).strip()
            value = str(item_dict.get("value", "")).strip()
            if not portal or not value:
                continue
            targets.append(
                ScanTarget(
                    portal=portal,
                    value=value,
                    label=str(item_dict.get("label", "")).strip(),
                    enabled=bool(item_dict.get("enabled", True)),
                )
            )
        return targets

    def save_report(self, jobs: list[PortalJob], directory: Optional[Path] = None) -> Path:
        """Persist scan results as JSON for later review."""
        report_dir = directory or REPORTS_DIR
        report_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = report_dir / f"scan_{stamp}.json"
        path.write_text(json.dumps([asdict(job) for job in jobs], indent=2))
        return path

    def _matched_keywords(self, *parts: str) -> list[str]:
        if not self.keywords:
            return []
        haystack = " ".join(filter(None, parts)).lower()
        return [keyword for keyword in self.keywords if keyword in haystack]

    @staticmethod
    def _coerce_location(value: object) -> str:
        if isinstance(value, dict):
            value_dict = cast(dict[str, Any], value)
            return str(value_dict.get("name", "")).strip()
        if isinstance(value, str):
            return value.strip()
        return ""

    @staticmethod
    def _strip_html(text: str) -> str:
        return re.sub(r"<[^>]+>", "", text).strip()
