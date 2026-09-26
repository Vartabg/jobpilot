import json

import pytest
import requests

from jobpilot.engine.adapters import ats_boards
from jobpilot.engine.adapters.ats_boards import (
    HttpBoards,
    html_to_text,
    parse_ashby,
    parse_greenhouse,
    parse_lever,
)
from jobpilot.engine.domain import BoardTarget, Workplace

GREENHOUSE = {
    "jobs": [
        {
            "id": 123,
            "title": "Solutions Engineer",
            "absolute_url": "https://acme.com/careers?gh_jid=123",
            "location": {"name": "Remote - US"},
            "offices": [{"name": "Austin"}],
            "updated_at": "2026-09-20T10:00:00-04:00",
            "content": "&lt;p&gt;Requirements:&lt;/p&gt;&lt;ul&gt;&lt;li&gt;3+ years of experience&lt;/li&gt;&lt;/ul&gt;",
        },
        {"id": 124, "title": ""},  # unusable rows are skipped
    ]
}
LEVER = [
    {
        "id": "abc-1",
        "text": "Customer Engineer",
        "hostedUrl": "https://jobs.lever.co/beta/abc-1",
        "workplaceType": "hybrid",
        "categories": {
            "location": "Denver, CO",
            "allLocations": ["Denver, CO", "Austin, TX"],
            "commitment": "Full-time",
        },
        "descriptionPlain": "Help customers succeed.",
        "lists": [
            {"text": "Requirements", "content": "<li>2+ years in a support role</li>"},
            {"text": "Nice to have", "content": "<li>10+ years of experience</li>"},
        ],
        "salaryRange": {
            "min": 110000,
            "max": 140000,
            "currency": "USD",
            "interval": "per-year-salary",
        },
        "createdAt": 1790000000000,
    }
]
ASHBY = {
    "jobs": [
        {
            "id": "uuid-9",
            "title": "Forward Deployed Engineer",
            "jobUrl": "https://jobs.ashbyhq.com/gamma/uuid-9",
            "location": "United States",
            "secondaryLocations": [{"location": "Austin, TX"}],
            "isRemote": True,
            "workplaceType": None,
            "employmentType": "FullTime",
            "descriptionPlain": "Builders welcome.",
            "compensation": {
                "compensationTierSummary": "$140K – $200K • Offers Equity"
            },
            "publishedAt": "2026-05-27T19:16:18+00:00",
        },
        {"id": "hidden", "title": "Unlisted", "isListed": False},
    ]
}


def test_html_to_text_keeps_structure():
    assert (
        html_to_text(
            "&lt;h3&gt;About&lt;/h3&gt;&lt;p&gt;We&amp;nbsp;build.&lt;br&gt;Fast.&lt;/p&gt;"
        )
        == "About\nWe build.\nFast."
    )


def test_greenhouse():
    (posting,) = parse_greenhouse(
        GREENHOUSE, BoardTarget("greenhouse", "Acme", "Acme Inc")
    )
    assert (
        posting.listing.identity_key() == "greenhouse:acme:123"
    )  # custom careers URL, same identity
    assert posting.listing.company == "Acme Inc"
    assert posting.locations == ("Remote - US", "Austin")
    assert posting.description == "Requirements:\n- 3+ years of experience"


def test_lever():
    (posting,) = parse_lever(LEVER, BoardTarget("lever", "beta"))
    assert posting.workplace is Workplace.HYBRID
    assert posting.locations == ("Denver, CO", "Austin, TX")
    assert posting.compensation == "$110,000 – $140,000"
    assert "Requirements:\n- 2+ years in a support role" in posting.description
    assert (
        "Nice to have:" in posting.description
    )  # stays a heading, so screening skips it
    assert posting.posted_at.startswith("2026-")


def test_ashby():
    (posting,) = parse_ashby(ASHBY, BoardTarget("ashby", "gamma"))
    assert posting.workplace is Workplace.REMOTE
    assert posting.locations == ("United States", "Austin, TX")
    assert posting.compensation == "$140K – $200K • Offers Equity"
    assert posting.listing.identity_key() == "ashby:gamma:uuid-9"


class FakeResponse:
    def __init__(self, status=200, body=b"", chunks=None):
        self.status_code = status
        self._chunks = chunks if chunks is not None else [body]

    def iter_content(self, chunk_size):
        yield from self._chunks

    def close(self):
        pass


class FakeSession:
    def __init__(self, response=None, error=None):
        self.headers = {}
        self.response, self.error, self.calls = response, error, []

    def get(self, url, **options):
        self.calls.append((url, options))
        if self.error:
            raise self.error
        return self.response


GAMMA = BoardTarget("ashby", "gamma")


def fetch(response=None, error=None, target=GAMMA):
    session = FakeSession(response, error)
    return HttpBoards(session).fetch(target), session


def test_fetch_success_is_bounded():
    result, session = fetch(FakeResponse(body=json.dumps(ASHBY).encode()))
    assert not result.error and len(result.postings) == 1
    ((url, options),) = session.calls
    assert url.endswith("/job-board/gamma?includeCompensation=true")
    assert (
        options["allow_redirects"] is False
        and options["timeout"] == ats_boards.TIMEOUT_SECONDS
    )


@pytest.mark.parametrize(
    ("response", "error", "message"),
    [
        (FakeResponse(status=302), None, "redirected"),
        (FakeResponse(status=404), None, "HTTP 404"),
        (FakeResponse(body=b"<html>"), None, "JSON"),
        (FakeResponse(body=b"{}"), None, "unexpected format"),
        (None, requests.ConnectionError(), "unreachable"),
    ],
)
def test_board_problems_are_results_not_exceptions(response, error, message):
    result, _ = fetch(response, error)
    assert message in result.error
    assert result.postings == ()


def test_oversized_responses_stop_early(monkeypatch):
    monkeypatch.setattr(ats_boards, "MAX_BYTES", 10)
    result, _ = fetch(FakeResponse(chunks=[b"12345", b"67890", b"overflow"]))
    assert "too large" in result.error


def test_unsupported_providers_are_reported():
    result, session = fetch(target=BoardTarget("indeed", "x"))
    assert "aren't supported" in result.error and session.calls == []
