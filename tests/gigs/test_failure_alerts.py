"""Stage 3 — honest failure semantics + phone alerts on failure.

Covers: scrapers propagating instead of swallowing, collect_all recording
ok=False, failure-push composition, heartbeat staleness, and the ntfy
Latin-1 header sanitization.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest
import requests  # pyright: ignore[reportMissingModuleSource]
from typer.testing import CliRunner

from jobpilot.gigs import cli
from jobpilot.gigs.core import collect, dispatcher, run_state, source_health
from jobpilot.gigs.core.models import Gig
from jobpilot.gigs.core.scrapers.hackernews import scrape_hn_hiring
from jobpilot.gigs.core.scrapers.himalayas import scrape_himalayas
from jobpilot.gigs.core.scrapers.remoteok import scrape_remoteok
from jobpilot.gigs.core.scrapers.weworkremotely import WWR_CATEGORIES, scrape_weworkremotely

runner = CliRunner()


def _gig(**overrides) -> Gig:
    base = dict(
        id="hn-1",
        source="hn",
        title="Acme AI | Senior Engineer | NYC",
        url="https://news.ycombinator.com/item?id=1",
        apply_url="mailto:jobs@acme.ai",
        company="Acme AI",
        description="LLM workflow automation in Python.",
        salary_min=180000,
        salary_max=200000,
        fit_score=95,
    )
    base.update(overrides)
    return Gig(**base)


def _boom(*args, **kwargs):
    raise requests.exceptions.ConnectionError("dns exploded")


class _FakeResponse:
    def __init__(self, payload=None, text=""):
        self._payload = payload
        self.text = text

    def raise_for_status(self) -> None:
        pass

    def json(self):
        return self._payload


# --- Part A: scrapers propagate, collect_all records ok=False ---------------


def test_collect_all_records_failure_and_continues(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(source_health, "HEALTH_PATH", tmp_path / "health.json")

    def explode() -> list[Gig]:
        raise RuntimeError("DNS exploded")

    monkeypatch.setattr(
        collect, "scraper_registry",
        lambda: [("Boom", explode), ("Fine", lambda: [_gig()])],
    )

    gigs, results = collect.collect_all()

    by_name = {r.name: r for r in results}
    assert by_name["Boom"].ok is False
    assert by_name["Boom"].fetched == 0
    assert "DNS exploded" in (by_name["Boom"].error or "")
    # The healthy source still ran and contributed gigs
    assert by_name["Fine"].ok is True
    assert len(gigs) == 1
    # failed_sources() now actually means something
    assert ("Boom", "DNS exploded") in source_health.failed_sources()


def test_remoteok_propagates_fetch_error(monkeypatch) -> None:
    monkeypatch.setattr("jobpilot.gigs.core.scrapers.remoteok.requests.get", _boom)
    with pytest.raises(requests.exceptions.ConnectionError):
        scrape_remoteok()


def test_himalayas_propagates_fetch_error(monkeypatch) -> None:
    monkeypatch.setattr("jobpilot.gigs.core.scrapers.himalayas.requests.get", _boom)
    with pytest.raises(requests.exceptions.ConnectionError):
        scrape_himalayas()


def test_hn_propagates_search_error(monkeypatch) -> None:
    monkeypatch.setattr("jobpilot.gigs.core.scrapers.hackernews.requests.get", _boom)
    with pytest.raises(requests.exceptions.ConnectionError):
        scrape_hn_hiring()


def test_hn_raises_when_thread_missing(monkeypatch) -> None:
    # Search succeeds but no Who's Hiring thread in the hits — that's a
    # failure (the thread always exists), not an empty result.
    monkeypatch.setattr(
        "jobpilot.gigs.core.scrapers.hackernews.requests.get",
        lambda *a, **k: _FakeResponse(payload={"hits": []}),
    )
    with pytest.raises(RuntimeError, match="Who's Hiring"):
        scrape_hn_hiring()


def test_wwr_raises_when_all_categories_fail(monkeypatch) -> None:
    monkeypatch.setattr("jobpilot.gigs.core.scrapers.weworkremotely.requests.get", _boom)
    with pytest.raises(RuntimeError, match="all WeWorkRemotely categories failed"):
        scrape_weworkremotely()


def test_wwr_tolerates_partial_category_failure(monkeypatch) -> None:
    empty_rss = "<?xml version='1.0'?><rss version='2.0'><channel></channel></rss>"
    ok_url = WWR_CATEGORIES["programming"]

    def fake_get(url, **kwargs):
        if url == ok_url:
            return _FakeResponse(text=empty_rss)
        raise requests.exceptions.ConnectionError("category down")

    monkeypatch.setattr("jobpilot.gigs.core.scrapers.weworkremotely.requests.get", fake_get)
    assert scrape_weworkremotely() == []  # degraded, but not a failure


# --- Part B: failure-push composition ----------------------------------------


def test_push_failure_skips_without_topic(monkeypatch) -> None:
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    monkeypatch.setattr(dispatcher.requests, "post", _boom)
    assert dispatcher.push_failure("anything") is False


def test_push_failure_posts_title_priority_and_body(monkeypatch) -> None:
    calls: dict = {}

    def fake_post(url, data=None, headers=None, timeout=None):
        calls.update(url=url, data=data, headers=headers)
        return _FakeResponse()

    monkeypatch.setattr(dispatcher.requests, "post", fake_post)
    monkeypatch.delenv("NTFY_TOPIC", raising=False)

    assert dispatcher.push_failure(
        "GigPilot digest FAILED: RuntimeError: boom — détails",
        topic="test-topic",
    ) is True
    assert calls["url"].endswith("/test-topic")
    assert "boom" in calls["data"].decode("utf-8")
    assert calls["headers"]["Priority"] == "high"
    assert calls["headers"]["Title"] == "GigPilot digest FAILED"
    for value in calls["headers"].values():
        value.encode("latin-1")  # must never crash the requests header layer


def test_push_failure_never_raises(monkeypatch) -> None:
    monkeypatch.setattr(dispatcher.requests, "post", _boom)
    assert dispatcher.push_failure("x", topic="test-topic") is False


def test_digest_failure_records_heartbeat_and_pushes(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(run_state, "LAST_RUN_PATH", tmp_path / "last_run.json")
    pushes: list[str] = []
    monkeypatch.setattr(cli, "push_failure", lambda msg, **kw: pushes.append(msg) or True)

    def explode(**kwargs) -> None:
        raise RuntimeError("scrape exploded")

    monkeypatch.setattr(cli, "_run_digest", explode)

    result = runner.invoke(cli.app, ["digest"])

    assert result.exit_code != 0
    data = run_state.load()
    assert data["ok"] is False
    assert "scrape exploded" in data["error"]
    assert len(pushes) == 1
    assert pushes[0].startswith("GigPilot digest FAILED: RuntimeError: scrape exploded")


def test_notify_failure_subcommand_pushes(monkeypatch) -> None:
    pushes: list[tuple] = []
    monkeypatch.setattr(
        cli, "push_failure", lambda msg, **kw: pushes.append((msg, kw)) or True,
    )
    result = runner.invoke(cli.app, ["notify-failure", "digest exploded (exit 1)"])
    assert result.exit_code == 0
    assert pushes == [("digest exploded (exit 1)", {"title": "GigPilot digest FAILED"})]


# --- Part B: source warning in push body + markdown header --------------------


def test_warning_line_empty_when_healthy() -> None:
    assert source_health.warning_line(stale=[], failed=[]) == ""


def test_warning_line_mentions_failed_and_stale() -> None:
    line = source_health.warning_line(
        stale=["Himalayas"], failed=[("RemoteOK", "503 Server Error")],
    )
    assert "failed: RemoteOK" in line
    assert "Himalayas" in line


def test_write_markdown_header_carries_source_warning(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(dispatcher, "ICLOUD_DIR", tmp_path)
    path = dispatcher.write_markdown(
        [], source_warning="Source trouble — failed: RemoteOK",
    )
    head = "\n".join(path.read_text().splitlines()[:5])
    assert "failed: RemoteOK" in head


def test_push_ntfy_body_carries_source_warning(monkeypatch) -> None:
    calls: dict = {}

    def fake_post(url, data=None, headers=None, timeout=None):
        calls.update(data=data, headers=headers)
        return _FakeResponse()

    monkeypatch.setattr(dispatcher.requests, "post", fake_post)
    pushed = dispatcher.push_ntfy(
        [_gig()], topic="test-topic",
        source_warning="Source trouble — failed: RemoteOK",
    )
    assert pushed is True
    assert "failed: RemoteOK" in calls["data"].decode("utf-8")


# --- Part B: heartbeat staleness ----------------------------------------------


def test_heartbeat_staleness_logic(tmp_path, monkeypatch) -> None:
    path = tmp_path / "last_run.json"
    monkeypatch.setattr(run_state, "LAST_RUN_PATH", path)

    assert run_state.heartbeat_is_stale()  # never ran

    run_state.record_digest(ok=False, error="boom")
    assert not run_state.heartbeat_is_stale()  # a failed run still beats

    old = (datetime.now() - timedelta(hours=37)).isoformat()
    path.write_text(json.dumps({"last_digest_at": old, "ok": True}))
    assert run_state.heartbeat_is_stale()
    assert not run_state.heartbeat_is_stale(max_age_hours=100)


def test_health_heartbeat_stale_exits_nonzero_and_pushes(tmp_path, monkeypatch) -> None:
    path = tmp_path / "last_run.json"
    old = (datetime.now() - timedelta(hours=72)).isoformat()
    path.write_text(json.dumps({"last_digest_at": old, "ok": True}))
    monkeypatch.setattr(run_state, "LAST_RUN_PATH", path)
    pushes: list[tuple] = []
    monkeypatch.setattr(
        cli, "push_failure", lambda msg, **kw: pushes.append((msg, kw)) or True,
    )

    result = runner.invoke(cli.app, ["health", "--heartbeat"])

    assert result.exit_code == 1
    assert len(pushes) == 1
    assert pushes[0][1]["title"] == "GigPilot heartbeat STALE"


def test_health_heartbeat_never_ran_is_stale(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(run_state, "LAST_RUN_PATH", tmp_path / "last_run.json")
    monkeypatch.setattr(cli, "push_failure", lambda msg, **kw: True)
    result = runner.invoke(cli.app, ["health", "--heartbeat"])
    assert result.exit_code == 1


def test_health_heartbeat_fresh_exits_zero(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(run_state, "LAST_RUN_PATH", tmp_path / "last_run.json")
    run_state.record_digest(ok=True, ranked_new=2, pushed=True)
    pushes: list[str] = []
    monkeypatch.setattr(cli, "push_failure", lambda msg, **kw: pushes.append(msg) or True)

    result = runner.invoke(cli.app, ["health", "--heartbeat"])

    assert result.exit_code == 0
    assert not pushes


def test_health_without_heartbeat_never_fails(tmp_path, monkeypatch) -> None:
    # Interactive `make health` stays informational even when stale
    monkeypatch.setattr(run_state, "LAST_RUN_PATH", tmp_path / "last_run.json")
    result = runner.invoke(cli.app, ["health"])
    assert result.exit_code == 0


# --- ntfy Latin-1 header sanitization ------------------------------------------


def test_header_safe_replaces_non_latin1() -> None:
    out = dispatcher._header_safe("Gigpilot: 95/100 Acme — 株式会社")
    out.encode("latin-1")  # must not raise
    assert out.startswith("Gigpilot: 95/100 Acme")


def test_header_safe_keeps_latin1_and_collapses_newlines() -> None:
    out = dispatcher._header_safe("Müller\r\nGmbH")
    out.encode("latin-1")
    assert out == "Müller GmbH"


def test_header_safe_url_percent_encodes_non_ascii() -> None:
    out = dispatcher._header_safe_url("https://example.com/jobs/智谱?x=1")
    out.encode("latin-1")
    assert out.startswith("https://example.com/jobs/%")


def test_push_ntfy_survives_non_latin1_company(monkeypatch) -> None:
    calls: dict = {}

    def fake_post(url, data=None, headers=None, timeout=None):
        calls.update(headers=headers)
        # Mirror requests' own Latin-1 header encoding — this raised
        # UnicodeEncodeError before sanitization
        for value in headers.values():
            value.encode("latin-1")
        return _FakeResponse()

    monkeypatch.setattr(dispatcher.requests, "post", fake_post)
    g = _gig(
        company="智谱 — Müller × AI",
        apply_url="https://example.com/jobs/智谱",
    )
    assert dispatcher.push_ntfy([g], topic="test-topic") is True
    assert "Title" in calls["headers"]
