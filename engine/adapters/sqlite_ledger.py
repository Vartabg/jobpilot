"""SQLite implementation of the Ledger port.

One private file holds every opportunity and its history. Events are append-only,
and triggers enforce that inside the database itself. Schema changes ship as
numbered migrations tracked with ``PRAGMA user_version``.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from types import TracebackType

from jobpilot.engine.domain import (
    Assessment,
    Event,
    Lane,
    Listing,
    Opportunity,
    Posting,
    Status,
    Workplace,
    identify_role,
    normalize_timestamp,
    opportunity_id,
    status_of,
)

LEDGER_FILENAME = "opportunities.db"

# Each entry upgrades the schema by one version. Never edit a released entry;
# append a new one instead.
_MIGRATIONS: tuple[str, ...] = (
    """
    CREATE TABLE opportunities (
        id TEXT PRIMARY KEY,
        identity_key TEXT NOT NULL UNIQUE,
        lane TEXT NOT NULL,
        company TEXT NOT NULL,
        title TEXT NOT NULL,
        url TEXT NOT NULL DEFAULT '',
        location TEXT NOT NULL DEFAULT '',
        provider TEXT NOT NULL DEFAULT '',
        tenant TEXT NOT NULL DEFAULT '',
        provider_job_id TEXT NOT NULL DEFAULT '',
        first_seen TEXT NOT NULL,
        last_seen TEXT NOT NULL
    );
    CREATE TABLE events (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        id TEXT NOT NULL UNIQUE,
        opportunity_id TEXT NOT NULL REFERENCES opportunities(id),
        kind TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        source TEXT NOT NULL,
        data_json TEXT NOT NULL DEFAULT '{}'
    );
    CREATE INDEX events_by_opportunity ON events (opportunity_id, occurred_at, seq);
    CREATE TRIGGER events_never_update BEFORE UPDATE ON events
    BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
    CREATE TRIGGER events_never_delete BEFORE DELETE ON events
    BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
    """,
    # v2: the latest full text of each role, and append-only assessments.
    """
    CREATE TABLE postings (
        opportunity_id TEXT PRIMARY KEY REFERENCES opportunities(id),
        fetched_at TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        description TEXT NOT NULL,
        workplace TEXT NOT NULL,
        locations_json TEXT NOT NULL,
        compensation TEXT NOT NULL DEFAULT '',
        employment_type TEXT NOT NULL DEFAULT '',
        posted_at TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE assessments (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        id TEXT NOT NULL UNIQUE,
        opportunity_id TEXT NOT NULL REFERENCES opportunities(id),
        kind TEXT NOT NULL,
        version TEXT NOT NULL,
        basis TEXT NOT NULL,
        assessed_at TEXT NOT NULL,
        result_json TEXT NOT NULL
    );
    CREATE INDEX assessments_by_opportunity ON assessments (opportunity_id, kind, seq);
    CREATE TRIGGER assessments_never_update BEFORE UPDATE ON assessments
    BEGIN SELECT RAISE(ABORT, 'assessments are append-only'); END;
    CREATE TRIGGER assessments_never_delete BEFORE DELETE ON assessments
    BEGIN SELECT RAISE(ABORT, 'assessments are append-only'); END;
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


class SQLiteLedger:
    """The opportunity ledger, stored in one SQLite file."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _create_private(self.path)
        self._db = sqlite3.connect(self.path)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._migrate()

    @classmethod
    def in_data_dir(cls, data_dir: Path) -> SQLiteLedger:
        """Open the ledger that lives in a JobPilot data folder."""
        return cls(Path(data_dir) / LEDGER_FILENAME)

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

    def upsert(self, listing: Listing, *, seen_at: str) -> Opportunity:
        seen_at = normalize_timestamp(seen_at)
        key = listing.identity_key()
        identity = identify_role(
            listing.url, listing.provider, listing.tenant, listing.provider_job_id
        )
        with self._db:
            self._db.execute(
                """
                INSERT INTO opportunities (
                    id, identity_key, lane, company, title, url, location,
                    provider, tenant, provider_job_id, first_seen, last_seen
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (identity_key) DO UPDATE SET
                    company = COALESCE(NULLIF(excluded.company, ''), company),
                    title = COALESCE(NULLIF(excluded.title, ''), title),
                    url = COALESCE(NULLIF(excluded.url, ''), url),
                    location = COALESCE(NULLIF(excluded.location, ''), location),
                    provider = COALESCE(NULLIF(provider, ''), excluded.provider),
                    tenant = COALESCE(NULLIF(tenant, ''), excluded.tenant),
                    provider_job_id = COALESCE(NULLIF(provider_job_id, ''), excluded.provider_job_id),
                    first_seen = MIN(first_seen, excluded.first_seen),
                    last_seen = MAX(last_seen, excluded.last_seen)
                """,
                (
                    opportunity_id(key),
                    key,
                    listing.lane.value,
                    listing.company.strip(),
                    listing.title.strip(),
                    identity.canonical_url,
                    listing.location.strip(),
                    identity.provider,
                    identity.tenant,
                    identity.provider_job_id,
                    seen_at,
                    seen_at,
                ),
            )
        opportunity = self.get(opportunity_id(key))
        assert opportunity is not None  # just written in the same connection
        return opportunity

    def append(self, event: Event) -> bool:
        if self.get(event.opportunity_id) is None:
            raise KeyError(f"unknown opportunity {event.opportunity_id}")
        with self._db:
            cursor = self._db.execute(
                """
                INSERT INTO events (id, opportunity_id, kind, occurred_at, source, data_json)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (id) DO NOTHING
                """,
                (
                    event.id,
                    event.opportunity_id,
                    event.kind.value,
                    event.occurred_at,
                    event.source,
                    json.dumps(dict(event.data), sort_keys=True, separators=(",", ":")),
                ),
            )
        return cursor.rowcount == 1

    def get(self, opportunity_id: str) -> Opportunity | None:
        row = self._db.execute(
            "SELECT * FROM opportunities WHERE id = ?", (opportunity_id,)
        ).fetchone()
        return _opportunity(row) if row else None

    def events(self, opportunity_id: str) -> list[Event]:
        rows = self._db.execute(
            "SELECT * FROM events WHERE opportunity_id = ? ORDER BY occurred_at, seq",
            (opportunity_id,),
        ).fetchall()
        return [
            Event(
                opportunity_id=row["opportunity_id"],
                kind=row["kind"],
                occurred_at=row["occurred_at"],
                source=row["source"],
                data=json.loads(row["data_json"]),
                id=row["id"],
            )
            for row in rows
        ]

    def status(self, opportunity_id: str) -> Status:
        return status_of(self.events(opportunity_id))

    def opportunities(self, lane: Lane | None = None) -> list[Opportunity]:
        if lane is None:
            rows = self._db.execute(
                "SELECT * FROM opportunities ORDER BY last_seen DESC, id"
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT * FROM opportunities WHERE lane = ? ORDER BY last_seen DESC, id",
                (lane.value,),
            ).fetchall()
        return [_opportunity(row) for row in rows]

    def save_posting(
        self, opportunity_id: str, posting: Posting, *, fetched_at: str
    ) -> bool:
        fetched_at = normalize_timestamp(fetched_at)
        previous = self._db.execute(
            "SELECT content_hash FROM postings WHERE opportunity_id = ?",
            (opportunity_id,),
        ).fetchone()
        with self._db:
            self._db.execute(
                """
                INSERT INTO postings (
                    opportunity_id, fetched_at, content_hash, description, workplace,
                    locations_json, compensation, employment_type, posted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (opportunity_id) DO UPDATE SET
                    fetched_at = excluded.fetched_at,
                    content_hash = excluded.content_hash,
                    description = excluded.description,
                    workplace = excluded.workplace,
                    locations_json = excluded.locations_json,
                    compensation = excluded.compensation,
                    employment_type = excluded.employment_type,
                    posted_at = excluded.posted_at
                """,
                (
                    opportunity_id,
                    fetched_at,
                    posting.content_hash,
                    posting.description,
                    posting.workplace.value,
                    json.dumps(list(posting.all_locations), ensure_ascii=False),
                    posting.compensation,
                    posting.employment_type,
                    posting.posted_at,
                ),
            )
        return previous is None or previous["content_hash"] != posting.content_hash

    def posting(self, opportunity_id: str) -> Posting | None:
        opportunity = self.get(opportunity_id)
        row = self._db.execute(
            "SELECT * FROM postings WHERE opportunity_id = ?", (opportunity_id,)
        ).fetchone()
        if opportunity is None or row is None:
            return None
        listing = Listing(
            company=opportunity.company,
            title=opportunity.title,
            url=opportunity.url,
            location=opportunity.location,
            lane=opportunity.lane,
            provider=opportunity.provider,
            tenant=opportunity.tenant,
            provider_job_id=opportunity.provider_job_id,
        )
        return Posting(
            listing=listing,
            description=row["description"],
            workplace=Workplace(row["workplace"]),
            locations=tuple(json.loads(row["locations_json"])),
            compensation=row["compensation"],
            employment_type=row["employment_type"],
            posted_at=row["posted_at"],
        )

    def record_assessment(self, assessment: Assessment) -> bool:
        if self.get(assessment.opportunity_id) is None:
            raise KeyError(f"unknown opportunity {assessment.opportunity_id}")
        with self._db:
            cursor = self._db.execute(
                """
                INSERT INTO assessments (id, opportunity_id, kind, version, basis, assessed_at, result_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (id) DO NOTHING
                """,
                (
                    assessment.id,
                    assessment.opportunity_id,
                    assessment.kind,
                    assessment.version,
                    assessment.basis,
                    assessment.assessed_at,
                    json.dumps(
                        dict(assessment.result), sort_keys=True, separators=(",", ":")
                    ),
                ),
            )
        return cursor.rowcount == 1

    def latest_assessment(self, opportunity_id: str, kind: str) -> Assessment | None:
        row = self._db.execute(
            "SELECT * FROM assessments WHERE opportunity_id = ? AND kind = ? ORDER BY seq DESC LIMIT 1",
            (opportunity_id, kind),
        ).fetchone()
        if row is None:
            return None
        return Assessment(
            opportunity_id=row["opportunity_id"],
            kind=row["kind"],
            version=row["version"],
            assessed_at=row["assessed_at"],
            result=json.loads(row["result_json"]),
            basis=row["basis"],
            id=row["id"],
        )

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> SQLiteLedger:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def _opportunity(row: sqlite3.Row) -> Opportunity:
    return Opportunity(
        id=row["id"],
        identity_key=row["identity_key"],
        lane=Lane(row["lane"]),
        company=row["company"],
        title=row["title"],
        url=row["url"],
        location=row["location"],
        provider=row["provider"],
        tenant=row["tenant"],
        provider_job_id=row["provider_job_id"],
        first_seen=row["first_seen"],
        last_seen=row["last_seen"],
    )
