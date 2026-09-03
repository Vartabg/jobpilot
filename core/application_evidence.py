"""Read-only application evidence merged from JobPilot's trusted sources."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, urlunparse


class EvidenceSourceError(RuntimeError):
    """Raised when a configured application-evidence source is unavailable."""


@dataclass(frozen=True)
class EvidenceRecord:
    company: str
    title: str
    url: str = ""
    status: str = "started"
    source: str = "tracker"


def _norm_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _norm_url(value: str) -> str:
    if not value:
        return ""
    parsed = urlparse(value)
    return urlunparse((
        parsed.scheme.lower(),
        parsed.netloc.lower(),
        parsed.path.rstrip("/"),
        "",
        "",
        "",
    ))


def _same_role(left: str, right: str) -> bool:
    left_n = _norm_text(left)
    right_n = _norm_text(right)
    if not left_n or not right_n:
        return False
    return left_n == right_n or left_n in right_n or right_n in left_n


class ApplicationEvidenceIndex:
    """Merged application evidence with conservative company + role matching."""

    def __init__(
        self,
        records: list[EvidenceRecord],
        errors: Optional[list[str]] = None,
    ):
        self.records = records
        self.errors = errors or []

    @classmethod
    def build(
        cls,
        *,
        tracker,
        employment_dir: Optional[Path] = None,
        gmail_cache_path: Optional[Path] = None,
        gmail_cache_max_age_hours: int = 0,
    ) -> "ApplicationEvidenceIndex":
        records: list[EvidenceRecord] = []
        errors: list[str] = []

        try:
            for item in tracker.get_recent(limit=10000):
                records.append(EvidenceRecord(
                    company=item.company,
                    title=item.job_title,
                    url=item.job_url,
                    status=item.status,
                    source="tracker",
                ))
        except Exception as exc:
            errors.append(f"canonical application tracker could not be read ({exc})")

        if employment_dir is not None:
            cls._add_employment_records(Path(employment_dir), records, errors)
        if gmail_cache_path is not None:
            cls._add_gmail_records(
                Path(gmail_cache_path), records, errors, gmail_cache_max_age_hours
            )

        return cls(records, errors)

    @staticmethod
    def _add_employment_records(
        root: Path,
        records: list[EvidenceRecord],
        errors: list[str],
    ) -> None:
        if not root.is_dir():
            errors.append(f"application packet folder is unavailable: {root}")
            return
        try:
            files = list(root.glob("*/notes.md")) + list(root.glob("*/SUBMITTED.md"))
            for path in files:
                first_line = path.read_text(errors="replace").splitlines()[:1]
                if not first_line:
                    continue
                heading = re.sub(r"^#+\s*", "", first_line[0]).strip()
                if "not submitted" in heading.lower():
                    continue
                parts = [part.strip() for part in re.split(r"\s+[—–]\s+", heading)]
                if len(parts) < 2:
                    continue
                company, title = parts[0], parts[1]
                status = "submitted" if path.name == "SUBMITTED.md" else "started"
                records.append(EvidenceRecord(
                    company=company,
                    title=title,
                    status=status,
                    source="employment",
                ))
        except Exception as exc:
            errors.append(f"application packet folder could not be read ({exc})")

    @staticmethod
    def _add_gmail_records(
        path: Path,
        records: list[EvidenceRecord],
        errors: list[str],
        max_age_hours: int,
    ) -> None:
        if not path.is_file():
            errors.append(f"Gmail application cache is unavailable: {path}")
            return
        try:
            payload = json.loads(path.read_text())
            if max_age_hours > 0:
                generated_at = str(payload.get("generated_at", "")).strip()
                if not generated_at:
                    errors.append(
                        "Gmail application cache has no generated_at timestamp: "
                        f"{path}"
                    )
                    return
                generated = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
                now = datetime.now().astimezone()
                if generated.tzinfo is None:
                    generated = generated.astimezone()
                age_hours = (now - generated).total_seconds() / 3600
                if age_hours > max_age_hours:
                    errors.append(
                        f"Gmail application cache is {age_hours:.1f} hours old; "
                        f"refresh it before recommending jobs: {path}"
                    )
                    return
            for item in payload.get("records", []):
                if (
                    not isinstance(item, dict)
                    or not item.get("company")
                    or not item.get("title")
                ):
                    continue
                records.append(EvidenceRecord(
                    company=str(item["company"]),
                    title=str(item["title"]),
                    url=str(item.get("url", "")),
                    status=str(item.get("status", "applied")),
                    source="gmail",
                ))
        except Exception as exc:
            errors.append(f"Gmail application cache could not be read ({exc})")

    def ensure_usable(self) -> None:
        if self.errors:
            detail = "; ".join(self.errors)
            raise EvidenceSourceError(
                "application evidence is incomplete, so JobPilot stopped before "
                "recommending jobs. Restore or update the configured source, then "
                f"run `jobpilot jobs` again: {detail}"
            )

    def match(self, company: str, title: str, url: str) -> Optional[EvidenceRecord]:
        url_n = _norm_url(url)
        if url_n:
            for record in self.records:
                if record.url and _norm_url(record.url) == url_n:
                    return record

        company_n = _norm_text(company)
        if not company_n:
            return None
        for record in self.records:
            same_company = _norm_text(record.company) == company_n
            if same_company and _same_role(record.title, title):
                return record
        return None
