"""Bounded public job-board scanner; never sends candidate data."""

import json
from typing import Any
from urllib.parse import quote

import requests

from .models import Board, Listing, SearchResult, SourceResult
from .source_parsers import _parse_ashby, _parse_greenhouse, _parse_lever
from .source_text import SourceError

TIMEOUT = 10
MAX_BYTES = 8 * 1024 * 1024
_SESSION = requests.Session()
_SESSION.headers.update({"Accept": "application/json", "User-Agent": "JobPilot/1.0"})
_URLS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{}/jobs?content=true",
    "lever": "https://api.lever.co/v0/postings/{}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{}",
}


def _board_url(board: Board) -> str:
    return _URLS[board.provider].format(quote(board.slug, safe=""))


def _fetch_json(board: Board, url: str) -> Any:
    name = board.provider.capitalize()
    try:
        resp = _SESSION.get(url, timeout=TIMEOUT, allow_redirects=False, stream=True)
    except requests.RequestException:
        raise SourceError(
            f"{name} board '{board.slug}' is unreachable. Check your connection and try again."
        ) from None
    try:
        if 300 <= resp.status_code < 400:
            raise SourceError(
                f"{name} board '{board.slug}' redirected and was not followed. The board may have moved; try again later."
            )
        if resp.status_code != 200:
            raise SourceError(
                f"{name} board '{board.slug}' is unavailable (HTTP {resp.status_code}). Try again later."
            )
        declared = resp.headers.get("Content-Length") or ""
        if declared.strip().isdigit() and int(declared) > MAX_BYTES:
            raise SourceError(
                f"{name} board '{board.slug}' sent an oversized response. Try again later."
            )
        buf = bytearray()
        try:
            for chunk in resp.iter_content(chunk_size=65536):
                if chunk:
                    buf += chunk
                    if len(buf) > MAX_BYTES:
                        raise SourceError(
                            f"{name} board '{board.slug}' sent an oversized response. Try again later."
                        )
        except SourceError:
            raise
        except requests.RequestException:
            raise SourceError(
                f"{name} board '{board.slug}' is unreachable. Check your connection and try again."
            ) from None
        try:
            return json.loads(bytes(buf))
        except ValueError:
            raise SourceError(
                f"{name} board '{board.slug}' returned an unexpected format. Try again later."
            ) from None
    finally:
        resp.close()


def fetch_board(board: Board) -> list[Listing]:
    """Fetch and normalize one board; raises SourceError with a safe message."""
    try:
        payload = _fetch_json(board, _board_url(board))
    except SourceError:
        raise
    except Exception:
        raise SourceError(
            f"{board.provider.capitalize()} board '{board.slug}' could not be read. Try again later."
        ) from None
    if board.provider == "greenhouse":
        return _parse_greenhouse(payload, board.slug)
    if board.provider == "lever":
        return _parse_lever(payload, board.slug)
    if board.provider == "ashby":
        return _parse_ashby(payload, board.slug)
    raise SourceError("Unsupported board. Use a Greenhouse, Lever, or Ashby board.")


def scan_boards(boards: list[Board]) -> SearchResult:
    """Scan every board; one failure never erases other boards' successes."""
    jobs: list[Listing] = []
    sources: list[SourceResult] = []
    for board in boards:
        try:
            found = fetch_board(board)
        except SourceError as exc:
            sources.append(
                SourceResult(
                    provider=board.provider,
                    slug=board.slug,
                    error=str(exc) or "Board unavailable. Try again later.",
                )
            )
        except Exception:
            sources.append(
                SourceResult(
                    provider=board.provider,
                    slug=board.slug,
                    error=f"{board.provider.capitalize()} board '{board.slug}' could not be read. Try again later.",
                )
            )
        else:
            jobs.extend(found)
            sources.append(
                SourceResult(provider=board.provider, slug=board.slug, count=len(found))
            )
    return SearchResult(jobs=jobs, sources=sources)
