"""Mocked tests for product.sources (no live network)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest
import requests

from product import sources
from product.models import Board
from product.sources import SourceError, fetch_board, scan_boards


class _Resp:
    def __init__(self, payload, status=200, headers=None):
        self.status_code = status
        self.headers = headers or {}
        self._body = (
            payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        )

    def close(self):
        self.closed = True

    def iter_content(self, chunk_size=65536):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i : i + chunk_size]


class _FakeSession:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        for key, value in self.routes.items():
            if key in url:
                if isinstance(value, Exception):
                    raise value
                return value
        raise AssertionError(f"unexpected URL: {url}")


def _serve(monkeypatch, routes):
    fake = _FakeSession(routes)
    monkeypatch.setattr(sources._SESSION, "get", fake.get)
    return fake


def test_greenhouse_content_remote_stable_id(monkeypatch):
    jobs = {
        "jobs": [
            {
                "id": 11,
                "title": "Backend Engineer",
                "absolute_url": "https://boards.greenhouse.io/acme/jobs/11",
                "location": {"name": "Austin, TX"},
                "isRemote": True,
                "content": "<p>Build &amp; ship</p><p>Second</p>",
            },
            {"id": 12, "title": "", "absolute_url": "", "content": "x"},
        ]
    }
    fake = _serve(monkeypatch, {"greenhouse": _Resp(jobs)})
    found = fetch_board(Board(provider="greenhouse", slug="acme"))
    assert len(found) == 1
    job = found[0]
    assert job.description == "Build & ship\n\nSecond"
    assert job.location == "Austin, TX (Remote)"
    assert job.provider == "greenhouse" and job.board == "acme"
    assert job.url == "https://boards.greenhouse.io/acme/jobs/11"
    assert fetch_board(Board(provider="greenhouse", slug="acme"))[0].id == job.id
    assert "content=true" in fake.calls[0][0]
    assert "boards-api.greenhouse.io" in fake.calls[0][0]


def test_lever_lists_additional_company_fallback(monkeypatch):
    jobs = [
        {
            "id": "a1",
            "text": "Frontend Dev",
            "hostedUrl": "https://jobs.lever.co/acme/a1",
            "company": "Acme",
            "categories": {"location": "Remote"},
            "workplaceType": "remote",
            "descriptionPlain": "Intro",
            "lists": [{"text": "Tasks", "content": "<p>Do things</p>"}],
            "additional": ["Extra"],
        },
        {
            "id": "a2",
            "text": "No company",
            "hostedUrl": "https://jobs.lever.co/acme/a2",
            "categories": {},
        },
        {"id": "a3", "text": "Bad link", "hostedUrl": "ftp://evil/x", "categories": {}},
    ]
    _serve(monkeypatch, {"lever": _Resp(jobs)})
    found = fetch_board(Board(provider="lever", slug="acme"))
    assert [j.title for j in found] == ["Frontend Dev", "No company"]
    assert found[0].description == "Intro\n\nTasks\nDo things\n\nExtra"
    assert found[0].company == "Acme" and found[1].company == "acme"


def test_ashby_locations_hybrid_not_remote(monkeypatch):
    jobs = {
        "jobs": [
            {
                "id": "z1",
                "title": "Data Analyst",
                "jobUrl": "https://jobs.ashbyhq.com/acme/z1",
                "location": "NYC",
                "secondaryLocations": [{"location": "Boston"}],
                "workplaceType": "hybrid",
                "descriptionPlain": "Analyze stuff",
            },
            {"id": "z2", "title": "", "jobUrl": "notaurl"},
        ]
    }
    _serve(monkeypatch, {"ashby": _Resp(jobs)})
    found = fetch_board(Board(provider="ashby", slug="acme"))
    assert len(found) == 1
    assert found[0].location == "NYC; Boston"
    assert "remote" not in found[0].location.lower()
    assert found[0].company == "acme"


def test_partial_outage_keeps_successes(monkeypatch):
    routes = {
        "greenhouse": _Resp(
            {
                "jobs": [
                    {
                        "id": 1,
                        "title": "G",
                        "absolute_url": "https://boards.greenhouse.io/a/jobs/1",
                    }
                ]
            }
        ),
        "lever": _Resp({"error": "boom SECRET-BODY"}, status=500),
    }
    _serve(monkeypatch, routes)
    res = scan_boards(
        [Board(provider="greenhouse", slug="a"), Board(provider="lever", slug="b")]
    )
    assert [j.title for j in res.jobs] == ["G"]
    assert len(res.sources) == 2
    assert res.sources[0].error == "" and res.sources[0].count == 1
    assert res.sources[1].count == 0 and res.sources[1].error
    assert "SECRET-BODY" not in res.sources[1].error
    assert "Traceback" not in res.sources[1].error


def test_redirect_not_followed(monkeypatch):
    fake = _serve(
        monkeypatch,
        {"ashby": _Resp({}, status=301, headers={"Location": "https://evil.example/"})},
    )
    res = scan_boards([Board(provider="ashby", slug="acme")])
    assert res.jobs == [] and "redirect" in res.sources[0].error.lower()
    assert fake.calls[0][1]["allow_redirects"] is False
    assert fake.calls[0][1]["timeout"] == sources.TIMEOUT


def test_oversize_response_rejected(monkeypatch):
    big = _Resp({"jobs": []}, headers={"Content-Length": str(9 * 1024 * 1024)})
    _serve(monkeypatch, {"lever": big})
    with pytest.raises(SourceError, match="oversized"):
        fetch_board(Board(provider="lever", slug="acme"))


def test_all_malformed_is_source_error(monkeypatch):
    bad = {
        "jobs": [
            {"title": "", "absolute_url": "notaurl"},
            {"title": "Long", "absolute_url": "https://example.com/" + "y" * 2100},
        ]
    }
    _serve(monkeypatch, {"greenhouse": _Resp(bad)})
    with pytest.raises(SourceError, match="usable titles or links"):
        fetch_board(Board(provider="greenhouse", slug="acme"))


def test_wrong_json_shape_clean_error(monkeypatch):
    _serve(monkeypatch, {"greenhouse": _Resp(["not", "a", "SECRET-DICT"])})
    with pytest.raises(SourceError, match="unexpected format") as exc:
        fetch_board(Board(provider="greenhouse", slug="acme"))
    assert "SECRET-DICT" not in str(exc.value)


def test_max_1000_jobs_per_source(monkeypatch):
    jobs = [
        {
            "id": f"i{n}",
            "text": f"Job {n}",
            "hostedUrl": f"https://jobs.lever.co/big/{n}",
        }
        for n in range(1005)
    ]
    _serve(monkeypatch, {"lever": _Resp(jobs)})
    with pytest.raises(SourceError, match="search limit"):
        fetch_board(Board(provider="lever", slug="big"))


def test_connection_failure_actionable(monkeypatch):
    _serve(monkeypatch, {"greenhouse": requests.ConnectionError("dns blew up")})
    with pytest.raises(SourceError) as exc:
        fetch_board(Board(provider="greenhouse", slug="acme"))
    assert "unreachable" in str(exc.value) and "dns" not in str(exc.value)
