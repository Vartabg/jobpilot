import sqlite3
import stat

import pytest

from jobpilot.engine.adapters.sqlite_ledger import SCHEMA_VERSION, SQLiteLedger
from jobpilot.engine.domain import Event, EventKind, Lane, Listing, Status

URL = "https://jobs.ashbyhq.com/acme/uuid-9"
T1, T2, T3 = (
    "2026-09-20T12:00:00+00:00",
    "2026-09-24T12:00:00+00:00",
    "2026-09-25T12:00:00+00:00",
)


@pytest.fixture
def ledger(tmp_path):
    with SQLiteLedger.in_data_dir(tmp_path) as opened:
        yield opened


def test_file_is_private_and_versioned(tmp_path):
    with SQLiteLedger.in_data_dir(tmp_path) as ledger:
        mode = stat.S_IMODE(ledger.path.stat().st_mode)
        version = ledger._db.execute("PRAGMA user_version").fetchone()[0]
    assert mode == 0o600
    assert version == SCHEMA_VERSION


def test_upsert_is_idempotent_and_keeps_known_fields(ledger):
    first = ledger.upsert(
        Listing("Acme", "Engineer", url=URL, location="Remote (US)"), seen_at=T2
    )
    again = ledger.upsert(
        Listing("Acme", "Engineer II", url=URL + "?src=li"), seen_at=T3
    )
    earlier = ledger.upsert(Listing("Acme", "Engineer II", url=URL), seen_at=T1)

    assert first.id == again.id == earlier.id
    assert len(ledger.opportunities()) == 1
    assert earlier.title == "Engineer II"  # newer non-empty values win
    assert earlier.location == "Remote (US)"  # an empty value never erases a known one
    assert (earlier.first_seen, earlier.last_seen) == (T1, T3)
    assert (earlier.provider, earlier.tenant, earlier.provider_job_id) == (
        "ashby",
        "acme",
        "uuid-9",
    )


def test_events_are_idempotent_ordered_and_drive_status(ledger):
    opportunity = ledger.upsert(Listing("Acme", "Engineer", url=URL), seen_at=T1)
    applied = Event(opportunity.id, EventKind.APPLIED, T2, "user")
    discovered = Event(
        opportunity.id, EventKind.DISCOVERED, T1, "scan", data={"score": 61}
    )

    assert ledger.append(applied) is True
    assert ledger.append(applied) is False  # same fact, recorded once
    assert ledger.append(discovered) is True

    events = ledger.events(opportunity.id)
    assert [event.kind for event in events] == [EventKind.DISCOVERED, EventKind.APPLIED]
    assert events[0].data == {"score": 61}
    assert events[1].id == applied.id
    assert ledger.status(opportunity.id) is Status.APPLIED


def test_events_cannot_be_edited_or_deleted(ledger):
    opportunity = ledger.upsert(Listing("Acme", "Engineer", url=URL), seen_at=T1)
    ledger.append(Event(opportunity.id, EventKind.APPLIED, T2, "user"))
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        ledger._db.execute("UPDATE events SET kind = 'offer'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        ledger._db.execute("DELETE FROM events")


def test_events_need_a_known_opportunity(ledger):
    with pytest.raises(KeyError):
        ledger.append(Event("opp_missing", EventKind.APPLIED, T2, "user"))


def test_history_survives_reopening(tmp_path):
    with SQLiteLedger.in_data_dir(tmp_path) as ledger:
        opportunity = ledger.upsert(Listing("Acme", "Engineer", url=URL), seen_at=T1)
        ledger.append(Event(opportunity.id, EventKind.INTERVIEW, T2, "user"))
    with SQLiteLedger.in_data_dir(tmp_path) as reopened:
        assert reopened.status(opportunity.id) is Status.INTERVIEWING


def test_lanes_filter(ledger):
    ledger.upsert(Listing("Acme", "Engineer", url=URL), seen_at=T1)
    ledger.upsert(Listing("Beta", "Contract build", lane=Lane.GIG), seen_at=T2)
    assert [o.company for o in ledger.opportunities(Lane.GIG)] == ["Beta"]
    assert [o.company for o in ledger.opportunities()] == [
        "Beta",
        "Acme",
    ]  # most recent first


def test_a_newer_schema_is_refused(tmp_path):
    path = tmp_path / "opportunities.db"
    with sqlite3.connect(path) as db:
        db.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    with pytest.raises(RuntimeError, match="schema"):
        SQLiteLedger(path)


# ----- schema v2: postings and assessments ----------------------------------------


def test_a_v1_ledger_upgrades_in_place_keeping_history(tmp_path, monkeypatch):
    import jobpilot.engine.adapters.sqlite_ledger as module

    path = tmp_path / "opportunities.db"
    monkeypatch.setattr(module, "_MIGRATIONS", module._MIGRATIONS[:1])
    monkeypatch.setattr(module, "SCHEMA_VERSION", 1)
    with SQLiteLedger(path) as old:
        opportunity = old.upsert(Listing("Acme", "Engineer", url=URL), seen_at=T1)
        old.append(Event(opportunity.id, EventKind.APPLIED, T2, "user"))
    monkeypatch.undo()

    with SQLiteLedger(path) as upgraded:
        assert (
            upgraded._db.execute("PRAGMA user_version").fetchone()[0]
            == SCHEMA_VERSION
            == 2
        )
        assert upgraded.status(opportunity.id) is Status.APPLIED


def test_postings_keep_the_latest_text_and_report_changes(ledger):
    from jobpilot.engine.domain import Posting, Workplace

    opportunity = ledger.upsert(Listing("Acme", "Engineer", url=URL), seen_at=T1)
    first = Posting(
        Listing("Acme", "Engineer", url=URL),
        "v1 text",
        Workplace.REMOTE,
        ("United States",),
    )
    assert ledger.save_posting(opportunity.id, first, fetched_at=T1) is True
    assert (
        ledger.save_posting(opportunity.id, first, fetched_at=T2) is False
    )  # unchanged
    second = Posting(first.listing, "v2 text", Workplace.REMOTE, ("United States",))
    assert ledger.save_posting(opportunity.id, second, fetched_at=T3) is True
    stored = ledger.posting(opportunity.id)
    assert stored.description == "v2 text"
    assert stored.workplace is Workplace.REMOTE
    assert stored.locations == ("United States",)


def test_assessments_are_idempotent_append_only_and_latest_wins(ledger):
    from jobpilot.engine.domain import Assessment

    opportunity = ledger.upsert(Listing("Acme", "Engineer", url=URL), seen_at=T1)
    first = Assessment(
        opportunity.id, "screen", "screen/1", T1, {"passed": True}, basis="hash-a"
    )
    assert ledger.record_assessment(first) is True
    assert ledger.record_assessment(first) is False
    second = Assessment(
        opportunity.id, "screen", "screen/1", T2, {"passed": False}, basis="hash-b"
    )
    ledger.record_assessment(second)
    assert ledger.latest_assessment(opportunity.id, "screen").result == {
        "passed": False
    }
    assert ledger.latest_assessment(opportunity.id, "fit") is None
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        ledger._db.execute("DELETE FROM assessments")
