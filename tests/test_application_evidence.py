"""Cross-source application evidence must suppress already-touched roles."""

import json
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from jobpilot.core.application_evidence import (
    ApplicationEvidenceIndex,
    EvidenceRecord,
    EvidenceSourceError,
    configured_external_evidence_errors,
)
from jobpilot.core.application_tracker import ApplicationTracker
from jobpilot.core.gmail_application_cache import (
    GmailCacheError,
    sync_gmail_application_cache,
)
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


def test_gmail_sync_validates_and_atomically_replaces_without_touching_source(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 14, 12, tzinfo=UTC)
    source = tmp_path / "readonly-export.json"
    source.write_text(json.dumps({
        "generated_at": (now - timedelta(minutes=10)).isoformat(),
        "records": [{
            "company": "Acme",
            "title": "Implementation Engineer",
            "status": "rejected",
            "date": "2026-08-13",
            "message_id": "gmail-message-1",
        }],
    }))
    source.chmod(0o444)
    source_before = (source.read_bytes(), source.stat().st_mtime_ns)
    destination = tmp_path / "data" / "gmail_applications.json"
    destination.parent.mkdir()
    destination.write_text(json.dumps({
        "generated_at": (now - timedelta(days=2)).isoformat(),
        "records": [],
    }))

    result = sync_gmail_application_cache(
        source,
        destination,
        max_age_hours=24,
        now=now,
    )

    payload = json.loads(destination.read_text())
    assert result.record_count == 1
    assert payload["generated_at"] == (now - timedelta(minutes=10)).isoformat()
    assert payload["records"][0]["message_id"] == "gmail-message-1"
    assert source_before == (source.read_bytes(), source.stat().st_mtime_ns)
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600


def test_gmail_sync_stale_or_malformed_export_never_replaces_cache(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 14, 12, tzinfo=UTC)
    destination = tmp_path / "gmail_applications.json"
    destination.write_text(json.dumps({
        "generated_at": (now - timedelta(hours=1)).isoformat(),
        "records": [],
    }))
    before = (destination.read_bytes(), destination.stat().st_ino)
    stale = tmp_path / "stale.json"
    stale.write_text(json.dumps({
        "generated_at": (now - timedelta(days=2)).isoformat(),
        "records": [],
    }))

    with pytest.raises(GmailCacheError, match="hours old"):
        sync_gmail_application_cache(
            stale,
            destination,
            max_age_hours=24,
            now=now,
        )
    assert before == (destination.read_bytes(), destination.stat().st_ino)

    malformed = tmp_path / "malformed.json"
    malformed.write_text(json.dumps({
        "generated_at": now.isoformat(),
        "records": [{"company": "Acme", "status": "rejected"}],
    }))
    with pytest.raises(GmailCacheError, match="company and title"):
        sync_gmail_application_cache(
            malformed,
            destination,
            max_age_hours=24,
            now=now,
        )
    assert before == (destination.read_bytes(), destination.stat().st_ino)
    assert not list(tmp_path.glob(".gmail_applications.json.*.tmp"))


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com:bad/jobs/1",
        "https://example.com:99999/jobs/1",
        "https://[::1",
    ],
)
def test_gmail_sync_rejects_malformed_role_urls_cleanly(
    tmp_path: Path,
    url: str,
) -> None:
    now = datetime(2026, 8, 14, 12, tzinfo=UTC)
    source = tmp_path / "export.json"
    source.write_text(json.dumps({
        "generated_at": now.isoformat(),
        "records": [{
            "company": "Acme",
            "title": "Engineer",
            "status": "applied",
            "url": url,
        }],
    }))

    with pytest.raises(GmailCacheError, match="unsafe role URL"):
        sync_gmail_application_cache(source, tmp_path / "cache.json", now=now)


def test_gmail_sync_rejects_extreme_timestamp_cleanly(tmp_path: Path) -> None:
    source = tmp_path / "export.json"
    source.write_text(json.dumps({
        "generated_at": "0001-01-01T00:00:00+23:59",
        "records": [],
    }))

    with pytest.raises(GmailCacheError, match="invalid generated_at"):
        sync_gmail_application_cache(source, tmp_path / "cache.json")


def test_gmail_sync_rejects_invalid_unicode_without_replacing_cache(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 14, 12, tzinfo=UTC)
    source = tmp_path / "export.json"
    source.write_text(json.dumps({
        "generated_at": now.isoformat(),
        "records": [{
            "company": "\ud800",
            "title": "Engineer",
            "status": "applied",
        }],
    }))
    destination = tmp_path / "cache.json"

    with pytest.raises(GmailCacheError, match="invalid Unicode"):
        sync_gmail_application_cache(source, destination, now=now)

    assert not destination.exists()


def test_gmail_sync_contains_json_integer_limit_errors(tmp_path: Path) -> None:
    source = tmp_path / "export.json"
    source.write_text('{"generated_at":' + "1" * 5000 + ',"records":[]}')

    with pytest.raises(GmailCacheError, match="could not be read"):
        sync_gmail_application_cache(source, tmp_path / "cache.json")


def test_gmail_sync_rejects_self_referential_destination_symlink(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 14, 12, tzinfo=UTC)
    source = tmp_path / "export.json"
    source.write_text(json.dumps({
        "generated_at": now.isoformat(),
        "records": [],
    }))
    destination = tmp_path / "cache.json"
    destination.symlink_to(destination.name)

    with pytest.raises(GmailCacheError, match="resolved safely"):
        sync_gmail_application_cache(source, destination, now=now)


def test_gmail_content_is_rejected_and_never_copied_to_private_ledger(
    tmp_path: Path,
) -> None:
    private_text = "PRIVATE INTERVIEW BODY medical accommodation details"
    cache = tmp_path / "gmail.json"
    cache.write_text(json.dumps({
        "generated_at": "2026-08-13T12:00:00+00:00",
        "records": [{
            "company": "Acme",
            "title": "Engineer",
            "status": "interview",
            "message_id": "gmail-message-1",
            "message_body": private_text,
        }],
    }))
    tracker = ApplicationTracker(data_dir=tmp_path / "tracker")
    evidence = ApplicationEvidenceIndex.build(
        tracker=tracker,
        gmail_cache_path=cache,
    )
    ledger = OpportunityLedger(data_dir=tmp_path / "ledger")

    assert evidence.records == []
    assert len(evidence.errors) == 1
    assert "message content is not accepted" in evidence.errors[0]
    assert evidence.import_into(ledger) == 0
    ledger.close()
    tracker.close()

    assert stat.S_IMODE((tmp_path / "ledger" / "opportunities.db").stat().st_mode) == 0o600
    assert private_text.encode() not in (
        tmp_path / "ledger" / "opportunities.db"
    ).read_bytes()


def test_gmail_sync_refuses_older_export_even_when_both_are_current(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 14, 12, tzinfo=UTC)
    destination = tmp_path / "gmail_applications.json"
    destination.write_text(json.dumps({
        "generated_at": (now - timedelta(minutes=5)).isoformat(),
        "records": [],
    }))
    source = tmp_path / "export.json"
    source.write_text(json.dumps({
        "generated_at": (now - timedelta(minutes=10)).isoformat(),
        "records": [],
    }))

    with pytest.raises(GmailCacheError, match="older export"):
        sync_gmail_application_cache(
            source,
            destination,
            max_age_hours=24,
            now=now,
        )


def test_gmail_sync_refuses_export_that_drops_existing_history(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 14, 12, tzinfo=UTC)
    destination = tmp_path / "gmail_applications.json"
    destination.write_text(json.dumps({
        "generated_at": (now - timedelta(hours=1)).isoformat(),
        "records": [
            {"company": "Acme", "title": "Engineer", "status": "applied"},
            {"company": "Beta", "title": "Support", "status": "rejected"},
        ],
    }))
    source = tmp_path / "export.json"
    source.write_text(json.dumps({
        "generated_at": now.isoformat(),
        "records": [
            {"company": "Acme", "title": "Engineer", "status": "rejected"},
        ],
    }))

    with pytest.raises(GmailCacheError, match="incomplete Gmail export"):
        sync_gmail_application_cache(
            source,
            destination,
            max_age_hours=24,
            now=now,
        )


def test_gmail_sync_keeps_distinct_same_title_requisitions(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 14, 12, tzinfo=UTC)
    destination = tmp_path / "gmail_applications.json"
    destination.write_text(json.dumps({
        "generated_at": (now - timedelta(hours=1)).isoformat(),
        "records": [
            {
                "company": "Acme",
                "title": "Customer Engineer",
                "status": "applied",
                "url": "https://jobs.ashbyhq.com/acme/role-old",
            },
            {
                "company": "Acme",
                "title": "Customer Engineer",
                "status": "rejected",
                "url": "https://jobs.ashbyhq.com/acme/role-new",
            },
        ],
    }))
    source = tmp_path / "export.json"
    source.write_text(json.dumps({
        "generated_at": now.isoformat(),
        "records": [{
            "company": "Acme",
            "title": "Customer Engineer",
            "status": "rejected",
            "url": "https://jobs.ashbyhq.com/acme/role-new",
        }],
    }))

    with pytest.raises(GmailCacheError, match="omits 1 existing"):
        sync_gmail_application_cache(
            source,
            destination,
            max_age_hours=24,
            now=now,
        )


def test_gmail_sync_serializes_newer_and_older_concurrent_exports(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 14, 12, tzinfo=UTC)
    destination = tmp_path / "gmail_applications.json"
    destination.write_text(json.dumps({
        "generated_at": (now - timedelta(hours=3)).isoformat(),
        "records": [{
            "company": "Acme",
            "title": "Implementation Engineer",
            "status": "applied",
            "url": "https://jobs.ashbyhq.com/acme/role-one",
        }],
    }))
    older = tmp_path / "older.json"
    older.write_text(json.dumps({
        "generated_at": (now - timedelta(hours=1)).isoformat(),
        "records": [{
            "company": "Acme",
            "title": "Implementation Engineer",
            "status": "applied",
            "url": "https://jobs.ashbyhq.com/acme/role-one",
        }],
    }))
    newer = tmp_path / "newer.json"
    newer.write_text(json.dumps({
        "generated_at": (now - timedelta(minutes=10)).isoformat(),
        "records": [
            {
                "company": "Acme",
                "title": "Implementation Engineer",
                "status": "applied",
                "url": "https://jobs.ashbyhq.com/acme/role-one",
            },
            {
                "company": "Beta",
                "title": "Support Engineer",
                "status": "rejected",
                "url": "https://jobs.ashbyhq.com/beta/role-two",
            },
        ],
    }))
    start = threading.Barrier(3)

    def sync(path: Path) -> str:
        start.wait(timeout=3)
        try:
            return sync_gmail_application_cache(
                path,
                destination,
                max_age_hours=24,
                now=now,
            ).generated_at
        except GmailCacheError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(sync, path) for path in (older, newer)]
        start.wait(timeout=3)
        results = [future.result(timeout=3) for future in futures]

    payload = json.loads(destination.read_text())
    assert payload["generated_at"] == (now - timedelta(minutes=10)).isoformat()
    assert len(payload["records"]) == 2
    assert (now - timedelta(minutes=10)).isoformat() in results


def test_external_evidence_health_helper_needs_no_tracker_or_ledger(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 14, 12, tzinfo=UTC)
    cache = tmp_path / "gmail.json"
    cache.write_text(json.dumps({
        "generated_at": now.isoformat(),
        "records": [{
            "company": "Acme",
            "title": "Engineer",
            "status": "applied",
        }],
    }))

    assert configured_external_evidence_errors(
        gmail_cache_path=cache,
        gmail_cache_max_age_hours=24,
        now=now,
    ) == ()
    errors = configured_external_evidence_errors(
        gmail_cache_path=tmp_path / "missing.json",
        gmail_cache_max_age_hours=24,
        now=now,
    )
    assert len(errors) == 1
    assert "jobpilot gmail-sync" in errors[0]


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
