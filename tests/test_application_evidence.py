"""Cross-source application evidence must suppress already-touched roles."""

import json
from pathlib import Path

import pytest

from jobpilot.core.application_evidence import (
    ApplicationEvidenceIndex,
    EvidenceRecord,
    EvidenceSourceError,
)
from jobpilot.core.application_tracker import ApplicationTracker
from jobpilot.core.opportunity_ledger import OpportunityLedger


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


def test_same_company_sibling_role_does_not_match_rejection(tmp_path: Path):
    tracker = ApplicationTracker(data_dir=tmp_path / "data")
    tracker.log_application(
        company="Acme", title="Solutions Engineer",
        url="https://jobs.example.test/acme/solutions", status="rejected",
    )
    evidence = ApplicationEvidenceIndex.build(tracker=tracker)

    assert evidence.match(
        "Acme", "Senior Solutions Engineer", "https://jobs.example.test/acme/senior"
    ) is None
    tracker.close()


def test_indeed_query_identity_does_not_suppress_another_role(tmp_path: Path):
    tracker = ApplicationTracker(data_dir=tmp_path / "data")
    tracker.log_application(
        company="Acme",
        title="Engineer A",
        url="https://www.indeed.com/viewjob?jk=ROLE-A&utm_source=feed",
        status="rejected",
    )
    evidence = ApplicationEvidenceIndex.build(tracker=tracker)

    assert evidence.match(
        "Beta", "Engineer B", "https://www.indeed.com/viewjob?jk=ROLE-B"
    ) is None
    assert evidence.match(
        "Acme", "Engineer A", "https://www.indeed.com/viewjob?jk=ROLE-A&from=web"
    ) is not None
    tracker.close()


def test_provider_identity_matches_greenhouse_alias_despite_title_variant(
    tmp_path: Path,
):
    tracker = ApplicationTracker(data_dir=tmp_path / "data")
    tracker.log_application(
        company="Acme",
        title="Customer Engineer (Remote)",
        url="https://job-boards.greenhouse.io/acme/jobs/123?gh_src=campaign",
        status="applied",
    )
    evidence = ApplicationEvidenceIndex.build(tracker=tracker)

    match = evidence.match(
        "Acme",
        "Customer Engineer",
        "https://boards.greenhouse.io/acme/jobs/123",
    )

    assert match is not None
    assert match.status == "applied"
    tracker.close()


def test_provider_identity_mismatch_blocks_same_company_title_fallback() -> None:
    evidence = ApplicationEvidenceIndex([
        EvidenceRecord(
            company="Acme",
            title="Customer Engineer",
            url="https://boards.greenhouse.io/acme/jobs/123",
            status="rejected",
        )
    ])

    assert evidence.match(
        "Acme",
        "Customer Engineer",
        "https://boards.greenhouse.io/acme/jobs/456",
    ) is None


def test_company_normalization_keeps_intrinsic_and_geographic_words() -> None:
    evidence = ApplicationEvidenceIndex([
        EvidenceRecord(company="US Foods", title="Implementation Engineer"),
        EvidenceRecord(company="The Company Store", title="Support Engineer"),
    ])

    assert evidence.match("Foods", "Implementation Engineer", "") is None
    assert evidence.match("The Store", "Support Engineer", "") is None


def test_company_normalization_strips_only_trailing_legal_suffixes() -> None:
    evidence = ApplicationEvidenceIndex([
        EvidenceRecord(
            company="Crown Equipment Corporation",
            title="Field Service Engineer",
        )
    ])

    assert evidence.match("Crown Equipment", "Field Service Engineer", "") is not None


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


def test_import_retains_dates_provenance_and_conflicting_sources(tmp_path: Path):
    tracker = ApplicationTracker(data_dir=tmp_path / "tracker")
    tracker.log_application(
        company="Acme", title="Solutions Engineer", status="rejected",
        applied_at="2026-08-10", source="manual-review",
    )
    employment = tmp_path / "Employment"
    packet = employment / "Acme-Solutions-2026-08-09"
    packet.mkdir(parents=True)
    (packet / "SUBMITTED.md").write_text("# Acme — Solutions Engineer\n")
    gmail_cache = tmp_path / "gmail.json"
    gmail_cache.write_text(json.dumps({
        "generated_at": "2026-08-13T12:00:00+00:00",
        "records": [{
            "company": "Acme", "title": "Solutions Engineer",
            "status": "interview", "date": "2026-08-12",
            "message_id": "gmail-message-1",
        }],
    }))
    evidence = ApplicationEvidenceIndex.build(
        tracker=tracker, employment_dir=employment, gmail_cache_path=gmail_cache
    )
    ledger = OpportunityLedger(data_dir=tmp_path / "ledger")

    assert evidence.import_into(ledger) == 3
    assert evidence.import_into(ledger) == 0
    events = ledger.iter_events()
    assert [event.event_type for event in events] == [
        "applied", "rejected", "interview",
    ]
    assert [event.occurred_at for event in events] == [
        "2026-08-09", "2026-08-10", "2026-08-12",
    ]
    gmail = events[-1]
    assert gmail.source == "gmail"
    assert gmail.provenance["message_id"] == "gmail-message-1"
    assert gmail.provenance["original_status"] == "interview"
    assert ledger.conflicting_outcomes(gmail.opportunity_id) == {
        "interview", "rejected",
    }
    ledger.close()
    tracker.close()


def test_import_without_source_date_remains_idempotent(tmp_path: Path):
    tracker = ApplicationTracker(data_dir=tmp_path / "tracker")
    cache = tmp_path / "gmail.json"
    cache.write_text(json.dumps({
        "generated_at": "2026-08-13T12:00:00+00:00",
        "records": [{
            "company": "Acme", "title": "Role", "status": "human_reply",
        }],
    }))
    evidence = ApplicationEvidenceIndex.build(
        tracker=tracker, gmail_cache_path=cache
    )
    ledger = OpportunityLedger(data_dir=tmp_path / "ledger")

    assert evidence.import_into(ledger) == 1
    assert evidence.import_into(ledger) == 0
    ledger.close()
    tracker.close()
