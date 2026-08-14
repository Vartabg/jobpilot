"""Read-only application evidence merged from JobPilot's trusted sources."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from jobpilot.core.gmail_application_cache import (
    GmailCacheError,
    load_gmail_application_cache,
)
from jobpilot.core.role_identity import (
    canonicalize_role_url,
    explicit_role_identity_key,
    identify_role,
)


class EvidenceSourceError(RuntimeError):
    """Raised when a configured application-evidence source is unavailable."""


def _gmail_error_with_guidance(exc: GmailCacheError) -> str:
    return (
        f"{exc}. Import a fresh full-history read-only export with "
        "`jobpilot gmail-sync /path/to/gmail-export.json`."
    )


@dataclass(frozen=True)
class EvidenceRecord:
    company: str
    title: str
    url: str = ""
    status: str = "started"
    source: str = "tracker"
    occurred_at: str = ""
    provenance: dict[str, str] = field(default_factory=dict)


def _norm_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _norm_url(value: str) -> str:
    return canonicalize_role_url(value)


_TRAILING_LEGAL_SUFFIXES = {
    "inc", "incorporated", "corp", "corporation", "llc", "llp", "ltd", "limited",
    "co", "company", "lp", "plc", "gmbh", "ag", "sa", "bv",
}


def _norm_company(value: str) -> str:
    """Normalize a company name, dropping trailing corporate suffixes so that
    'Crown Equipment' matches 'Crown Equipment Corporation'."""
    base = _norm_text(value)
    tokens = base.split()
    while len(tokens) > 1 and tokens[-1] in _TRAILING_LEGAL_SUFFIXES:
        tokens.pop()
    return " ".join(tokens) or base


def _same_role(left: str, right: str) -> bool:
    left_n = _norm_text(left)
    right_n = _norm_text(right)
    if not left_n or not right_n:
        return False
    return left_n == right_n


class ApplicationEvidenceIndex:
    """Merged application evidence with conservative company + role matching."""

    def __init__(
        self,
        records: list[EvidenceRecord],
        errors: list[str] | None = None,
    ):
        self.records = records
        self.errors = errors or []

    @classmethod
    def build(
        cls,
        *,
        tracker,
        employment_dir: Path | None = None,
        gmail_cache_path: Path | None = None,
        gmail_cache_max_age_hours: int = 0,
    ) -> ApplicationEvidenceIndex:
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
                    occurred_at=item.applied_at,
                    provenance={"tracker_source": getattr(item, "source", "legacy")},
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
                parts = [
                    part.strip()
                    for part in re.split(r"\s+[\u2014\u2013]\s+", heading)
                ]
                if len(parts) < 2:
                    continue
                company, title = parts[0], parts[1]
                status = "submitted" if path.name == "SUBMITTED.md" else "started"
                date_match = re.search(r"\d{4}-\d{2}-\d{2}", path.parent.name)
                records.append(EvidenceRecord(
                    company=company,
                    title=title,
                    status=status,
                    source="employment",
                    occurred_at=date_match.group(0) if date_match else "",
                    provenance={"packet": str(path.relative_to(root))},
                ))
        except Exception as exc:
            errors.append(f"application packet folder could not be read ({exc})")

    @staticmethod
    def _add_gmail_records(
        path: Path,
        records: list[EvidenceRecord],
        errors: list[str],
        max_age_hours: int,
        now: datetime | None = None,
    ) -> None:
        try:
            snapshot = load_gmail_application_cache(
                path,
                max_age_hours=max_age_hours,
                now=now,
            )
            for item in snapshot.records:
                records.append(EvidenceRecord(
                    company=str(item["company"]),
                    title=str(item["title"]),
                    url=str(item.get("url", "")),
                    status=str(item.get("status", "applied")),
                    source="gmail",
                    occurred_at=str(
                        item.get("occurred_at") or item.get("date")
                        or item.get("applied_at") or item.get("received_at") or ""
                    ),
                    provenance=(
                        {"message_id": str(item["message_id"])}
                        if item.get("message_id") else {}
                    ),
                ))
        except GmailCacheError as exc:
            errors.append(_gmail_error_with_guidance(exc))

    def ensure_usable(self) -> None:
        if self.errors:
            detail = "; ".join(self.errors)
            raise EvidenceSourceError(
                "application evidence is incomplete, so JobPilot stopped before "
                "recommending jobs. Restore or update the configured source, then "
                f"run `jobpilot queue --refresh` again: {detail}"
            )

    def match(self, company: str, title: str, url: str) -> EvidenceRecord | None:
        url_n = _norm_url(url)
        if url_n:
            for record in self.records:
                if record.url and _norm_url(record.url) == url_n:
                    return record

        identity_key = explicit_role_identity_key(url)
        if identity_key:
            for record in self.records:
                if (
                    record.url
                    and explicit_role_identity_key(record.url) == identity_key
                ):
                    return record

        company_n = _norm_company(company)
        if not company_n:
            return None
        for record in self.records:
            # A different explicit requisition ID is a sibling role even when
            # the employer reused the same title. URL-less/manual evidence can
            # still use the conservative exact company+title fallback below.
            record_identity_key = explicit_role_identity_key(record.url)
            if identity_key and record_identity_key:
                continue
            same_company = _norm_company(record.company) == company_n
            if same_company and _same_role(record.title, title):
                return record
        return None

    def import_into(self, ledger) -> int:
        """Import all evidence idempotently without resolving source conflicts."""
        status_events = {
            "started": "discovered", "submitted": "applied", "abandoned": "withdrawn",
            "skipped": "discovered",
        }
        before = ledger.table_count("events")
        for record in self.records:
            event_type = status_events.get(record.status, record.status)
            if event_type not in {
                "discovered", "verified", "applied", "outreach", "human_reply",
                "screen", "interview", "offer", "rejected", "withdrawn", "no_response",
            }:
                continue
            identity = identify_role(record.url)
            explicit_identity = (
                identity.key
                if identity.provider and identity.provider_job_id
                else ""
            )
            opportunity_id = ledger.upsert_opportunity(
                record.company,
                record.title,
                canonical_url=identity.canonical_url,
                provider=identity.provider,
                provider_job_id=identity.provider_job_id,
                identity_key=explicit_identity,
            )
            provenance = {
                **record.provenance,
                "original_status": record.status,
                "evidence_source": record.source,
            }
            ledger.append_event(
                opportunity_id, event_type, record.occurred_at,
                record.source, provenance,
                idempotency_key=json.dumps({
                    "company": _norm_company(record.company),
                    "title": _norm_text(record.title),
                    "url": _norm_url(record.url),
                    "event_type": event_type,
                    "occurred_at": record.occurred_at,
                    "source": record.source,
                    "provenance": provenance,
                }, sort_keys=True),
            )
        return ledger.table_count("events") - before


def configured_external_evidence_errors(
    *,
    employment_dir: Path | None = None,
    gmail_cache_path: Path | None = None,
    gmail_cache_max_age_hours: int = 0,
    now: datetime | None = None,
) -> tuple[str, ...]:
    """Check configured external evidence without opening tracker or ledger state."""
    records: list[EvidenceRecord] = []
    errors: list[str] = []
    if employment_dir is not None:
        ApplicationEvidenceIndex._add_employment_records(
            Path(employment_dir),
            records,
            errors,
        )
    if gmail_cache_path is not None:
        ApplicationEvidenceIndex._add_gmail_records(
            Path(gmail_cache_path),
            records,
            errors,
            gmail_cache_max_age_hours,
            now,
        )
    return tuple(errors)
