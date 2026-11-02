"""Public ATS job boards (Greenhouse, Lever, Ashby) as a Boards adapter.

Reads only public board APIs and never sends candidate data. Every fetch is
bounded: a timeout, no redirects, a response-size cap, and a posting cap. Board
problems come back as ``BoardResult.error``, never as exceptions. The fetch
safeguards follow the Mac app's hardened scanner (product/sources.py).
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, unquote

import requests

from jobpilot.engine.domain import (
    BoardResult,
    BoardTarget,
    Lane,
    Listing,
    Posting,
    Workplace,
)

TIMEOUT_SECONDS = 15
MAX_BYTES = 8 * 1024 * 1024
MAX_POSTINGS = 2000
BOARD_URLS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{}/jobs?content=true",
    "lever": "https://api.lever.co/v0/postings/{}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{}?includeCompensation=true",
}
_WORKPLACES = {
    "remote": Workplace.REMOTE,
    "hybrid": Workplace.HYBRID,
    "onsite": Workplace.ONSITE,
    "on-site": Workplace.ONSITE,
    "in-office": Workplace.ONSITE,
}


class BoardError(Exception):
    """A board couldn't be read. The message is safe to show the user."""


def html_to_text(markup: str) -> str:
    """Readable plain text from board HTML, one block or list item per line."""
    text = html.unescape(markup or "")  # Greenhouse sends escaped HTML
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*li[^>]*>", "\n- ", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|tr|ul|ol|section)\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    lines = (re.sub(r"[ \t\u00a0]+", " ", line).strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line and line != "-")


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _listing(
    target: BoardTarget, job_id: str, title: str, url: str, location: str
) -> Listing:
    return Listing(
        company=target.company or target.token,
        title=title,
        url=url,
        location=location,
        lane=Lane.JOB,
        provider=target.provider,
        tenant=target.token.lower(),
        provider_job_id=job_id,
    )


def _jobs(payload: Any, key: str | None) -> list[Any]:
    jobs = payload.get(key) if key and isinstance(payload, dict) else payload
    if not isinstance(jobs, list):
        raise BoardError("the board answered in an unexpected format")
    if len(jobs) > MAX_POSTINGS:
        raise BoardError(f"the board lists more than {MAX_POSTINGS} postings")
    return [job for job in jobs if isinstance(job, dict)]


def parse_greenhouse(payload: Any, target: BoardTarget) -> list[Posting]:
    postings = []
    for job in _jobs(payload, "jobs"):
        job_id, title = str(job.get("id") or "").strip(), _text(job.get("title"))
        if not job_id or not title:
            continue
        token, safe_id = quote(unquote(target.token), safe=""), quote(job_id, safe="")
        url = (
            _text(job.get("absolute_url"))
            or f"https://boards.greenhouse.io/{token}/jobs/{safe_id}"
        )
        place = job.get("location")
        location = _text(place.get("name")) if isinstance(place, dict) else _text(place)
        offices = (
            _text(office.get("name"))
            for office in job.get("offices") or []
            if isinstance(office, dict)
        )
        postings.append(
            Posting(
                listing=_listing(target, job_id, title, url, location),
                description=html_to_text(_text(job.get("content"))),
                locations=_unique([location, *offices]),
                posted_at=_text(job.get("updated_at")),
            )
        )
    return postings


def _lever_pay(job: dict[str, Any]) -> str:
    salary = job.get("salaryRange")
    if (
        isinstance(salary, dict)
        and salary.get("currency") == "USD"
        and "year" in _text(salary.get("interval"))
    ):
        low, high = salary.get("min"), salary.get("max")
        if isinstance(low, int | float) and isinstance(high, int | float):
            return f"${int(low):,} – ${int(high):,}"
    return _text(job.get("salaryDescriptionPlain"))


def parse_lever(payload: Any, target: BoardTarget) -> list[Posting]:
    postings = []
    for job in _jobs(payload, None):
        job_id, title = _text(job.get("id")), _text(job.get("text"))
        if not job_id or not title:
            continue
        url = _text(job.get("hostedUrl")) or (
            f"https://jobs.lever.co/{quote(unquote(target.token), safe='')}/{quote(job_id, safe='')}"
        )
        categories = (
            job.get("categories") if isinstance(job.get("categories"), dict) else {}
        )
        location = _text(categories.get("location"))
        # Lever's lists are titled sections ("Requirements", "Nice to have");
        # a trailing colon keeps them recognizable as section headings.
        sections = [
            f"{_text(section.get('text'))}:\n{html_to_text(_text(section.get('content')))}"
            for section in job.get("lists") or []
            if isinstance(section, dict)
        ]
        description = "\n".join(
            part
            for part in (
                _text(job.get("descriptionPlain")),
                *sections,
                _text(job.get("additionalPlain")),
            )
            if part
        )
        created = job.get("createdAt")
        posted_at = (
            datetime.fromtimestamp(created / 1000, UTC).isoformat(timespec="seconds")
            if isinstance(created, int | float)
            else ""
        )
        postings.append(
            Posting(
                listing=_listing(target, job_id, title, url, location),
                description=description,
                workplace=_WORKPLACES.get(
                    _text(job.get("workplaceType")).lower(), Workplace.UNKNOWN
                ),
                locations=_unique(
                    [location, *map(_text, categories.get("allLocations") or [])]
                ),
                compensation=_lever_pay(job),
                employment_type=_text(categories.get("commitment")),
                posted_at=posted_at,
            )
        )
    return postings


def parse_ashby(payload: Any, target: BoardTarget) -> list[Posting]:
    postings = []
    for job in _jobs(payload, "jobs"):
        job_id, title = _text(job.get("id")), _text(job.get("title"))
        if not job_id or not title or job.get("isListed") is False:
            continue
        url = _text(job.get("jobUrl")) or (
            f"https://jobs.ashbyhq.com/{quote(unquote(target.token), safe='')}/{quote(job_id, safe='')}"
        )
        location = _text(job.get("location"))
        secondary = (
            _text(place.get("location"))
            for place in job.get("secondaryLocations") or []
            if isinstance(place, dict)
        )
        workplace = _WORKPLACES.get(_text(job.get("workplaceType")).lower()) or (
            Workplace.REMOTE if job.get("isRemote") is True else Workplace.UNKNOWN
        )
        pay = (
            job.get("compensation") if isinstance(job.get("compensation"), dict) else {}
        )
        postings.append(
            Posting(
                listing=_listing(target, job_id, title, url, location),
                description=_text(job.get("descriptionPlain"))
                or html_to_text(_text(job.get("descriptionHtml"))),
                workplace=workplace,
                locations=_unique([location, *secondary]),
                compensation=_text(pay.get("compensationTierSummary")),
                employment_type=_text(job.get("employmentType")),
                posted_at=_text(job.get("publishedAt")),
            )
        )
    return postings


PARSERS: dict[str, Callable[[Any, BoardTarget], list[Posting]]] = {
    "greenhouse": parse_greenhouse,
    "lever": parse_lever,
    "ashby": parse_ashby,
}


class HttpBoards:
    """Boards read over HTTPS from each provider's public API."""

    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "JobPilot (+https://github.com/Vartabg/jobpilot)",
            }
        )

    def fetch(self, target: BoardTarget) -> BoardResult:
        if target.provider not in BOARD_URLS:
            return BoardResult(
                target, error=f"{target.provider} boards aren't supported yet"
            )
        url = BOARD_URLS[target.provider].format(quote(unquote(target.token), safe=""))
        try:
            postings = PARSERS[target.provider](self._get_json(url), target)
        except BoardError as exc:
            return BoardResult(target, error=str(exc))
        return BoardResult(target, tuple(postings))

    def _get_json(self, url: str) -> Any:
        try:
            response = self.session.get(
                url, timeout=TIMEOUT_SECONDS, allow_redirects=False, stream=True
            )
        except requests.RequestException:
            raise BoardError("the board is unreachable") from None
        try:
            if 300 <= response.status_code < 400:
                raise BoardError("the board redirected; it may have moved")
            if response.status_code != 200:
                raise BoardError(f"the board answered HTTP {response.status_code}")
            body = bytearray()
            try:
                for chunk in response.iter_content(chunk_size=65536):
                    body += chunk
                    if len(body) > MAX_BYTES:
                        raise BoardError("the board's response was too large")
            except requests.RequestException:
                raise BoardError("the board stopped responding") from None
            try:
                return json.loads(bytes(body))
            except ValueError:
                raise BoardError("the board didn't answer with JSON") from None
        finally:
            response.close()
