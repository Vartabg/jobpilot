"""
Learning DB — SQLite-backed storage for templates and action history.

Replaces the file-based ``templates.json`` and ``actions.jsonl`` with a
single ``learning.db`` that supports concurrent access and SQL queries.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from jobpilot.core.config import DATA_DIR


_DB_FILE = DATA_DIR / "learning.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS templates (
    question    TEXT PRIMARY KEY,
    answer      TEXT NOT NULL,
    times_used  INTEGER DEFAULT 0,
    last_used   TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS actions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT NOT NULL,
    action_type     TEXT NOT NULL,
    job_url         TEXT,
    job_title       TEXT,
    company         TEXT,
    field_label     TEXT,
    field_type      TEXT,
    suggested_value TEXT,
    final_value     TEXT,
    confidence      REAL,
    time_spent_ms   INTEGER,
    step_number     INTEGER
);

CREATE INDEX IF NOT EXISTS idx_actions_type ON actions(action_type);
CREATE INDEX IF NOT EXISTS idx_actions_job  ON actions(job_url);
"""


class LearningDB:
    """Unified SQLite store for answer templates and action history.

    Thread-safe via ``check_same_thread=False`` — safe for use in an
    async application where DB calls happen from the same event loop.
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = db_path or _DB_FILE
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    # -------------------------------------------------------------------
    # Templates
    # -------------------------------------------------------------------

    def get_template(self, question: str) -> Optional[str]:
        """Return the answer for *question*, or ``None``."""
        row = self._conn.execute(
            "SELECT answer FROM templates WHERE question = ?",
            (question,),
        ).fetchone()
        return row["answer"] if row else None

    def upsert_template(self, question: str, answer: str) -> None:
        """Insert or update an answer template."""
        self._conn.execute(
            """
            INSERT INTO templates (question, answer, times_used, last_used, created_at)
            VALUES (?, ?, 0, NULL, datetime('now'))
            ON CONFLICT(question) DO UPDATE SET
                answer    = excluded.answer,
                last_used = datetime('now')
            """,
            (question, answer),
        )
        self._conn.commit()

    def delete_template(self, question: str) -> bool:
        """Delete a template. Returns True if a row was deleted."""
        cur = self._conn.execute(
            "DELETE FROM templates WHERE question = ?", (question,),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def get_all_templates(self) -> dict[str, str]:
        """Return all templates as ``{question: answer}``."""
        rows = self._conn.execute(
            "SELECT question, answer FROM templates"
        ).fetchall()
        return {r["question"]: r["answer"] for r in rows}

    def increment_usage(self, question: str) -> None:
        """Bump times_used and set last_used for a template."""
        self._conn.execute(
            """
            UPDATE templates
            SET times_used = times_used + 1,
                last_used  = datetime('now')
            WHERE question = ?
            """,
            (question,),
        )
        self._conn.commit()

    # -------------------------------------------------------------------
    # Actions
    # -------------------------------------------------------------------

    def record_action(
        self,
        *,
        action_type: str,
        timestamp: Optional[str] = None,
        job_url: Optional[str] = None,
        job_title: Optional[str] = None,
        company: Optional[str] = None,
        field_label: Optional[str] = None,
        field_type: Optional[str] = None,
        suggested_value: Optional[str] = None,
        final_value: Optional[str] = None,
        confidence: Optional[float] = None,
        time_spent_ms: Optional[int] = None,
        step_number: Optional[int] = None,
    ) -> int:
        """Insert an action row. Returns the new row id."""
        ts = timestamp or datetime.now().isoformat()
        cur = self._conn.execute(
            """
            INSERT INTO actions
                (timestamp, action_type, job_url, job_title, company,
                 field_label, field_type, suggested_value, final_value,
                 confidence, time_spent_ms, step_number)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ts, action_type, job_url, job_title, company,
                field_label, field_type, suggested_value, final_value,
                confidence, time_spent_ms, step_number,
            ),
        )
        self._conn.commit()
        return cur.lastrowid

    def get_stats(self) -> dict[str, int | float]:
        """Aggregate statistics matching the old ``ActionRecorder.get_stats`` shape."""
        rows = self._conn.execute(
            """
            SELECT action_type, COUNT(*) AS cnt
            FROM actions
            GROUP BY action_type
            """,
        ).fetchall()
        counts = {r["action_type"]: r["cnt"] for r in rows}

        approved = counts.get("field_approved", 0)
        edited = counts.get("field_edited", 0)
        skipped = counts.get("field_skipped", 0)
        total_fields = approved + edited + skipped

        return {
            "total_applications": counts.get("app_started", 0),
            "submitted": counts.get("app_submitted", 0),
            "abandoned": counts.get("app_abandoned", 0),
            "fields_approved": approved,
            "fields_edited": edited,
            "fields_skipped": skipped,
            "approval_rate": approved / total_fields if total_fields else 0.0,
        }

    def get_recent_actions(self, limit: int = 50) -> list[dict[str, object]]:
        """Return the most recent *limit* actions (newest first)."""
        rows = self._conn.execute(
            "SELECT * FROM actions ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    # -------------------------------------------------------------------
    # Advanced queries (enabled by SQL, impossible with JSONL)
    # -------------------------------------------------------------------

    def approval_rate_by_field(self) -> dict[str, float]:
        """Per-field approval rate: ``{field_label: rate}``."""
        rows = self._conn.execute(
            """
            SELECT field_label,
                   SUM(CASE WHEN action_type = 'field_approved' THEN 1 ELSE 0 END) AS ok,
                   COUNT(*) AS total
            FROM actions
            WHERE action_type IN ('field_approved', 'field_edited', 'field_skipped')
              AND field_label IS NOT NULL
            GROUP BY field_label
            """,
        ).fetchall()
        return {r["field_label"]: r["ok"] / r["total"] for r in rows if r["total"]}

    def low_confidence_templates(self, threshold: float = 0.6) -> list[dict[str, object]]:
        """Templates whose most recent fill had confidence below *threshold*."""
        rows = self._conn.execute(
            """
            SELECT DISTINCT a.field_label, a.suggested_value, a.confidence
            FROM actions a
            WHERE a.action_type = 'field_approved'
              AND a.confidence < ?
              AND a.suggested_value IS NOT NULL
            ORDER BY a.confidence ASC
            """,
            (threshold,),
        ).fetchall()
        return [dict(r) for r in rows]

    def templates_with_stats(self) -> list[dict[str, object]]:
        """Return all templates enriched with per-template approval stats.

        Each dict contains:
        - question, answer, times_used, created_at
        - total_actions, approved, edited, approval_rate
        Sorted by approval rate ascending (worst first).
        """
        rows = self._conn.execute(
            """
            SELECT
                t.question,
                t.answer,
                t.times_used,
                t.created_at,
                COUNT(a.id) AS total_actions,
                SUM(CASE WHEN a.action_type = 'field_approved' THEN 1 ELSE 0 END) AS approved,
                SUM(CASE WHEN a.action_type = 'field_edited' THEN 1 ELSE 0 END) AS edited
            FROM templates t
            LEFT JOIN actions a ON a.field_label = t.question
                AND a.action_type IN ('field_approved', 'field_edited', 'field_skipped')
            GROUP BY t.question
            ORDER BY
                CASE WHEN COUNT(a.id) = 0 THEN 1 ELSE 0 END,
                CAST(SUM(CASE WHEN a.action_type = 'field_approved' THEN 1 ELSE 0 END) AS REAL) / MAX(COUNT(a.id), 1) ASC
            """,
        ).fetchall()
        result = []
        for r in rows:
            total = r["total_actions"] or 0
            ok = r["approved"] or 0
            result.append({
                "question": r["question"],
                "answer": r["answer"],
                "times_used": r["times_used"],
                "created_at": r["created_at"],
                "total_actions": total,
                "approved": ok,
                "edited": r["edited"] or 0,
                "approval_rate": ok / total if total > 0 else None,
            })
        return result


# -------------------------------------------------------------------
# Module-level singleton (path-keyed)
# -------------------------------------------------------------------

_dbs: dict[str, LearningDB] = {}


def get_learning_db(db_path: Optional[Path] = None) -> LearningDB:
    """Return (and lazily create) a ``LearningDB`` instance for *db_path*."""
    resolved = str(db_path or _DB_FILE)
    if resolved not in _dbs:
        _dbs[resolved] = LearningDB(Path(resolved))
    return _dbs[resolved]


def _reset_learning_db() -> None:
    """Close all cached instances. Used by tests for isolation."""
    for db in _dbs.values():
        try:
            db.close()
        except Exception:
            pass
    _dbs.clear()
