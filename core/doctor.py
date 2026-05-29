"""Health checks for JobPilot data and local setup."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, urlunparse

from jobpilot.core.application_tracker import ApplicationTracker
from jobpilot.core.bro_client import get_health
from jobpilot.core.config import DATA_DIR
from jobpilot.core.profile_store import ProfileStore
from jobpilot.learning.learning_db import LearningDB

# Original schema: started / submitted / abandoned (the in-app `apply` flow).
# Extended set: applied / rejected / interview — semantically used by the
# Gmail-backfill path and `jobpilot log` for tracking external/manual flows.
# All are accepted by the doctor so backfilled rows don't trip warnings.
_VALID_APPLICATION_STATUSES = {
    "started", "submitted", "abandoned",
    "applied", "rejected", "interview",
}


@dataclass
class DoctorReport:
    """Structured result from a JobPilot health check."""

    status: str
    summary: dict[str, int | str | bool]
    infos: list[str]
    warnings: list[str]
    errors: list[str]

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "summary": self.summary,
            "infos": self.infos,
            "warnings": self.warnings,
            "errors": self.errors,
        }


def run_doctor(data_dir: Optional[Path] = None, *, check_bro: bool = True) -> DoctorReport:
    """Run integrity checks over JobPilot's local data directory."""
    root = data_dir or DATA_DIR
    root.mkdir(parents=True, exist_ok=True)

    infos: list[str] = []
    warnings: list[str] = []
    errors: list[str] = []
    summary: dict[str, int | str | bool] = {
        "data_dir": str(root),
        "applications": 0,
        "templates": 0,
        "actions": 0,
        "reports": 0,
    }

    _check_profile(root, infos, warnings)
    _check_applications(root, summary, infos, warnings)
    _check_learning(root, summary, infos, warnings)
    _check_reports(root, summary, infos)

    if check_bro:
        health = get_health()
        bro_status = str(health.get("status", "unknown"))
        summary["bro_status"] = bro_status
        if bro_status == "ok":
            infos.append("Bro health check passed.")
        else:
            warnings.append(f"Bro health check: {bro_status}")

    status = "error" if errors else "warn" if warnings else "ok"
    return DoctorReport(
        status=status,
        summary=summary,
        infos=infos,
        warnings=warnings,
        errors=errors,
    )


def _check_profile(root: Path, infos: list[str], warnings: list[str]) -> None:
    profile_path = root / "profile.json"
    if not profile_path.exists():
        warnings.append(f"Profile not configured: {profile_path}")
        return

    infos.append("Profile file found.")
    profile = ProfileStore(data_dir=root).load()

    if profile.resume_path:
        resume_path = Path(profile.resume_path).expanduser()
        if resume_path.exists():
            infos.append(f"Resume found: {resume_path.name}")
        else:
            warnings.append(f"Resume path not found: {profile.resume_path}")
    else:
        warnings.append("Resume path not set in profile.")


def _check_applications(
    root: Path,
    summary: dict[str, int | str | bool],
    infos: list[str],
    warnings: list[str],
) -> None:
    db_path = root / "applications.db"
    if not db_path.exists():
        infos.append("No applications DB yet.")
        return

    tracker = ApplicationTracker(data_dir=root)
    try:
        stats = tracker.get_stats()
        total = stats.get("total", 0)
        submitted = stats.get("submitted", 0)
        summary["applications"] = total
        infos.append(f"Applications tracked: {total} total, {submitted} submitted.")

        with sqlite3.connect(str(db_path)) as conn:
            invalid_rows = conn.execute(
                "SELECT DISTINCT status FROM applications WHERE status NOT IN (?, ?, ?)",
                tuple(sorted(_VALID_APPLICATION_STATUSES)),
            ).fetchall()
            invalid_statuses = [str(row[0]) for row in invalid_rows if row[0]]
            if invalid_statuses:
                warnings.append(
                    f"Invalid application statuses found: {', '.join(sorted(invalid_statuses))}"
                )

            url_rows = conn.execute("SELECT job_url FROM applications").fetchall()
            duplicates = _find_duplicate_normalized_urls([str(row[0]) for row in url_rows if row[0]])
            if duplicates:
                warnings.append(
                    f"Duplicate normalized job URLs detected: {len(duplicates)}"
                )
    finally:
        tracker.close()


def _check_learning(
    root: Path,
    summary: dict[str, int | str | bool],
    infos: list[str],
    warnings: list[str],
) -> None:
    db_path = root / "learning.db"
    if not db_path.exists():
        infos.append("No learning DB yet.")
        return

    db = LearningDB(db_path=db_path)
    try:
        templates = db.get_all_templates()
        recent_actions = db.get_recent_actions(limit=1000)
        summary["templates"] = len(templates)
        summary["actions"] = len(recent_actions)
        infos.append(
            f"Learning DB ready: {len(templates)} templates, {len(recent_actions)} recent actions."
        )

        template_stats = db.templates_with_stats()
        low_templates: list[dict[str, object]] = []
        for item in template_stats:
            rate_value = item.get("approval_rate")
            if isinstance(rate_value, (int, float)) and rate_value < 0.6:
                low_templates.append(item)
        if low_templates:
            warnings.append(
                f"{len(low_templates)} low-approval templates need review."
            )
    finally:
        db.close()


def _check_reports(
    root: Path,
    summary: dict[str, int | str | bool],
    infos: list[str],
) -> None:
    report_dir = root / "reports"
    if not report_dir.exists():
        infos.append("No report directory yet.")
        return

    files = [path for path in report_dir.iterdir() if path.is_file()]
    summary["reports"] = len(files)
    infos.append(f"Reports on disk: {len(files)}")


def _normalize_url(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def _find_duplicate_normalized_urls(urls: list[str]) -> list[str]:
    counts: dict[str, int] = {}
    for url in urls:
        normalized = _normalize_url(url)
        counts[normalized] = counts.get(normalized, 0) + 1
    return [url for url, count in counts.items() if count > 1]
