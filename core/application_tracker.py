"""
Application Tracker — prevents re-applying to the same job.

SQLite-backed store of every application with URL deduplication,
status tracking, and stats queries.
"""

import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table

from jobpilot.core.config import DATA_DIR
from jobpilot.core.logger import get_logger
from jobpilot.core.role_identity import (
    canonicalize_role_url,
    explicit_role_identity_key,
)

console = Console()
log = get_logger(__name__)

DB_DIR = DATA_DIR
VALID_STATUSES = {
    "started",
    "submitted",
    "applied",
    "rejected",
    "interview",
    "abandoned",
    "skipped",
    "offer",
    "withdrawn",
    "outreach",
    "human_reply",
    "screen",
    "no_response",
}


@dataclass
class TrackedApplication:
    """A single tracked application."""
    job_url: str
    job_title: str
    company: str
    applied_at: str
    status: str  # "started", "submitted", "abandoned"
    source: str = "manual"


class ApplicationTracker:
    """
    SQLite-backed tracker that remembers every application.

    Prevents duplicates, tracks status, and provides stats.
    The database lives alongside other data files in data/applications.db.
    """

    def __init__(self, data_dir: Path | None = None):
        self.db_path = (data_dir or DB_DIR) / "applications.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._ensure_schema()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _ensure_schema(self):
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_url TEXT NOT NULL,
                job_title TEXT NOT NULL DEFAULT '',
                company TEXT NOT NULL DEFAULT '',
                applied_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'started',
                step_reached INTEGER NOT NULL DEFAULT 1,
                fields_filled INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            )
        """)
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(applications)").fetchall()
        }
        if "source" not in columns:
            conn.execute(
                "ALTER TABLE applications ADD COLUMN source TEXT NOT NULL DEFAULT 'legacy'"
            )
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_applications_url
            ON applications(job_url)
        """)
        conn.commit()

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def _normalize_url(self, url: str) -> str:
        """Canonicalize without dropping provider identity such as Indeed ``jk``."""
        return canonicalize_role_url(url)

    def _normalize_status(self, status: str) -> str:
        normalized = (status or "applied").strip().lower()
        if normalized not in VALID_STATUSES:
            raise ValueError(
                f"Unsupported status '{status}'. Use one of: {', '.join(sorted(VALID_STATUSES))}"
            )
        return normalized

    def _find_role_row(self, url: str) -> sqlite3.Row | None:
        """Find an exact URL or provider-native identity match.

        The fallback scan also recognizes rows written before a provider host
        alias was canonicalized. Application history is small, so this keeps
        the schema compatible without a destructive URL migration.
        """
        normalized = self._normalize_url(url)
        if not normalized:
            return None
        conn = self._get_conn()
        exact = conn.execute(
            """SELECT id, job_url, status FROM applications
               WHERE job_url = ? LIMIT 1""",
            (normalized,),
        ).fetchone()
        if exact is not None:
            return exact

        identity_key = explicit_role_identity_key(normalized)
        if not identity_key:
            return None
        rows = conn.execute(
            """SELECT id, job_url, status FROM applications
               ORDER BY updated_at DESC, id DESC"""
        ).fetchall()
        return next(
            (
                row
                for row in rows
                if explicit_role_identity_key(row["job_url"]) == identity_key
            ),
            None,
        )

    def _synthetic_url(
        self,
        company: str,
        title: str = "",
        applied_at: str = "",
        source: str = "manual",
    ) -> str:
        """Build a stable synthetic URL for external/manual applications."""
        slug = re.sub(
            r"[^a-z0-9]+",
            "-",
            f"{company}-{title or 'role'}".lower(),
        ).strip("-") or "unknown"
        date = (applied_at or datetime.now().date().isoformat())[:10]
        source_slug = re.sub(r"[^a-z0-9]+", "-", source.lower()).strip("-") or "manual"
        return f"{source_slug}://{slug}/{date}"

    def has_applied(self, url: str) -> bool:
        """Check if we've already started/submitted this job."""
        return self._find_role_row(url) is not None

    def get_status(self, url: str) -> str | None:
        """Get the status of a previous application."""
        row = self._find_role_row(url)
        return row["status"] if row else None

    def role_status(self, company: str, title: str = "", url: str = "") -> str | None:
        """Return status for this role only; sibling outcomes never propagate."""
        normalized = self._normalize_url(url)
        if normalized:
            direct = self.get_status(normalized)
            if direct is not None:
                return direct
            # A concrete URL that did not match is evidence of a different role.
            # Do not let a repeated company/title inherit a sibling outcome.
            return None
        if not company.strip() or not title.strip():
            return None
        row = self._get_conn().execute(
            """SELECT status FROM applications
               WHERE LOWER(company) = LOWER(?) AND LOWER(job_title) = LOWER(?)
               ORDER BY updated_at DESC, id DESC LIMIT 1""",
            (company.strip(), title.strip()),
        ).fetchone()
        return row["status"] if row else None

    def has_applied_to_company(self, company: str) -> bool:
        """Case-insensitive dedup at the company level.

        URL-based dedup misses cases where the same company is on a different
        board, posts a slightly different role, or where backfilled rows use
        synthetic URLs (e.g. `gmail-backfill://...`). Company-level dedup
        catches those for sourcing-time filtering.
        """
        if not company or not company.strip():
            return False
        row = self._get_conn().execute(
            "SELECT 1 FROM applications WHERE LOWER(company) = LOWER(?) LIMIT 1",
            (company.strip(),),
        ).fetchone()
        return row is not None

    def company_status(self, company: str) -> str | None:
        """Latest application status for a company (rejected > applied > queued)."""
        if not company or not company.strip():
            return None
        # Prefer terminal/interview states for company-first dedup. This is
        # intentionally conservative because JobPilot should steer toward one
        # best role per company, not repeated applications to the same firm.
        rows = self._get_conn().execute(
            "SELECT status FROM applications WHERE LOWER(company) = LOWER(?)",
            (company.strip(),),
        ).fetchall()
        if not rows:
            return None
        statuses = {r["status"] for r in rows}
        if "interview" in statuses:
            return "interview"
        if "rejected" in statuses:
            return "rejected"
        if "applied" in statuses or "submitted" in statuses:
            return "applied"
        return next(iter(statuses), None)

    def log_application(
        self,
        company: str,
        title: str = "",
        url: str = "",
        status: str = "applied",
        applied_at: str | None = None,
        source: str = "manual",
    ) -> TrackedApplication:
        """Log an application from any source.

        This is the durable entry point for manual applies, Gmail backfills,
        dashboard quick logs, and queue actions. It is idempotent by URL and,
        when a title is available, by company + title.
        """
        company = (company or "").strip()
        title = (title or "").strip()
        if not company:
            raise ValueError("company is required")

        normalized_status = self._normalize_status(status)
        date_value = applied_at or datetime.now().date().isoformat()
        has_real_url = bool(url and url.strip())
        raw_url = url.strip() if has_real_url else self._synthetic_url(company, title, date_value, source)
        normalized_url = self._normalize_url(raw_url)
        now = datetime.now().isoformat()
        conn = self._get_conn()

        if has_real_url:
            existing = self._find_role_row(normalized_url)
        elif title:
            # Prefer an exact-URL match over a company+title match, and break
            # company+title ties by most recently updated row, so the row we
            # update is deterministic (and never steals a URL another row owns).
            existing = conn.execute(
                """SELECT id, job_url FROM applications
                   WHERE job_url = ?
                      OR (LOWER(company) = LOWER(?) AND LOWER(COALESCE(job_title,'')) = LOWER(?))
                   ORDER BY (job_url = ?) DESC, updated_at DESC, id DESC
                   LIMIT 1""",
                (normalized_url, company, title, normalized_url),
            ).fetchone()
        else:
            existing = conn.execute(
                "SELECT id, job_url FROM applications WHERE job_url = ? LIMIT 1",
                (normalized_url,),
            ).fetchone()

        if existing:
            stored_url = normalized_url if has_real_url else existing["job_url"]
            try:
                conn.execute(
                    """UPDATE applications
                       SET job_url = ?,
                           job_title = COALESCE(NULLIF(?, ''), job_title),
                           company = ?,
                           status = ?,
                           source = ?,
                           updated_at = ?
                       WHERE id = ?""",
                    (stored_url, title, company, normalized_status, source, now, existing["id"]),
                )
            except sqlite3.IntegrityError:
                # The URL already belongs to a different row — it's the same
                # application, so update that row instead of crashing.
                owner = conn.execute(
                    "SELECT id FROM applications WHERE job_url = ? LIMIT 1",
                    (stored_url,),
                ).fetchone()
                if owner is None:
                    raise
                conn.execute(
                    """UPDATE applications
                       SET job_title = COALESCE(NULLIF(?, ''), job_title),
                           company = ?,
                           status = ?,
                           source = ?,
                           updated_at = ?
                       WHERE id = ?""",
                    (title, company, normalized_status, source, now, owner["id"]),
                )
        else:
            stored_url = normalized_url
            conn.execute(
                """INSERT INTO applications
                   (job_url, job_title, company, applied_at, status, source, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (stored_url, title, company, date_value, normalized_status, source, now),
            )
        conn.commit()

        return TrackedApplication(
            job_url=stored_url,
            job_title=title,
            company=company,
            applied_at=date_value,
            status=normalized_status,
            source=source,
        )

    def mark_started(
        self, url: str, title: str = "", company: str = "", source: str = "browser"
    ):
        """Record that an application was started."""
        normalized = self._normalize_url(url)
        now = datetime.now().isoformat()
        conn = self._get_conn()
        conn.execute("""
            INSERT INTO applications
                (job_url, job_title, company, applied_at, status, source, updated_at)
            VALUES (?, ?, ?, ?, 'started', ?, ?)
            ON CONFLICT(job_url) DO UPDATE SET
                job_title = excluded.job_title,
                company = excluded.company,
                status = 'started',
                source = excluded.source,
                updated_at = excluded.updated_at
        """, (normalized, title, company, now, source, now))
        conn.commit()

    def mark_submitted(self, url: str):
        """Record that the application was submitted."""
        normalized = self._normalize_url(url)
        now = datetime.now().isoformat()
        conn = self._get_conn()
        conn.execute("""
            UPDATE applications SET status = 'submitted', updated_at = ?
            WHERE job_url = ?
        """, (now, normalized))
        conn.commit()

    def mark_applied(self, url: str, title: str = "", company: str = ""):
        """Record a manually confirmed application.

        Dashboard applies happen outside the LinkedIn watch loop, so they need
        an upsert that stores the company/title and marks the row as applied.
        """
        if company:
            self.log_application(company=company, title=title, url=url, status="applied", source="queue")
            return
        normalized = self._normalize_url(url)
        now = datetime.now().isoformat()
        conn = self._get_conn()
        conn.execute("""
            INSERT INTO applications (job_url, job_title, company, applied_at, status, updated_at)
            VALUES (?, ?, ?, ?, 'applied', ?)
            ON CONFLICT(job_url) DO UPDATE SET
                job_title = excluded.job_title,
                company = excluded.company,
                status = 'applied',
                updated_at = excluded.updated_at
        """, (normalized, title, company, now, now))
        conn.commit()

    def mark_abandoned(self, url: str, step_reached: int = 1):
        """Record that the application was abandoned."""
        normalized = self._normalize_url(url)
        now = datetime.now().isoformat()
        conn = self._get_conn()
        conn.execute("""
            UPDATE applications SET status = 'abandoned', step_reached = ?, updated_at = ?
            WHERE job_url = ?
        """, (step_reached, now, normalized))
        conn.commit()

    def update_progress(self, url: str, fields_filled: int = 0):
        """Update the progress count for an application."""
        normalized = self._normalize_url(url)
        now = datetime.now().isoformat()
        conn = self._get_conn()
        conn.execute("""
            UPDATE applications SET fields_filled = ?, updated_at = ?
            WHERE job_url = ?
        """, (fields_filled, now, normalized))
        conn.commit()

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_recent(self, limit: int = 10, status: str | None = None) -> list[TrackedApplication]:
        """Get the most recent applications."""
        if status:
            normalized_status = self._normalize_status(status)
            rows = self._get_conn().execute(
                "SELECT job_url, job_title, company, applied_at, status, source "
                "FROM applications WHERE status = ? ORDER BY applied_at DESC LIMIT ?",
                (normalized_status, limit),
            ).fetchall()
        else:
            rows = self._get_conn().execute(
                "SELECT job_url, job_title, company, applied_at, status, source "
                "FROM applications ORDER BY applied_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [TrackedApplication(**dict(r)) for r in rows]

    def get_stats(self) -> dict[str, int]:
        """Get aggregate statistics."""
        conn = self._get_conn()
        total = conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
        applied = conn.execute(
            "SELECT COUNT(*) FROM applications WHERE status = 'applied'"
        ).fetchone()[0]
        submitted = conn.execute(
            "SELECT COUNT(*) FROM applications WHERE status = 'submitted'"
        ).fetchone()[0]
        rejected = conn.execute(
            "SELECT COUNT(*) FROM applications WHERE status = 'rejected'"
        ).fetchone()[0]
        interview = conn.execute(
            "SELECT COUNT(*) FROM applications WHERE status = 'interview'"
        ).fetchone()[0]
        abandoned = conn.execute(
            "SELECT COUNT(*) FROM applications WHERE status = 'abandoned'"
        ).fetchone()[0]
        in_progress = conn.execute(
            "SELECT COUNT(*) FROM applications WHERE status = 'started'"
        ).fetchone()[0]
        return {
            "total": total,
            "applied": applied,
            "submitted": submitted,
            "rejected": rejected,
            "interview": interview,
            "abandoned": abandoned,
            "in_progress": in_progress,
        }

    def get_status_counts(self) -> dict[str, int]:
        """Return counts by stored status."""
        rows = self._get_conn().execute(
            "SELECT status, COUNT(*) AS count FROM applications GROUP BY status"
        ).fetchall()
        return {r["status"]: int(r["count"]) for r in rows}

    def display_recent(self, limit: int = 10):
        """Display recent applications in a Rich table."""
        apps = self.get_recent(limit)
        if not apps:
            console.print("[dim]No applications tracked yet.[/dim]")
            return

        table = Table(title=f"Recent Applications (last {limit})", show_header=True)
        table.add_column("Title", style="cyan", max_width=40)
        table.add_column("Company", style="white")
        table.add_column("Status", style="bold")
        table.add_column("Date", style="dim")

        status_colors = {
            "applied": "green",
            "submitted": "green",
            "interview": "cyan",
            "rejected": "red",
            "abandoned": "red",
            "started": "yellow",
        }
        for app in apps:
            color = status_colors.get(app.status, "white")
            date_str = app.applied_at[:10] if app.applied_at else ""
            table.add_row(
                app.job_title or "(untitled)",
                app.company or "(unknown)",
                f"[{color}]{app.status}[/{color}]",
                date_str,
            )
        console.print(table)

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------
_tracker_state = threading.local()


def get_application_tracker() -> ApplicationTracker:
    """Get one tracker connection per thread.

    FastAPI queue refreshes run in a worker thread. A process-global SQLite
    connection would be reused across threads and fail at runtime.
    """
    tracker = getattr(_tracker_state, "tracker", None)
    if tracker is None:
        tracker = ApplicationTracker()
        _tracker_state.tracker = tracker
    return tracker
