"""Contracts for the append-only opportunity ledger."""

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest

from jobpilot.core.opportunity_ledger import OpportunityLedger


@pytest.fixture()
def ledger(tmp_path: Path) -> OpportunityLedger:
    store = OpportunityLedger(data_dir=tmp_path)
    yield store
    store.close()


@dataclass
class Observation:
    provider: str = "ashby"
    tenant: str = "acme"
    provider_job_id: str = "role-1"
    canonical_url: str = "https://jobs.example.test/acme/role-1"
    fetched_at: str = "2026-08-13T12:00:00+00:00"
    listing_state: str = "open"
    description: str = "Work directly with customers."


def test_core_writes_are_idempotent(ledger: OpportunityLedger):
    opportunity_id = ledger.upsert_opportunity(
        company="Acme", title="Solutions Engineer",
        canonical_url="https://jobs.example.test/acme/role-1",
        provider="ashby", provider_job_id="role-1",
    )
    assert opportunity_id == ledger.upsert_opportunity(
        company="Acme", title="Solutions Engineer",
        canonical_url="https://jobs.example.test/acme/role-1",
        provider="ashby", provider_job_id="role-1",
    )

    run_id = ledger.record_source_run(
        source="ashby", target="acme", status="success", result_count=1,
        fetched_at="2026-08-13T12:00:00+00:00", idempotency_key="run-1",
    )
    assert run_id == ledger.record_source_run(
        source="ashby", target="acme", status="success", result_count=1,
        fetched_at="2026-08-13T12:00:00+00:00", idempotency_key="run-1",
    )
    assert ledger.record_observation(opportunity_id, Observation(), run_id) == (
        ledger.record_observation(opportunity_id, Observation(), run_id)
    )
    result = {"grade": "A", "state": "recommend", "reasons": ["direct"]}
    assert ledger.record_assessment(
        opportunity_id, "legitimacy", result, assessed_at="2026-08-13T12:01:00+00:00"
    ) == ledger.record_assessment(
        opportunity_id, "legitimacy", result, assessed_at="2026-08-13T12:01:00+00:00"
    )

    assert ledger.table_count("opportunities") == 1
    assert ledger.table_count("source_runs") == 1
    assert ledger.table_count("observations") == 1
    assert ledger.table_count("assessments") == 1
    assert ledger.iter_observations(opportunity_id)[0]["provider_job_id"] == "role-1"


def test_url_only_record_converges_when_provider_identity_arrives(
    ledger: OpportunityLedger,
):
    url = "https://jobs.example.test/acme/role-1"
    url_only_id = ledger.upsert_opportunity(
        "Acme", "Solutions Engineer", canonical_url=url
    )
    ledger.append_event(url_only_id, "discovered", "2026-08-12", "aggregator")

    provider_id = ledger.upsert_opportunity(
        "Acme", "Solutions Engineer", canonical_url=url,
        provider="ashby", provider_job_id="role-1",
    )
    ledger.append_event(provider_id, "verified", "2026-08-13", "ashby")

    row = ledger.connection.execute(
        "SELECT provider, provider_job_id FROM opportunities WHERE id = ?",
        (provider_id,),
    ).fetchone()
    assert provider_id == url_only_id
    assert dict(row) == {"provider": "ashby", "provider_job_id": "role-1"}
    assert ledger.table_count("opportunities") == 1
    assert len(ledger.iter_events(opportunity_id=provider_id)) == 2


def test_provider_record_converges_with_later_url_only_evidence(
    ledger: OpportunityLedger,
):
    url = "https://jobs.example.test/acme/role-1"
    provider_id = ledger.upsert_opportunity(
        "Acme", "Solutions Engineer", canonical_url=url,
        provider="ashby", provider_job_id="role-1",
    )

    url_only_id = ledger.upsert_opportunity(
        "ACME INC", "Solutions Engineer", canonical_url=url
    )
    row = ledger.connection.execute(
        "SELECT provider, provider_job_id FROM opportunities WHERE id = ?",
        (url_only_id,),
    ).fetchone()

    assert url_only_id == provider_id
    assert dict(row) == {"provider": "ashby", "provider_job_id": "role-1"}
    assert ledger.table_count("opportunities") == 1


def test_same_provider_job_id_across_tenants_never_collides(
    ledger: OpportunityLedger,
):
    first = ledger.upsert_opportunity(
        "Acme", "Engineer",
        canonical_url="https://boards.greenhouse.io/acme/jobs/123",
        provider="greenhouse", provider_job_id="123",
        identity_key="greenhouse:acme:123",
    )
    second = ledger.upsert_opportunity(
        "Other", "Engineer",
        canonical_url="https://boards.greenhouse.io/other/jobs/123",
        provider="greenhouse", provider_job_id="123",
        identity_key="greenhouse:other:123",
    )

    assert first != second
    assert ledger.table_count("opportunities") == 2


def test_events_are_idempotent_append_only_and_keep_conflicts(ledger: OpportunityLedger):
    opportunity_id = ledger.upsert_opportunity("Acme", "Solutions Engineer")
    event_id = ledger.append_event(
        opportunity_id, "rejected", "2026-08-10", "tracker", {"row": 7}
    )
    assert event_id == ledger.append_event(
        opportunity_id, "rejected", "2026-08-10", "tracker", {"row": 7}
    )
    ledger.append_event(
        opportunity_id, "interview", "2026-08-11", "gmail", {"message_id": "m1"}
    )

    events = ledger.iter_events(opportunity_id=opportunity_id)
    assert [event.event_type for event in events] == ["rejected", "interview"]
    assert events[1].provenance == {"message_id": "m1"}
    assert ledger.conflicting_outcomes(opportunity_id) == {"interview", "rejected"}

    conn = ledger.connection
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE events SET event_type = 'offer' WHERE id = ?", (event_id,))
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM events WHERE id = ?", (event_id,))


def test_event_validation_and_filtered_iteration(ledger: OpportunityLedger):
    first = ledger.upsert_opportunity("Acme", "Role A")
    second = ledger.upsert_opportunity("Acme", "Role B")
    ledger.append_event(first, "applied", "2026-08-01", "manual")
    ledger.append_event(second, "offer", "2026-08-12", "gmail")

    assert [item.event_type for item in ledger.iter_events(event_type="offer")] == ["offer"]
    assert [item.event_type for item in ledger.iter_events(since="2026-08-10")] == ["offer"]
    with pytest.raises(ValueError, match="Unsupported event"):
        ledger.append_event(first, "maybe", "2026-08-13", "manual")
