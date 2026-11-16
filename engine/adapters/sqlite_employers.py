"""SQLite storage for employer profiles, HR contacts, and pre-submit requirements."""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from types import TracebackType

from jobpilot.engine.domain import (
    ChannelKind,
    EmployerRecord,
    FormSnapshot,
    GateKind,
    HrContact,
    RequirementCategory,
    RequirementObservation,
)

EMPLOYERS_FILENAME = "employers.db"

_MIGRATIONS: tuple[str, ...] = (
    """
    CREATE TABLE employers (
        employer_key TEXT PRIMARY KEY,
        display_name TEXT NOT NULL,
        providers_json TEXT NOT NULL DEFAULT '[]',
        board_tokens_json TEXT NOT NULL DEFAULT '[]',
        domains_json TEXT NOT NULL DEFAULT '[]',
        first_seen TEXT NOT NULL DEFAULT '',
        last_seen TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE hr_contacts (
        contact_id TEXT PRIMARY KEY,
        employer_key TEXT NOT NULL REFERENCES employers(employer_key),
        email TEXT NOT NULL,
        display_name TEXT NOT NULL DEFAULT '',
        domain TEXT NOT NULL DEFAULT '',
        channel_kind TEXT NOT NULL,
        first_seen TEXT NOT NULL DEFAULT '',
        last_seen TEXT NOT NULL DEFAULT '',
        message_count INTEGER NOT NULL DEFAULT 1,
        sample_subject TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX hr_contacts_by_employer ON hr_contacts (employer_key);
    CREATE TABLE hr_contact_messages (
        message_key TEXT PRIMARY KEY,
        contact_id TEXT NOT NULL REFERENCES hr_contacts(contact_id)
    );
    CREATE TABLE form_snapshots (
        role_key TEXT PRIMARY KEY,
        employer_key TEXT NOT NULL REFERENCES employers(employer_key),
        provider TEXT NOT NULL DEFAULT '',
        role_title TEXT NOT NULL DEFAULT '',
        role_url TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL,
        error TEXT NOT NULL DEFAULT '',
        field_count INTEGER NOT NULL DEFAULT 0,
        beyond_standard_count INTEGER NOT NULL DEFAULT 0,
        gates_json TEXT NOT NULL DEFAULT '[]',
        fetched_at TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX form_snapshots_by_employer ON form_snapshots (employer_key);
    CREATE TABLE requirement_observations (
        observation_id TEXT PRIMARY KEY,
        employer_key TEXT NOT NULL REFERENCES employers(employer_key),
        employer_name TEXT NOT NULL,
        role_key TEXT NOT NULL,
        role_title TEXT NOT NULL DEFAULT '',
        role_url TEXT NOT NULL DEFAULT '',
        provider TEXT NOT NULL DEFAULT '',
        category TEXT NOT NULL,
        label TEXT NOT NULL DEFAULT '',
        field_type TEXT NOT NULL DEFAULT '',
        requiredness TEXT NOT NULL DEFAULT 'unknown',
        gate_kind TEXT NOT NULL DEFAULT '',
        is_beyond_standard INTEGER NOT NULL DEFAULT 0,
        source_kind TEXT NOT NULL DEFAULT '',
        source_ref TEXT NOT NULL DEFAULT '',
        observed_at TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX observations_by_employer ON requirement_observations (employer_key);
    CREATE INDEX observations_by_role ON requirement_observations (role_key, source_kind);
    """,
)
SCHEMA_VERSION = len(_MIGRATIONS)


def _create_private(path: Path) -> None:
    """Create the database file readable only by its owner, refusing symlinks."""
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)


def _merge_sorted(a: Sequence[str], b: Sequence[str]) -> str:
    return json.dumps(sorted({x for x in (*a, *b) if x}), ensure_ascii=False)


def _min_nonempty(a: str, b: str) -> str:
    if not a:
        return b
    if not b:
        return a
    return min(a, b)


class SQLiteEmployerStore:
    """SQLite persistence for employer profiles, HR contacts, and form schemas."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _create_private(self.path)
        self._db = sqlite3.connect(self.path)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._migrate()

    @classmethod
    def in_data_dir(cls, data_dir: Path) -> SQLiteEmployerStore:
        """Open the employer store inside a JobPilot data folder."""
        return cls(Path(data_dir) / EMPLOYERS_FILENAME)

    def _migrate(self) -> None:
        version = self._db.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError(
                f"{self.path} uses schema v{version}; this JobPilot understands up to v{SCHEMA_VERSION}"
            )
        for number, script in enumerate(_MIGRATIONS[version:], start=version + 1):
            try:
                self._db.executescript(
                    f"BEGIN;\n{script}\nPRAGMA user_version = {number};\nCOMMIT;"
                )
            except sqlite3.Error:
                self._db.rollback()
                raise

    def upsert_employer(self, employer: EmployerRecord) -> EmployerRecord:
        with self._db:
            existing = self._db.execute(
                "SELECT * FROM employers WHERE employer_key = ?",
                (employer.employer_key,),
            ).fetchone()
            if existing is None:
                merged = employer
                self._db.execute(
                    """
                    INSERT INTO employers (
                        employer_key, display_name, providers_json, board_tokens_json,
                        domains_json, first_seen, last_seen
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        merged.employer_key,
                        merged.display_name,
                        json.dumps(list(merged.providers), ensure_ascii=False),
                        json.dumps(list(merged.board_tokens), ensure_ascii=False),
                        json.dumps(list(merged.domains), ensure_ascii=False),
                        merged.first_seen,
                        merged.last_seen,
                    ),
                )
                return merged

            old = _row_to_employer(existing)
            display_name = (
                employer.display_name
                if old.display_name == old.employer_key and employer.display_name
                else old.display_name
            )

            first_seen = _min_nonempty(old.first_seen, employer.first_seen)
            last_seen = max(old.last_seen, employer.last_seen)
            providers_json = _merge_sorted(old.providers, employer.providers)
            tokens_json = _merge_sorted(old.board_tokens, employer.board_tokens)
            domains_json = _merge_sorted(old.domains, employer.domains)
            self._db.execute(
                """
                UPDATE employers SET
                    display_name = ?,
                    providers_json = ?,
                    board_tokens_json = ?,
                    domains_json = ?,
                    first_seen = ?,
                    last_seen = ?
                WHERE employer_key = ?
                """,
                (
                    display_name,
                    providers_json,
                    tokens_json,
                    domains_json,
                    first_seen,
                    last_seen,
                    employer.employer_key,
                ),
            )
        updated = self.get_employer(employer.employer_key)
        assert updated is not None
        return updated

    def upsert_contact(self, contact: HrContact, *, message_key: str = "") -> bool:
        with self._db:
            existed = (
                self._db.execute(
                    "SELECT 1 FROM hr_contacts WHERE contact_id = ?",
                    (contact.contact_id,),
                ).fetchone()
                is not None
            )
            self._db.execute(
                """
                INSERT INTO hr_contacts (
                    contact_id, employer_key, email, display_name, domain,
                    channel_kind, first_seen, last_seen, message_count, sample_subject
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (contact_id) DO UPDATE SET
                    display_name = COALESCE(NULLIF(hr_contacts.display_name, ''), excluded.display_name),
                    domain = COALESCE(NULLIF(hr_contacts.domain, ''), excluded.domain),
                    channel_kind = excluded.channel_kind,
                    first_seen = CASE
                        WHEN hr_contacts.first_seen = '' THEN excluded.first_seen
                        WHEN excluded.first_seen = '' THEN hr_contacts.first_seen
                        ELSE MIN(hr_contacts.first_seen, excluded.first_seen)
                    END,
                    last_seen = MAX(hr_contacts.last_seen, excluded.last_seen),
                    sample_subject = COALESCE(NULLIF(hr_contacts.sample_subject, ''), excluded.sample_subject)
                """,
                (
                    contact.contact_id,
                    contact.employer_key,
                    contact.email,
                    contact.display_name,
                    contact.domain,
                    contact.channel_kind.value,
                    contact.first_seen,
                    contact.last_seen,
                    max(1, contact.message_count),
                    contact.sample_subject,
                ),
            )
            if not message_key:
                return not existed
            cursor = self._db.execute(
                """
                INSERT INTO hr_contact_messages (message_key, contact_id)
                VALUES (?, ?)
                ON CONFLICT (message_key) DO NOTHING
                """,
                (message_key, contact.contact_id),
            )
            new_msg = cursor.rowcount == 1
            if existed and new_msg:
                self._db.execute(
                    "UPDATE hr_contacts SET message_count = message_count + 1 WHERE contact_id = ?",
                    (contact.contact_id,),
                )
            return (not existed) or new_msg

    def record_observation(self, observation: RequirementObservation) -> bool:
        with self._db:
            return self._upsert_observation(observation)

    def _upsert_observation(self, observation: RequirementObservation) -> bool:
        existed = (
            self._db.execute(
                "SELECT 1 FROM requirement_observations WHERE observation_id = ?",
                (observation.observation_id,),
            ).fetchone()
            is not None
        )
        self._db.execute(
            """
            INSERT INTO requirement_observations (
                observation_id, employer_key, employer_name, role_key, role_title,
                role_url, provider, category, label, field_type, requiredness,
                gate_kind, is_beyond_standard, source_kind, source_ref, observed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (observation_id) DO UPDATE SET
                requiredness = excluded.requiredness,
                field_type = COALESCE(NULLIF(excluded.field_type, ''), requirement_observations.field_type),
                is_beyond_standard = MAX(requirement_observations.is_beyond_standard, excluded.is_beyond_standard),
                observed_at = MAX(requirement_observations.observed_at, excluded.observed_at)
            """,
            (
                observation.observation_id,
                observation.employer_key,
                observation.employer_name,
                observation.role_key,
                observation.role_title,
                observation.role_url,
                observation.provider,
                observation.category.value,
                observation.label,
                observation.field_type,
                observation.requiredness,
                observation.gate_kind.value if observation.gate_kind else "",
                1 if observation.is_beyond_standard else 0,
                observation.source_kind,
                observation.source_ref,
                observation.observed_at,
            ),
        )
        return not existed

    def replace_observations_for_role(
        self,
        role_key: str,
        source_kind: str,
        observations: Sequence[RequirementObservation],
    ) -> int:
        added = 0
        with self._db:
            self._db.execute(
                "DELETE FROM requirement_observations WHERE role_key = ? AND source_kind = ?",
                (role_key, source_kind),
            )
            for obs in observations:
                if self._upsert_observation(obs):
                    added += 1
        return added

    def record_snapshot(self, snapshot: FormSnapshot) -> None:
        with self._db:
            self._db.execute(
                """
                INSERT INTO form_snapshots (
                    role_key, employer_key, provider, role_title, role_url,
                    status, error, field_count, beyond_standard_count, gates_json, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (role_key) DO UPDATE SET
                    employer_key = excluded.employer_key,
                    provider = COALESCE(NULLIF(excluded.provider, ''), form_snapshots.provider),
                    role_title = COALESCE(NULLIF(excluded.role_title, ''), form_snapshots.role_title),
                    role_url = COALESCE(NULLIF(excluded.role_url, ''), form_snapshots.role_url),
                    status = excluded.status,
                    error = excluded.error,
                    field_count = excluded.field_count,
                    beyond_standard_count = excluded.beyond_standard_count,
                    gates_json = excluded.gates_json,
                    fetched_at = excluded.fetched_at
                WHERE NOT (form_snapshots.status = 'ok' AND excluded.status IN ('error', 'unsupported'))
                """,
                (
                    snapshot.role_key,
                    snapshot.employer_key,
                    snapshot.provider,
                    snapshot.role_title,
                    snapshot.role_url,
                    snapshot.status,
                    snapshot.error,
                    snapshot.field_count,
                    snapshot.beyond_standard_count,
                    json.dumps([g.value for g in snapshot.gates]),
                    snapshot.fetched_at,
                ),
            )

    def has_snapshot(
        self,
        role_key: str,
        *,
        statuses: tuple[str, ...] = ("ok", "inferred_gate", "unsupported"),
    ) -> bool:
        placeholders = ",".join("?" for _ in statuses)
        row = self._db.execute(
            f"SELECT 1 FROM form_snapshots WHERE role_key = ? AND status IN ({placeholders})",
            (role_key, *statuses),
        ).fetchone()
        return row is not None

    def employers(self) -> list[EmployerRecord]:
        rows = self._db.execute(
            "SELECT * FROM employers ORDER BY employer_key"
        ).fetchall()
        return [_row_to_employer(r) for r in rows]

    def get_employer(self, employer_key: str) -> EmployerRecord | None:
        row = self._db.execute(
            "SELECT * FROM employers WHERE employer_key = ?",
            (employer_key,),
        ).fetchone()
        return _row_to_employer(row) if row else None

    def contacts(self, employer_key: str | None = None) -> list[HrContact]:
        if employer_key is None:
            rows = self._db.execute(
                "SELECT * FROM hr_contacts ORDER BY message_count DESC, email"
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT * FROM hr_contacts WHERE employer_key = ? ORDER BY message_count DESC, email",
                (employer_key,),
            ).fetchall()
        return [_row_to_contact(r) for r in rows]

    def observations(
        self, employer_key: str | None = None
    ) -> list[RequirementObservation]:
        if employer_key is None:
            rows = self._db.execute(
                "SELECT * FROM requirement_observations ORDER BY employer_key, role_key, observation_id"
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT * FROM requirement_observations WHERE employer_key = ? ORDER BY role_key, observation_id",
                (employer_key,),
            ).fetchall()
        return [_row_to_observation(r) for r in rows]

    def snapshots(self, employer_key: str | None = None) -> list[FormSnapshot]:
        if employer_key is None:
            rows = self._db.execute(
                "SELECT * FROM form_snapshots ORDER BY employer_key, role_key"
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT * FROM form_snapshots WHERE employer_key = ? ORDER BY role_key",
                (employer_key,),
            ).fetchall()
        return [_row_to_snapshot(r) for r in rows]

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> SQLiteEmployerStore:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def _row_to_employer(row: sqlite3.Row) -> EmployerRecord:
    return EmployerRecord(
        employer_key=row["employer_key"],
        display_name=row["display_name"],
        providers=tuple(json.loads(row["providers_json"])),
        board_tokens=tuple(json.loads(row["board_tokens_json"])),
        domains=tuple(json.loads(row["domains_json"])),
        first_seen=row["first_seen"],
        last_seen=row["last_seen"],
    )


def _row_to_contact(row: sqlite3.Row) -> HrContact:
    return HrContact(
        contact_id=row["contact_id"],
        employer_key=row["employer_key"],
        email=row["email"],
        display_name=row["display_name"],
        domain=row["domain"],
        channel_kind=ChannelKind(row["channel_kind"]),
        first_seen=row["first_seen"],
        last_seen=row["last_seen"],
        message_count=row["message_count"],
        sample_subject=row["sample_subject"],
    )


def _row_to_observation(row: sqlite3.Row) -> RequirementObservation:
    gate_val = row["gate_kind"]
    return RequirementObservation(
        observation_id=row["observation_id"],
        employer_key=row["employer_key"],
        employer_name=row["employer_name"],
        role_key=row["role_key"],
        role_title=row["role_title"],
        role_url=row["role_url"],
        provider=row["provider"],
        category=RequirementCategory(row["category"]),
        label=row["label"],
        field_type=row["field_type"],
        requiredness=row["requiredness"],
        gate_kind=GateKind(gate_val) if gate_val else None,
        is_beyond_standard=bool(row["is_beyond_standard"]),
        source_kind=row["source_kind"],
        source_ref=row["source_ref"],
        observed_at=row["observed_at"],
    )


def _row_to_snapshot(row: sqlite3.Row) -> FormSnapshot:
    return FormSnapshot(
        role_key=row["role_key"],
        employer_key=row["employer_key"],
        provider=row["provider"],
        role_title=row["role_title"],
        role_url=row["role_url"],
        status=row["status"],
        error=row["error"],
        field_count=row["field_count"],
        beyond_standard_count=row["beyond_standard_count"],
        gates=tuple(GateKind(g) for g in json.loads(row["gates_json"])),
        fetched_at=row["fetched_at"],
    )
