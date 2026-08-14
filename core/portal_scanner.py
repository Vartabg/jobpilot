"""Portal scanner for finding roles before the application step.

Supports lightweight reads from common job board endpoints so JobPilot can
surface likely matches before opening LinkedIn or Easy Apply.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import requests  # pyright: ignore[reportMissingModuleSource]

from jobpilot.core.config import ADZUNA_API_KEY, ADZUNA_APP_ID, DATA_DIR, TIMEOUT_SHORT
from jobpilot.core.job_description import (
    normalize_job_description,
    richest_job_description,
)
from jobpilot.core.logger import get_logger
from jobpilot.core.opportunity_models import (
    SourceObservation,
    SourceRunResult,
    utc_now_iso,
)
from jobpilot.core.role_identity import canonicalize_role_url, identify_role

log = get_logger(__name__)

REPORTS_DIR = DATA_DIR / "reports"
DEFAULT_TARGETS_PATH = DATA_DIR / "portals.json"


def _http_error_type(exc: requests.HTTPError) -> str:
    status = exc.response.status_code if exc.response is not None else None
    return f"HTTP{status}" if status is not None else "HTTPError"

def _extract_snippet(raw: str) -> str:
    """Return a clean plain-text version of a raw (possibly HTML) job description.

    Strips HTML tags and normalizes whitespace. No length truncation — the
    full text is stored so that downstream consumers (LLM rewrite, display) can
    decide how much to use.
    """
    return normalize_job_description(raw)


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
    description: str = ""
    provider_tenant: str = ""
    provider_job_id: str = ""
    canonical_url: str = ""
    fetched_at: str = ""
    listing_state: str = "unknown"
    posted_at: str = ""
    expires_at: str = ""
    compensation: dict[str, Any] = field(default_factory=dict)
    workplace_type: str = ""
    source_kind: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.description = normalize_job_description(self.description)
        identity = identify_role(
            self.canonical_url or self.url,
            self.portal,
            self.provider_tenant,
            self.provider_job_id,
        )
        self.provider_tenant = identity.tenant
        self.provider_job_id = identity.provider_job_id
        self.canonical_url = identity.canonical_url

    @property
    def identity_key(self) -> str:
        return identify_role(
            self.canonical_url or self.url,
            self.portal,
            self.provider_tenant,
            self.provider_job_id,
        ).key

    def to_source_observation(self) -> SourceObservation:
        """Return the normalized evidence shape used by the opportunity ledger."""
        return SourceObservation(
            provider=self.portal,
            tenant=self.provider_tenant,
            provider_job_id=self.provider_job_id,
            canonical_url=self.canonical_url,
            fetched_at=self.fetched_at,
            listing_state=self.listing_state,
            source_kind=self.source_kind,
            company=self.company,
            title=self.title,
            description=self.description,
            location=self.location,
            posted_at=self.posted_at,
            expires_at=self.expires_at,
            compensation=self.compensation,
            workplace_type=self.workplace_type,
            metadata=self.metadata,
        )


class PortalScanner:
    """Fetch and filter jobs from common ATS boards."""

    def __init__(self, keywords: list[str] | None = None, timeout: int = TIMEOUT_SHORT):
        cleaned = [k.strip().lower() for k in (keywords or []) if k and k.strip()]
        self.keywords = list(dict.fromkeys(cleaned))
        self.timeout = timeout
        self.last_run_results: list[SourceRunResult] = []
        self._active_scan_error = ""

    def scan_targets(self, targets: list[ScanTarget]) -> list[PortalJob]:
        """Scan multiple targets and return deduplicated matches."""
        results: list[PortalJob] = []
        self.last_run_results = []

        for target in targets:
            if not target.enabled:
                self.last_run_results.append(SourceRunResult(
                    target.portal, target.value, "skipped", label=target.label,
                ))
                continue
            self._active_scan_error = ""
            try:
                portal = target.portal.lower().strip()
                if portal == "greenhouse":
                    target_jobs = self.scan_greenhouse_board(target.value, label=target.label)
                elif portal == "lever":
                    target_jobs = self.scan_lever_board(target.value, label=target.label)
                elif portal == "ashby":
                    target_jobs = self.scan_ashby_board(target.value, label=target.label)
                elif portal == "google_jobs":
                    target_jobs = self.scan_google_jobs(target.value, label=target.label)
                elif portal == "indeed":
                    target_jobs = self.scan_indeed(target.value, label=target.label)
                elif portal == "adzuna":
                    target_jobs = self.scan_adzuna(target.value, label=target.label)
                else:
                    log.info("Skipping unsupported portal target: %s", target.portal)
                    self.last_run_results.append(SourceRunResult(
                        portal, target.value, "failure", label=target.label,
                        error_type="UnsupportedPortal", error="scan unavailable",
                    ))
                    continue
                results.extend(target_jobs)
                failed = bool(self._active_scan_error)
                self.last_run_results.append(SourceRunResult(
                    portal,
                    target.value,
                    "failure" if failed else ("success" if target_jobs else "zero"),
                    result_count=len(target_jobs),
                    label=target.label,
                    error_type=self._active_scan_error,
                    error="scan failed" if failed else "",
                ))
            except requests.HTTPError as exc:
                error_type = _http_error_type(exc)
                log.info("Portal target unavailable: %s (%s)", target.portal, error_type)
                self.last_run_results.append(SourceRunResult(
                    target.portal,
                    target.value,
                    "failure",
                    label=target.label,
                    error_type=error_type,
                    error="scan failed",
                ))
            except requests.RequestException as exc:
                log.info(
                    "Portal request failed: %s (%s)",
                    target.portal,
                    exc.__class__.__name__,
                )
                self.last_run_results.append(SourceRunResult(
                    target.portal,
                    target.value,
                    "failure",
                    label=target.label,
                    error_type=exc.__class__.__name__,
                    error="scan failed",
                ))
            except Exception as exc:
                log.warning("Portal scan failed for %s:%s — %s", target.portal, target.value, exc)
                self.last_run_results.append(SourceRunResult(
                    target.portal, target.value, "failure", label=target.label,
                    error_type=exc.__class__.__name__, error="scan failed",
                ))

        deduped: dict[str, PortalJob] = {}
        for job in results:
            deduped[job.identity_key] = job

        return list(deduped.values())

    def scan_greenhouse_board(self, board_token: str, *, label: str = "") -> list[PortalJob]:
        """Read jobs from the public Greenhouse board API."""
        url = f"https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs"
        response = requests.get(url, params={"content": "true"}, timeout=self.timeout)
        response.raise_for_status()
        payload: dict[str, Any] = response.json()

        jobs: list[PortalJob] = []
        company_name = label or board_token.replace("-", " ").title()
        fetched_at = utc_now_iso()
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
                    description=_extract_snippet(str(item.get("content") or "")),
                    provider_tenant=board_token,
                    provider_job_id=str(job_id or ""),
                    canonical_url=direct_url,
                    fetched_at=fetched_at,
                    listing_state="listed",
                    posted_at=str(item.get("published_at") or item.get("updated_at") or ""),
                    expires_at=str(item.get("expires_at") or ""),
                    compensation=self._coerce_mapping(
                        item.get("compensation") or item.get("pay_transparency")
                    ),
                    metadata={
                        "departments": item.get("departments") or [],
                        "offices": item.get("offices") or [],
                        "fields": item.get("metadata") or [],
                    },
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
        fetched_at = utc_now_iso()
        for item in payload:
            title = item.get("text", "").strip()
            categories_raw: object = item.get("categories") or {}
            categories = cast(dict[str, Any], categories_raw) if isinstance(categories_raw, dict) else {}
            location = str(categories.get("location", "")).strip()
            matched = self._matched_keywords(title, company_name, location)
            if self.keywords and not matched:
                continue

            description_parts = [richest_job_description(
                item.get("descriptionPlain"),
                item.get("description"),
            )]
            for section in item.get("lists") or []:
                if not isinstance(section, dict):
                    continue
                heading = normalize_job_description(section.get("text"))
                content = normalize_job_description(section.get("content"))
                description_parts.append("\n".join(filter(None, (heading, content))))
            description_parts.append(richest_job_description(
                item.get("additionalPlain"),
                item.get("additional"),
            ))
            description = "\n".join(dict.fromkeys(part for part in description_parts if part))
            job_url = str(item.get("hostedUrl") or item.get("applyUrl") or "").strip()
            provider_job_id = str(item.get("id") or "").strip()

            jobs.append(
                PortalJob(
                    company=item.get("company") or company_name,
                    title=title,
                    url=job_url,
                    location=location,
                    portal="lever",
                    matched_keywords=matched,
                    description=description,
                    provider_tenant=site,
                    provider_job_id=provider_job_id,
                    canonical_url=canonicalize_role_url(job_url, "lever"),
                    fetched_at=fetched_at,
                    listing_state="listed",
                    posted_at=self._coerce_timestamp(item.get("createdAt")),
                    expires_at=self._coerce_timestamp(item.get("expiresAt")),
                    compensation=self._coerce_mapping(item.get("salaryRange")),
                    workplace_type=str(item.get("workplaceType") or "").strip(),
                    metadata={"categories": categories},
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
        fetched_at = utc_now_iso()
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
                    description=richest_job_description(
                        item.get("descriptionPlain"),
                        item.get("descriptionHtml"),
                        item.get("description"),
                    ),
                    provider_tenant=org_slug,
                    provider_job_id=str(item.get("id") or "").strip(),
                    canonical_url=canonicalize_role_url(href, "ashby"),
                    fetched_at=fetched_at,
                    listing_state="listed" if item.get("isListed", True) else "closed",
                    posted_at=str(item.get("publishedAt") or ""),
                    expires_at=str(item.get("expiresAt") or ""),
                    compensation=self._coerce_mapping(item.get("compensation")),
                    workplace_type=str(item.get("workplaceType") or "").strip(),
                    metadata={
                        "department": item.get("department"),
                        "team": item.get("team"),
                        "employment_type": item.get("employmentType"),
                    },
                )
            )
        return jobs

    def scan_adzuna(
        self,
        query: str,
        *,
        label: str = "",
        location: str = "Austin, TX",
        country: str = "us",
        max_results: int = 50,
    ) -> list[PortalJob]:
        """Search Adzuna's job API — covers iCIMS, Workday, and other ATS boards.

        The `query` value in portals.json is the keyword string (e.g.
        "field service technician"). Location defaults to Austin TX.
        Requires ADZUNA_APP_ID and ADZUNA_API_KEY in the environment / .env.
        """
        if not ADZUNA_APP_ID or not ADZUNA_API_KEY:
            log.warning("Adzuna scan skipped — ADZUNA_APP_ID / ADZUNA_API_KEY not set")
            self._active_scan_error = "ConfigurationError"
            return []

        # Adzuna location: split "Austin, TX" → what="field service" where="Austin"
        where = location.split(",")[0].strip()
        url = (
            f"https://api.adzuna.com/v1/api/jobs/{country}/search/1"
            f"?app_id={ADZUNA_APP_ID}"
            f"&app_key={ADZUNA_API_KEY}"
            f"&results_per_page={max_results}"
            f"&what={urllib.parse.quote_plus(query)}"
            f"&where={urllib.parse.quote_plus(where)}"
            "&distance=15"
            "&sort_by=date"
            "&content-type=application/json"
        )
        try:
            response = requests.get(url, timeout=self.timeout)
            response.raise_for_status()
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "unknown"
            log.warning("Adzuna request failed for %r: HTTP %s", query, status)
            self._active_scan_error = _http_error_type(exc)
            return []
        except requests.RequestException as exc:
            log.warning("Adzuna request failed for %r: %s", query, exc.__class__.__name__)
            self._active_scan_error = exc.__class__.__name__
            return []
        except Exception as exc:
            log.warning("Adzuna request failed for %r: unexpected %s", query, exc.__class__.__name__)
            self._active_scan_error = exc.__class__.__name__
            return []

        payload: dict[str, Any] = response.json()
        jobs: list[PortalJob] = []
        fetched_at = utc_now_iso()

        for item in payload.get("results", []):
            title = str(item.get("title", "")).strip()
            company = str((item.get("company") or {}).get("display_name", label or query)).strip()
            loc_raw = item.get("location") or {}
            loc_parts: list[str] = loc_raw.get("area", []) if isinstance(loc_raw, dict) else []
            loc = ", ".join(loc_parts) if loc_parts else location
            job_url = str(item.get("redirect_url", "")).strip()
            matched = self._matched_keywords(title, company, loc)
            if self.keywords and not matched:
                continue
            jobs.append(PortalJob(
                company=company,
                title=title,
                url=job_url,
                location=loc,
                portal="adzuna",
                matched_keywords=matched,
                description=_extract_snippet(str(item.get("description", ""))),
                provider_job_id=str(item.get("id") or "").strip(),
                canonical_url=canonicalize_role_url(job_url, "adzuna"),
                fetched_at=fetched_at,
                listing_state="listed",
                posted_at=str(item.get("created") or ""),
                expires_at=str(item.get("expires_at") or ""),
                compensation=self._coerce_mapping({
                    "min": item.get("salary_min"),
                    "max": item.get("salary_max"),
                }),
                source_kind="aggregator",
                metadata={"category": item.get("category") or {}},
            ))

        log.info("Adzuna scan for %r in %r: %d matches", query, where, len(jobs))
        return jobs

    _BROWSER_UA = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    )

    def scan_google_jobs(self, query: str, *, label: str = "") -> list[PortalJob]:
        """Search Google Jobs and extract JobPosting JSON-LD from the response.

        Google embeds schema.org JobPosting blocks in the initial HTML for some
        queries. When it does, this gives us cross-ATS discovery (iCIMS, Workday,
        etc.) with a single requests call. When Google renders the jobs widget
        entirely in JS the list will be empty — that's a silent miss, not an error.
        """
        url = (
            "https://www.google.com/search"
            f"?q={urllib.parse.quote_plus(query)}&ibp=htl;jobs&hl=en&gl=us"
        )
        headers = {
            "User-Agent": self._BROWSER_UA,
            "Accept-Language": "en-US,en;q=0.9",
        }
        try:
            response = requests.get(url, headers=headers, timeout=self.timeout)
            response.raise_for_status()
        except requests.HTTPError as exc:
            self._active_scan_error = _http_error_type(exc)
            log.info("Google Jobs discovery unavailable (%s)", self._active_scan_error)
            return []
        except requests.RequestException as exc:
            self._active_scan_error = exc.__class__.__name__
            log.info("Google Jobs discovery unavailable (%s)", self._active_scan_error)
            return []
        except Exception as exc:
            log.warning("Google Jobs request failed for %r: %s", query, exc)
            self._active_scan_error = exc.__class__.__name__
            return []

        ld_pattern = re.compile(
            r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            re.DOTALL | re.IGNORECASE,
        )
        jobs: list[PortalJob] = []
        fetched_at = utc_now_iso()

        for m in ld_pattern.finditer(response.text):
            try:
                data = json.loads(m.group(1))
            except json.JSONDecodeError:
                continue

            items: list[dict[str, Any]] = []
            if isinstance(data, list):
                items = [d for d in data if isinstance(d, dict)]
            elif isinstance(data, dict):
                if data.get("@type") == "JobPosting":
                    items = [data]
                elif data.get("@type") == "ItemList":
                    for entry in data.get("itemListElement") or []:
                        if isinstance(entry, dict) and isinstance(entry.get("item"), dict):
                            items.append(entry["item"])

            for item in items:
                if item.get("@type") != "JobPosting":
                    continue
                title = str(item.get("title", "")).strip()
                org = item.get("hiringOrganization") or {}
                company = str(org.get("name", label or query)).strip() if isinstance(org, dict) else (label or query)
                loc_raw = item.get("jobLocation") or {}
                addr = loc_raw.get("address") or {} if isinstance(loc_raw, dict) else {}
                if isinstance(addr, dict):
                    city = str(addr.get("addressLocality", "")).strip()
                    state = str(addr.get("addressRegion", "")).strip()
                    location = ", ".join(filter(None, [city, state]))
                else:
                    location = str(addr).strip()
                job_url = str(item.get("url") or item.get("sameAs") or "").strip()
                matched = self._matched_keywords(title, company, location)
                if self.keywords and not matched:
                    continue
                jobs.append(PortalJob(
                    company=company,
                    title=title,
                    url=job_url,
                    location=location,
                    portal="google_jobs",
                    matched_keywords=matched,
                    description=_extract_snippet(str(item.get("description") or "")),
                    canonical_url=canonicalize_role_url(job_url),
                    fetched_at=fetched_at,
                    listing_state="listed",
                    posted_at=str(item.get("datePosted") or ""),
                    expires_at=str(item.get("validThrough") or ""),
                    compensation=self._coerce_mapping(item.get("baseSalary")),
                    source_kind="aggregator",
                    metadata={"employment_type": item.get("employmentType")},
                ))

        log.info("Google Jobs scan for %r: %d matches", query, len(jobs))
        return jobs

    def scan_indeed(self, query: str, *, label: str = "", location: str = "Austin, TX") -> list[PortalJob]:
        """Search Indeed job listings by keyword query.

        Indeed renders job cards server-side and embeds structured data in the
        initial HTML, making it more reliably parseable than Google Jobs.
        The `query` value in portals.json is the keyword string (e.g.
        "field service technician").
        """
        url = (
            "https://www.indeed.com/jobs"
            f"?q={urllib.parse.quote_plus(query)}"
            f"&l={urllib.parse.quote_plus(location)}"
            "&radius=15&fromage=30&sort=date"
        )
        headers = {
            "User-Agent": self._BROWSER_UA,
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        try:
            response = requests.get(url, headers=headers, timeout=self.timeout)
            response.raise_for_status()
        except requests.HTTPError as exc:
            self._active_scan_error = _http_error_type(exc)
            log.info("Indeed discovery unavailable (%s)", self._active_scan_error)
            return []
        except requests.RequestException as exc:
            self._active_scan_error = exc.__class__.__name__
            log.info("Indeed discovery unavailable (%s)", self._active_scan_error)
            return []
        except Exception as exc:
            log.warning("Indeed request failed for %r: %s", query, exc)
            self._active_scan_error = exc.__class__.__name__
            return []

        # Indeed embeds job data in a window._initialData JSON blob.
        match = re.search(r'window\._initialData\s*=\s*(\{.*?\});\s*</script>', response.text, re.DOTALL)
        jobs: list[PortalJob] = []
        fetched_at = utc_now_iso()

        if match:
            try:
                initial = json.loads(match.group(1))
                # Flatten whatever structure Indeed uses to get title+company+url.
                results_raw = (
                    initial.get("jobKeysWithTitles")
                    or initial.get("results")
                    or []
                )
                for item in results_raw if isinstance(results_raw, list) else []:
                    if not isinstance(item, dict):
                        continue
                    title = str(item.get("title") or item.get("jobTitle") or "").strip()
                    company = str(item.get("company") or item.get("companyName") or label or query).strip()
                    loc = str(item.get("formattedLocation") or item.get("location") or location).strip()
                    job_key = str(item.get("jobkey") or item.get("jobKey") or "").strip()
                    job_url = f"https://www.indeed.com/viewjob?jk={job_key}" if job_key else ""
                    if not title:
                        continue
                    matched = self._matched_keywords(title, company, loc)
                    if self.keywords and not matched:
                        continue
                    jobs.append(PortalJob(
                        company=company,
                        title=title,
                        url=job_url,
                        location=loc,
                        portal="indeed",
                        matched_keywords=matched,
                        provider_job_id=job_key,
                        canonical_url=canonicalize_role_url(job_url, "indeed"),
                        fetched_at=fetched_at,
                        listing_state="listed",
                        posted_at=str(item.get("datePublished") or item.get("datePosted") or ""),
                        source_kind="aggregator",
                    ))
            except (json.JSONDecodeError, AttributeError, TypeError) as exc:
                log.warning("Indeed JSON parse failed: %s", exc)
                self._active_scan_error = exc.__class__.__name__

        # Fallback: extract job cards from HTML anchors when JSON blob is absent.
        if not jobs:
            card_pattern = re.compile(
                r'data-jk="([^"]+)"[^>]*>.*?class="[^"]*jobTitle[^"]*"[^>]*><[^>]+>([^<]+)',
                re.DOTALL,
            )
            for jk, title in card_pattern.findall(response.text):
                title = title.strip()
                matched = self._matched_keywords(title, label or query, location)
                if self.keywords and not matched:
                    continue
                jobs.append(PortalJob(
                    company=label or query,
                    title=title,
                    url=f"https://www.indeed.com/viewjob?jk={jk}",
                    location=location,
                    portal="indeed",
                    matched_keywords=matched,
                    provider_job_id=jk,
                    canonical_url=f"https://www.indeed.com/viewjob?jk={jk}",
                    fetched_at=fetched_at,
                    listing_state="listed",
                    source_kind="aggregator",
                ))

        log.info("Indeed scan for %r in %r: %d matches", query, location, len(jobs))
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
    def load_targets(path: Path | None = None) -> list[ScanTarget]:
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

    def save_report(self, jobs: list[PortalJob], directory: Path | None = None) -> Path:
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
    def _coerce_timestamp(value: object) -> str:
        if isinstance(value, (int, float)):
            try:
                return datetime.fromtimestamp(value / 1000, tz=UTC).isoformat()
            except (OSError, OverflowError, ValueError):
                return ""
        return str(value or "").strip()

    @staticmethod
    def _coerce_mapping(value: object) -> dict[str, Any]:
        if isinstance(value, dict):
            return cast(dict[str, Any], value)
        if value not in (None, ""):
            return {"summary": str(value)}
        return {}

    @staticmethod
    def _strip_html(text: str) -> str:
        return re.sub(r"<[^>]+>", "", text).strip()
