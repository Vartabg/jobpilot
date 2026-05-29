"""
Application Tracker — prevents re-applying to the same job.

SQLite-backed store of every application with URL deduplication,
status tracking, and stats queries.
"""

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional
from dataclasses import dataclass
from rich.console import Console
from rich.table import Table

from jobpilot.core.logger import get_logger

console = Console()
log = get_logger(__name__)

DB_DIR = Path(__file__).parent.parent / "data"


@dataclass
class TrackedApplication:
    """A single tracked application."""
    job_url: str
    job_title: str
    company: str
    applied_at: str
    status: str  # "started", "submitted", "abandoned"


class ApplicationTracker:
    """
    SQLite-backed tracker that remembers every application.

    Prevents duplicates, tracks status, and provides stats.
    The database lives alongside other data files in data/applications.db.
    """

    def __init__(self, data_dir: Optional[Path] = None):
        self.db_path = (data_dir or DB_DIR) / "applications.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
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
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_applications_url
            ON applications(job_url)
        """)
        conn.commit()

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def _normalize_url(self, url: str) -> str:
        """Strip query params and fragments for dedup comparison."""
        from urllib.parse import urlparse, urlunparse
        parsed = urlparse(url)
        # Keep scheme + host + path only
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))

    def has_applied(self, url: str) -> bool:
        """Check if we've already started/submitted this job."""
        normalized = self._normalize_url(url)
        row = self._get_conn().execute(
            "SELECT status FROM applications WHERE job_url = ?",
            (normalized,),
        ).fetchone()
        return row is not None

    def get_status(self, url: str) -> Optional[str]:
        """Get the status of a previous application."""
        normalized = self._normalize_url(url)
        row = self._get_conn().execute(
            "SELECT status FROM applications WHERE job_url = ?",
            (normalized,),
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

    def company_status(self, company: str) -> Optional[str]:
        """Latest application status for a company (rejected > applied > queued)."""
        if not company or not company.strip():
            return None
        # Prefer rejected if any row is rejected (dead ground signal); else applied.
        rows = self._get_conn().execute(
            "SELECT status FROM applications WHERE LOWER(company) = LOWER(?)",
            (company.strip(),),
        ).fetchall()
        if not rows:
            return None
        statuses = {r["status"] for r in rows}
        if "rejected" in statuses:
            return "rejected"
        if "applied" in statuses or "submitted" in statuses:
            return "applied"
        return next(iter(statuses), None)

    def mark_started(self, url: str, title: str = "", company: str = ""):
        """Record that an application was started."""
        normalized = self._normalize_url(url)
        now = datetime.now().isoformat()
        conn = self._get_conn()
        conn.execute("""
            INSERT INTO applications (job_url, job_title, company, applied_at, status, updated_at)
            VALUES (?, ?, ?, ?, 'started', ?)
            ON CONFLICT(job_url) DO UPDATE SET
                job_title = excluded.job_title,
                company = excluded.company,
                status = 'started',
                updated_at = excluded.updated_at
        """, (normalized, title, company, now, now))
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

    def get_recent(self, limit: int = 10) -> list[TrackedApplication]:
        """Get the most recent applications."""
        rows = self._get_conn().execute(
            "SELECT job_url, job_title, company, applied_at, status "
            "FROM applications ORDER BY applied_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [TrackedApplication(**dict(r)) for r in rows]

    def get_stats(self) -> dict[str, int]:
        """Get aggregate statistics."""
        conn = self._get_conn()
        total = conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
        submitted = conn.execute(
            "SELECT COUNT(*) FROM applications WHERE status = 'submitted'"
        ).fetchone()[0]
        abandoned = conn.execute(
            "SELECT COUNT(*) FROM applications WHERE status = 'abandoned'"
        ).fetchone()[0]
        in_progress = conn.execute(
            "SELECT COUNT(*) FROM applications WHERE status = 'started'"
        ).fetchone()[0]
        return {
            "total": total,
            "submitted": submitted,
            "abandoned": abandoned,
            "in_progress": in_progress,
        }

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

        status_colors = {"submitted": "green", "abandoned": "red", "started": "yellow"}
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
_tracker: Optional[ApplicationTracker] = None


def get_application_tracker() -> ApplicationTracker:
    """Get the global application tracker instance."""
    global _tracker
    if _tracker is None:
        _tracker = ApplicationTracker()
    return _tracker
