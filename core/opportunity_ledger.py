"""Idempotent SQLite ledger for opportunities and append-only funnel events."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from collections.abc import Mapping
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

EVENT_TYPES = frozenset({"discovered", "verified", "applied", "outreach", "human_reply", "screen",
                         "interview", "offer", "rejected", "withdrawn", "no_response"})
_POSITIVE_OUTCOMES = {"human_reply", "screen", "interview", "offer"}
_NEGATIVE_OUTCOMES = {"rejected", "withdrawn", "no_response"}
@dataclass(frozen=True)
class LedgerEvent:
    id: str
    opportunity_id: str
    event_type: str
    occurred_at: str
    source: str
    provenance: dict[str, Any]
def _now() -> str:
    return datetime.now(UTC).isoformat()

def _data(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Mapping):
        return dict(value)
    return dict(vars(value))

def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _id(prefix: str, *parts: Any) -> str:
    digest = hashlib.sha256(_json(parts).encode()).hexdigest()[:24]
    return f"{prefix}_{digest}"


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


class OpportunityLedger:
    """Canonical opportunity evidence with immutable, deduplicated events."""

    def __init__(self, data_dir: Path | None = None, db_path: Path | None = None):
        root = Path(data_dir or Path(__file__).parent.parent / "data")
        self.db_path = Path(db_path) if db_path else root / "opportunities.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        database_fd = os.open(self.db_path, flags, 0o600)
        try:
            os.fchmod(database_fd, 0o600)
        finally:
            os.close(database_fd)
        self.connection = sqlite3.connect(str(self.db_path))
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self._ensure_schema()

    @classmethod
    def open_readonly(
        cls,
        data_dir: Path | None = None,
        db_path: Path | None = None,
    ) -> OpportunityLedger:
        """Open an existing ledger without creating or repairing anything."""
        root = Path(data_dir or Path(__file__).parent.parent / "data")
        path = Path(db_path) if db_path else root / "opportunities.db"
        if not path.is_file():
            raise FileNotFoundError(path)

        ledger = cls.__new__(cls)
        ledger.db_path = path
        ledger.connection = sqlite3.connect(
            f"{path.resolve().as_uri()}?mode=ro",
            uri=True,
        )
        ledger.connection.row_factory = sqlite3.Row
        ledger.connection.execute("PRAGMA query_only = ON")
        return ledger

    def _ensure_schema(self) -> None:
        self.connection.executescript("""
        CREATE TABLE IF NOT EXISTS opportunities (
          id TEXT PRIMARY KEY, identity_key TEXT NOT NULL UNIQUE, company TEXT NOT NULL, title TEXT NOT NULL, canonical_url TEXT NOT NULL DEFAULT '', provider TEXT NOT NULL DEFAULT '', provider_job_id TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS source_runs (
          id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE, source TEXT NOT NULL, target TEXT NOT NULL, status TEXT NOT NULL, result_count INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '', fetched_at TEXT NOT NULL, payload_json TEXT NOT NULL DEFAULT '{}');
        CREATE TABLE IF NOT EXISTS observations (
          id TEXT PRIMARY KEY, opportunity_id TEXT NOT NULL REFERENCES opportunities(id), source_run_id TEXT REFERENCES source_runs(id), source TEXT NOT NULL, observed_at TEXT NOT NULL, listing_state TEXT NOT NULL DEFAULT 'unknown', payload_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS assessments (
          id TEXT PRIMARY KEY, opportunity_id TEXT NOT NULL REFERENCES opportunities(id), assessment_type TEXT NOT NULL, assessed_at TEXT NOT NULL, version TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT '', result_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events (
          id TEXT PRIMARY KEY, opportunity_id TEXT NOT NULL REFERENCES opportunities(id), event_type TEXT NOT NULL, occurred_at TEXT NOT NULL, source TEXT NOT NULL, provenance_json TEXT NOT NULL DEFAULT '{}');
        CREATE INDEX IF NOT EXISTS idx_events_opportunity ON events(opportunity_id, occurred_at);
        CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
        CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
        """)
        self.connection.commit()
    def upsert_opportunity(self, company: str, title: str, canonical_url: str = "", provider: str = "",
                           provider_job_id: str = "", identity_key: str = "") -> str:
        rows = self.connection.execute("SELECT * FROM opportunities").fetchall()
        url_key = canonical_url.strip().lower().rstrip("/")
        existing = next((row for row in rows if
                         (url_key and row["canonical_url"].lower().rstrip("/") == url_key)
                         or (identity_key and row["identity_key"] == identity_key)), None)
        if not existing and not identity_key and provider and provider_job_id:
            existing = next((row for row in rows
                             if _norm(row["provider"]) == _norm(provider)
                             and _norm(row["provider_job_id"]) == _norm(provider_job_id)), None)
        if not existing and not identity_key and not (provider and provider_job_id):
            existing = next((row for row in rows if _norm(row["company"]) == _norm(company)
                             and _norm(row["title"]) == _norm(title)), None)
        if existing:
            self.connection.execute(
                """UPDATE opportunities SET company=?, title=?, canonical_url=COALESCE(NULLIF(?,''), canonical_url), provider=COALESCE(NULLIF(provider,''), ?),
                   provider_job_id=COALESCE(NULLIF(provider_job_id,''), ?), updated_at=?
                   WHERE id=?""", (company.strip(), title.strip(), canonical_url.strip(),
                                    provider.strip(), provider_job_id.strip(), _now(), existing["id"]),
            )
            self.connection.commit()
            return str(existing["id"])
        key = identity_key or (
            f"provider:{_norm(provider)}:{_norm(provider_job_id)}" if provider and provider_job_id
            else f"url:{canonical_url.strip().lower()}" if canonical_url
            else f"role:{_norm(company)}:{_norm(title)}"
        )
        opportunity_id, now = _id("opp", key), _now()
        self.connection.execute(
            """INSERT INTO opportunities VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(identity_key) DO UPDATE SET
                 company=excluded.company, title=excluded.title,
                 canonical_url=COALESCE(NULLIF(excluded.canonical_url,''), opportunities.canonical_url),
                 provider=COALESCE(NULLIF(excluded.provider,''), opportunities.provider),
                 provider_job_id=COALESCE(NULLIF(excluded.provider_job_id,''), opportunities.provider_job_id),
                 updated_at=excluded.updated_at""",
            (opportunity_id, key, company.strip(), title.strip(), canonical_url.strip(),
             provider.strip(), provider_job_id.strip(), now, now),
        )
        self.connection.commit()
        row = self.connection.execute("SELECT id FROM opportunities WHERE identity_key = ?", (key,)).fetchone()
        return str(row["id"])
    def record_source_run(self, source: str, target: str = "", status: str = "", result_count: int = 0,
                          fetched_at: str = "", idempotency_key: str = "", error: str = "",
                          payload: Mapping[str, Any] | None = None) -> str:
        fetched_at = fetched_at or _now()
        key = idempotency_key or _json((source, target, status, result_count, fetched_at, error, payload or {}))
        run_id = _id("run", key)
        self.connection.execute(
            "INSERT OR IGNORE INTO source_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, key, source, target, status, result_count, error, fetched_at, _json(payload or {})),
        )
        self.connection.commit()
        return run_id
    def record_observation(self, opportunity_id: str, observation: Any, source_run_id: str = "") -> str:
        item = _data(observation)
        source = str(item.get("provider") or item.get("source_kind") or "unknown")
        observed_at = str(item.get("fetched_at") or item.get("observed_at") or _now())
        state = str(item.get("listing_state") or "unknown")
        observation_id = _id("obs", opportunity_id, source_run_id, item)
        self.connection.execute(
            "INSERT OR IGNORE INTO observations VALUES (?, ?, NULLIF(?,''), ?, ?, ?, ?)",
            (observation_id, opportunity_id, source_run_id, source, observed_at, state, _json(item)),
        )
        self.connection.commit()
        return observation_id
    def record_assessment(self, opportunity_id: str, assessment_type: str, result: Any,
                          assessed_at: str = "", version: str = "", source: str = "") -> str:
        item, assessed_at = _data(result), assessed_at or _now()
        assessment_id = _id("assessment", opportunity_id, assessment_type, assessed_at, version, source, item)
        self.connection.execute(
            "INSERT OR IGNORE INTO assessments VALUES (?, ?, ?, ?, ?, ?, ?)",
            (assessment_id, opportunity_id, assessment_type, assessed_at, version, source, _json(item)),
        )
        self.connection.commit()
        return assessment_id
    def append_event(self, opportunity_id: str, event_type: str, occurred_at: str = "",
                     source: str = "manual", provenance: Mapping[str, Any] | None = None,
                     idempotency_key: str = "") -> str:
        event_type = event_type.strip().lower()
        if event_type not in EVENT_TYPES:
            raise ValueError(f"Unsupported event '{event_type}'")
        occurred_at, details = occurred_at or _now(), dict(provenance or {})
        key = idempotency_key or _json((opportunity_id, event_type, occurred_at, source, details))
        event_id = _id("event", key)
        self.connection.execute(
            "INSERT OR IGNORE INTO events VALUES (?, ?, ?, ?, ?, ?)",
            (event_id, opportunity_id, event_type, occurred_at, source, _json(details)),
        )
        self.connection.commit()
        return event_id
    def iter_events(self, opportunity_id: str = "", event_type: str = "", since: str = "") -> list[LedgerEvent]:
        clauses, values = [], []
        for column, value in (("opportunity_id", opportunity_id), ("event_type", event_type)):
            if value:
                clauses.append(f"{column} = ?")
                values.append(value)
        if since:
            clauses.append("occurred_at >= ?")
            values.append(since)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.connection.execute(
            f"SELECT * FROM events{where} ORDER BY occurred_at, id", values
        ).fetchall()
        return [LedgerEvent(r["id"], r["opportunity_id"], r["event_type"], r["occurred_at"],
                            r["source"], json.loads(r["provenance_json"])) for r in rows]
    def iter_observations(self, opportunity_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT payload_json FROM observations WHERE opportunity_id = ? ORDER BY observed_at, id",
            (opportunity_id,),
        ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def iter_opportunities(self) -> list[dict[str, Any]]:
        """Return opportunity identity rows for read-only reconciliation."""
        rows = self.connection.execute(
            "SELECT id, identity_key, canonical_url, provider, provider_job_id "
            "FROM opportunities ORDER BY id"
        ).fetchall()
        return [dict(row) for row in rows]

    def latest_assessment(
        self,
        opportunity_id: str,
        assessment_type: str,
    ) -> dict[str, Any] | None:
        """Return the newest assessment and its ledger timestamp."""
        row = self.connection.execute(
            "SELECT assessed_at, result_json FROM assessments "
            "WHERE opportunity_id = ? AND assessment_type = ? "
            "ORDER BY assessed_at DESC, id DESC LIMIT 1",
            (opportunity_id, assessment_type),
        ).fetchone()
        if row is None:
            return None
        result = json.loads(row["result_json"])
        if not isinstance(result, dict):
            raise ValueError("assessment result must be an object")
        return {"assessed_at": str(row["assessed_at"]), "result": result}
    def conflicting_outcomes(self, opportunity_id: str) -> set[str]:
        types = {event.event_type for event in self.iter_events(opportunity_id)}
        return types & (_POSITIVE_OUTCOMES | _NEGATIVE_OUTCOMES) if (
            types & _POSITIVE_OUTCOMES and types & _NEGATIVE_OUTCOMES
        ) else set()
    def table_count(self, table: str) -> int:
        if table not in {"opportunities", "source_runs", "observations", "assessments", "events"}:
            raise ValueError("unknown ledger table")
        return int(self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    def close(self) -> None:
        self.connection.close()
