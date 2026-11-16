import os
import sqlite3

import pytest

from jobpilot.engine.adapters import sqlite_employers
from jobpilot.engine.adapters.sqlite_employers import (
    EMPLOYERS_FILENAME,
    SCHEMA_VERSION,
    SQLiteEmployerStore,
)
from jobpilot.engine.domain import (
    ChannelKind,
    EmployerRecord,
    FormSnapshot,
    GateKind,
    HrContact,
    RequirementCategory,
    RequirementObservation,
)


def test_store_permissions_and_in_data_dir(tmp_path):
    store = SQLiteEmployerStore.in_data_dir(tmp_path)
    assert store.path == tmp_path / EMPLOYERS_FILENAME
    store.close()
    if os.name == "posix":
        assert (store.path.stat().st_mode & 0o777) == 0o600


def test_store_refuses_newer_schema_version(tmp_path):
    db_path = tmp_path / EMPLOYERS_FILENAME
    with sqlite3.connect(db_path) as db:
        db.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    with pytest.raises(RuntimeError):
        SQLiteEmployerStore(db_path)

    with SQLiteEmployerStore(tmp_path / "fresh.db") as store:
        version = store._db.execute("PRAGMA user_version").fetchone()[0]
        assert version == sqlite_employers.SCHEMA_VERSION


def test_employer_upsert_preserves_nonempty_first_seen_and_merges_sets(tmp_path):
    with SQLiteEmployerStore.in_data_dir(tmp_path) as store:
        store.upsert_employer(
            EmployerRecord(
                employer_key="acme",
                display_name="Acme",
                providers=("greenhouse",),
                board_tokens=("greenhouse:acme",),
                first_seen="2026-11-03T10:00:00+00:00",
                last_seen="2026-11-03T10:00:00+00:00",
            )
        )
        # Undated re-import must NOT overwrite valid first_seen with ""
        merged = store.upsert_employer(
            EmployerRecord(
                employer_key="acme",
                display_name="Acme Corp",
                providers=("ashby",),
                board_tokens=("ashby:acme-co",),
                domains=("acme.example.com",),
                first_seen="",
                last_seen="2026-11-11T12:00:00+00:00",
            )
        )
        assert merged.first_seen == "2026-11-03T10:00:00+00:00"
        assert merged.last_seen == "2026-11-11T12:00:00+00:00"
        assert merged.providers == ("ashby", "greenhouse")
        assert merged.board_tokens == ("ashby:acme-co", "greenhouse:acme")
        assert merged.domains == ("acme.example.com",)


def test_contact_message_deduplication_is_idempotent(tmp_path):
    with SQLiteEmployerStore.in_data_dir(tmp_path) as store:
        store.upsert_employer(EmployerRecord("acme", "Acme"))
        contact = HrContact(
            employer_key="acme",
            email="recruiter@acme.example.com",
            display_name="Alex Recruiter",
            channel_kind=ChannelKind.DIRECT_PERSON,
            first_seen="2026-11-05T10:00:00+00:00",
            last_seen="2026-11-05T10:00:00+00:00",
            sample_subject="Interview invite",
        )
        assert store.upsert_contact(contact, message_key="gmail:m1") is True
        assert store.contacts("acme")[0].message_count == 1
        # Repeating same message_key does not double-count
        assert store.upsert_contact(contact, message_key="gmail:m1") is False
        assert store.contacts("acme")[0].message_count == 1
        # New message_key increments cleanly and preserves non-empty first_seen
        later = HrContact(
            employer_key="acme",
            email="recruiter@acme.example.com",
            channel_kind=ChannelKind.DIRECT_PERSON,
            first_seen="",
            last_seen="2026-11-10T10:00:00+00:00",
        )
        assert store.upsert_contact(later, message_key="gmail:m2") is True
        got = store.contacts("acme")[0]
        assert got.message_count == 2
        assert got.first_seen == "2026-11-05T10:00:00+00:00"
        assert got.last_seen == "2026-11-10T10:00:00+00:00"


def test_snapshot_protects_ok_from_transient_errors_and_replaces_observations(tmp_path):
    with SQLiteEmployerStore.in_data_dir(tmp_path) as store:
        store.upsert_employer(EmployerRecord("acme", "Acme"))
        ok_snap = FormSnapshot(
            role_key="greenhouse:acme:101",
            employer_key="acme",
            provider="greenhouse",
            role_title="Engineer",
            role_url="https://boards.greenhouse.io/acme/jobs/101",
            status="ok",
            field_count=4,
            beyond_standard_count=1,
            gates=(GateKind.NONE,),
            fetched_at="2026-11-15T10:00:00+00:00",
        )
        store.record_snapshot(ok_snap)
        # Transient error must NOT clobber ok snapshot
        store.record_snapshot(
            FormSnapshot(
                role_key="greenhouse:acme:101",
                employer_key="acme",
                provider="greenhouse",
                role_title="Engineer",
                role_url="https://boards.greenhouse.io/acme/jobs/101",
                status="error",
                error="HTTP 429 (rate limited)",
                fetched_at="2026-11-16T10:00:00+00:00",
            )
        )
        snaps = store.snapshots("acme")
        assert len(snaps) == 1
        assert snaps[0].status == "ok" and snaps[0].field_count == 4

        obs1 = RequirementObservation(
            employer_key="acme",
            employer_name="Acme",
            role_key="greenhouse:acme:101",
            category=RequirementCategory.COMPENSATION,
            label="Salary expectation",
            requiredness="required",
            is_beyond_standard=True,
            source_kind="live_ats_form",
        )
        obs2 = RequirementObservation(
            employer_key="acme",
            employer_name="Acme",
            role_key="greenhouse:acme:101",
            category=RequirementCategory.AVAILABILITY,
            label="Earliest start date",
            requiredness="required",
            is_beyond_standard=True,
            source_kind="live_ats_form",
        )
        assert (
            store.replace_observations_for_role(
                "greenhouse:acme:101", "live_ats_form", [obs1]
            )
            == 1
        )
        assert len(store.observations("acme")) == 1
        assert (
            store.replace_observations_for_role(
                "greenhouse:acme:101", "live_ats_form", [obs2]
            )
            == 1
        )
        remaining = store.observations("acme")
        assert len(remaining) == 1 and remaining[0].label == "Earliest start date"
