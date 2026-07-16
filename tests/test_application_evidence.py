"""Cross-source application evidence must suppress already-touched roles."""

import json
from pathlib import Path

import pytest

from jobpilot.core.application_evidence import (
    ApplicationEvidenceIndex,
    EvidenceSourceError,
)
from jobpilot.core.application_tracker import ApplicationTracker


def test_employment_packet_matches_same_role_only(tmp_path: Path):
    employment = tmp_path / "Employment"
    packet = employment / "Switch-DCOFacilitiesTech1-2026-07-11"
    packet.mkdir(parents=True)
    (packet / "notes.md").write_text(
        "# Switch — DCO Facilities Technician I\n\nPrepared application packet.\n"
    )
    tracker = ApplicationTracker(data_dir=tmp_path / "data")

    evidence = ApplicationEvidenceIndex.build(
        tracker=tracker,
        employment_dir=employment,
    )

    match = evidence.match(
        company="Switch",
        title="DCO Facilities Technician I",
        url="https://example.test/switch/dco-1",
    )
    assert match is not None
    assert match.source == "employment"
    assert match.status == "started"
    assert evidence.match(
        "Switch", "Network Engineer", "https://example.test/new"
    ) is None
    tracker.close()


def test_tracker_and_gmail_cache_are_merged(tmp_path: Path):
    tracker = ApplicationTracker(data_dir=tmp_path / "data")
    tracker.log_application(
        company="Acme",
        title="Solutions Engineer",
        url="https://jobs.example.test/acme/123?source=feed",
        status="submitted",
    )
    gmail_cache = tmp_path / "gmail_applications.json"
    gmail_cache.write_text(json.dumps({
        "generated_at": "2026-07-16T12:00:00-05:00",
        "records": [{
            "company": "Lightedge",
            "title": "Support Engineer",
            "status": "applied",
        }],
    }))

    evidence = ApplicationEvidenceIndex.build(
        tracker=tracker,
        gmail_cache_path=gmail_cache,
    )

    assert evidence.match(
        "Acme", "Solutions Engineer", "https://jobs.example.test/acme/123"
    ).source == "tracker"
    assert evidence.match(
        "Lightedge", "Support Engineer", "https://example.test/lightedge"
    ).source == "gmail"
    tracker.close()


def test_configured_missing_source_fails_closed(tmp_path: Path):
    tracker = ApplicationTracker(data_dir=tmp_path / "data")
    evidence = ApplicationEvidenceIndex.build(
        tracker=tracker,
        employment_dir=tmp_path / "missing-employment",
    )

    with pytest.raises(EvidenceSourceError, match="application evidence"):
        evidence.ensure_usable()
    tracker.close()


def test_stale_gmail_cache_fails_closed(tmp_path: Path):
    tracker = ApplicationTracker(data_dir=tmp_path / "data")
    cache = tmp_path / "gmail_applications.json"
    cache.write_text(json.dumps({
        "generated_at": "2000-01-01T00:00:00+00:00",
        "records": [],
    }))
    evidence = ApplicationEvidenceIndex.build(
        tracker=tracker,
        gmail_cache_path=cache,
        gmail_cache_max_age_hours=24,
    )

    with pytest.raises(EvidenceSourceError, match="cache is"):
        evidence.ensure_usable()
    tracker.close()
