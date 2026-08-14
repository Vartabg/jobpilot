"""Validation and atomic import for read-only Gmail application exports.

JobPilot does not authenticate to Gmail or infer applications from arbitrary
mail. A separate read-only Gmail client may export the compact cache schema
consumed here; this module validates that export and atomically publishes it.
The export's own ``generated_at`` is preserved as the freshness authority.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from jobpilot.core.role_identity import (
    canonicalize_role_url,
    explicit_role_identity_key,
    is_safe_public_role_url,
)

GMAIL_CACHE_STATUSES = frozenset(
    {
        "abandoned",
        "applied",
        "human_reply",
        "interview",
        "no_response",
        "offer",
        "outreach",
        "rejected",
        "screen",
        "skipped",
        "started",
        "submitted",
        "withdrawn",
    }
)
GMAIL_CACHE_RECORD_FIELDS = frozenset({
    "company",
    "title",
    "status",
    "url",
    "occurred_at",
    "date",
    "applied_at",
    "received_at",
    "message_id",
})
_EVENT_TIME_FIELDS = ("occurred_at", "date", "applied_at", "received_at")

_CACHE_LOCKS_GUARD = threading.Lock()
_CACHE_THREAD_LOCKS: dict[Path, threading.RLock] = {}


class GmailCacheError(RuntimeError):
    """Raised when a Gmail export cannot safely become application evidence."""


@dataclass(frozen=True)
class GmailCacheSnapshot:
    generated_at: str
    generated_instant: datetime
    records: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class GmailCacheSyncResult:
    destination: Path
    generated_at: str
    record_count: int


def _parse_generated_at(value: object, path: Path) -> tuple[str, datetime]:
    raw = str(value or "").strip()
    if not raw:
        raise GmailCacheError(
            f"Gmail application cache has no generated_at timestamp: {path}"
        )
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        normalized = parsed.astimezone(UTC) if parsed.tzinfo is not None else parsed
    except (OverflowError, ValueError) as exc:
        raise GmailCacheError(
            f"Gmail application cache has an invalid generated_at timestamp: {path}"
        ) from exc
    if parsed.tzinfo is None:
        raise GmailCacheError(
            f"Gmail application cache generated_at must include a timezone: {path}"
        )
    return raw, normalized


def _validate_event_time(value: object, field: str, index: int, path: Path) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            date.fromisoformat(raw)
        else:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("timezone required")
            parsed.astimezone(UTC)
    except (OverflowError, ValueError) as exc:
        raise GmailCacheError(
            f"Gmail application cache record {index} has an invalid {field}: {path}"
        ) from exc
    return raw


def _validate_record(item: object, index: int, path: Path) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise GmailCacheError(
            f"Gmail application cache record {index} is not an object: {path}"
        )
    for key, value in item.items():
        if not isinstance(key, str) or isinstance(value, (dict, list)):
            raise GmailCacheError(
                f"Gmail application cache record {index} must contain only flat "
                f"JSON fields: {path}"
            )
    if set(item) - GMAIL_CACHE_RECORD_FIELDS:
        raise GmailCacheError(
            f"Gmail application cache record {index} has unsupported fields; "
            f"message content is not accepted: {path}"
        )
    company = str(item.get("company") or "").strip()
    title = str(item.get("title") or "").strip()
    if not company or not title:
        raise GmailCacheError(
            f"Gmail application cache record {index} needs company and title: {path}"
        )
    status = str(item.get("status") or "applied").strip().lower()
    if status not in GMAIL_CACHE_STATUSES:
        raise GmailCacheError(
            f"Gmail application cache record {index} has an unsupported status: "
            f"{path}"
        )
    url = str(item.get("url") or "").strip()
    if url and not is_safe_public_role_url(url):
        raise GmailCacheError(
            f"Gmail application cache record {index} has an unsafe role URL: {path}"
        )
    sanitized: dict[str, Any] = {
        "company": company,
        "title": title,
        "status": status,
        "url": url,
    }
    for field in _EVENT_TIME_FIELDS:
        if value := _validate_event_time(item.get(field), field, index, path):
            sanitized[field] = value
    if item.get("message_id") is not None:
        message_id = str(item["message_id"]).strip()
        if (
            not message_id
            or len(message_id) > 512
            or any(
                ord(character) < 32 or ord(character) == 127
                for character in message_id
            )
        ):
            raise GmailCacheError(
                f"Gmail application cache record {index} has an invalid message_id: "
                f"{path}"
            )
        sanitized["message_id"] = message_id
    try:
        json.dumps(sanitized, ensure_ascii=False).encode("utf-8")
    except UnicodeError as exc:
        raise GmailCacheError(
            f"Gmail application cache record {index} contains invalid Unicode: {path}"
        ) from exc
    return sanitized


def _fallback_record_identity(item: dict[str, Any]) -> str:
    def normalize(value: object) -> str:
        return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()

    return "role:" + ":".join((
        normalize(item.get("company")),
        normalize(item.get("title")),
    ))


def _precise_record_identity(item: dict[str, Any]) -> str:
    url = str(item.get("url") or "").strip()
    provider_key = explicit_role_identity_key(url)
    if provider_key:
        return f"provider:{provider_key}"
    canonical_url = canonicalize_role_url(url)
    return f"url:{canonical_url}" if canonical_url else ""


def _exported_identity_keys(item: dict[str, Any]) -> set[str]:
    keys = {_fallback_record_identity(item)}
    if precise := _precise_record_identity(item):
        keys.add(precise)
    return keys


def _required_existing_identity(item: dict[str, Any]) -> str:
    return _precise_record_identity(item) or _fallback_record_identity(item)


def load_gmail_application_cache(
    path: Path,
    *,
    max_age_hours: int = 0,
    now: datetime | None = None,
) -> GmailCacheSnapshot:
    """Read and strictly validate one cache without modifying it."""
    source = Path(path)
    if not source.is_file():
        raise GmailCacheError(f"Gmail application cache is unavailable: {source}")
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, RecursionError, UnicodeError, ValueError) as exc:
        raise GmailCacheError(
            f"Gmail application cache could not be read ({exc}): {source}"
        ) from exc
    if not isinstance(payload, dict):
        raise GmailCacheError(
            f"Gmail application cache must be a JSON object: {source}"
        )
    raw_generated, generated = _parse_generated_at(payload.get("generated_at"), source)
    try:
        instant = (now or datetime.now(UTC)).astimezone(UTC)
        future_cutoff = instant + timedelta(minutes=5)
        age_hours = (instant - generated).total_seconds() / 3600
    except (OverflowError, ValueError) as exc:
        raise GmailCacheError(
            f"Gmail application cache timestamp is outside supported bounds: {source}"
        ) from exc
    if generated > future_cutoff:
        raise GmailCacheError(
            f"Gmail application cache generated_at is in the future: {source}"
        )
    if max_age_hours > 0:
        if age_hours > max_age_hours:
            raise GmailCacheError(
                f"Gmail application cache is {age_hours:.1f} hours old; "
                f"refresh it before recommending jobs: {source}"
            )
    raw_records = payload.get("records")
    if not isinstance(raw_records, list):
        raise GmailCacheError(
            f"Gmail application cache records must be a JSON list: {source}"
        )
    records = tuple(
        _validate_record(item, index, source)
        for index, item in enumerate(raw_records)
    )
    return GmailCacheSnapshot(raw_generated, generated, records)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        directory_fd = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(directory_fd)
    except OSError:
        pass
    finally:
        os.close(directory_fd)


@contextmanager
def _cache_transaction(destination: Path) -> Iterator[None]:
    """Serialize one cache replacement across threads and local processes."""
    try:
        lock_key = destination.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise GmailCacheError(
            f"Gmail cache destination could not be resolved safely: {exc}"
        ) from exc
    with _CACHE_LOCKS_GUARD:
        thread_lock = _CACHE_THREAD_LOCKS.setdefault(lock_key, threading.RLock())
    with thread_lock:
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            lock_path = destination.with_name(f".{destination.name}.lock")
            flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
            lock_fd = os.open(lock_path, flags, 0o600)
            os.fchmod(lock_fd, 0o600)
        except OSError as exc:
            raise GmailCacheError(
                f"Gmail cache transaction lock could not be acquired: {exc}"
            ) from exc
        try:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
            except OSError as exc:
                raise GmailCacheError(
                    f"Gmail cache transaction lock could not be acquired: {exc}"
                ) from exc
            yield
        finally:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(lock_fd)


def sync_gmail_application_cache(
    source_path: Path,
    destination_path: Path,
    *,
    max_age_hours: int = 0,
    now: datetime | None = None,
) -> GmailCacheSyncResult:
    """Validate an external read-only export and atomically replace the cache."""
    source = Path(source_path).expanduser()
    destination = Path(destination_path).expanduser()
    try:
        same_file = source.resolve() == destination.resolve()
    except (OSError, RuntimeError):
        same_file = source.absolute() == destination.absolute()
    if same_file:
        raise GmailCacheError("Gmail export and cache destination must be different files.")
    with _cache_transaction(destination):
        return _sync_gmail_application_cache_locked(
            source,
            destination,
            max_age_hours=max_age_hours,
            now=now,
        )


def _sync_gmail_application_cache_locked(
    source: Path,
    destination: Path,
    *,
    max_age_hours: int,
    now: datetime | None,
) -> GmailCacheSyncResult:
    """Complete validation and replacement while holding the destination lock."""
    if destination.is_symlink():
        raise GmailCacheError(
            f"Refusing to replace a symlinked Gmail cache destination: {destination}"
        )

    try:
        before = source.stat() if source.is_file() else None
    except OSError as exc:
        raise GmailCacheError(f"Gmail export could not be inspected: {exc}") from exc
    snapshot = load_gmail_application_cache(
        source,
        max_age_hours=max_age_hours,
        now=now,
    )
    try:
        after = source.stat()
    except OSError as exc:
        raise GmailCacheError(f"Gmail export could not be inspected: {exc}") from exc
    if before is None or (
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ) != (
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise GmailCacheError("Gmail export changed while it was being validated; retry.")

    if destination.is_file():
        try:
            current = load_gmail_application_cache(destination)
        except GmailCacheError:
            current = None
        if current is not None and current.generated_instant > snapshot.generated_instant:
            raise GmailCacheError(
                "Refusing to replace the Gmail cache with an older export."
            )
        if current is not None:
            exported_keys = set().union(*(
                _exported_identity_keys(item) for item in snapshot.records
            )) if snapshot.records else set()
            missing_count = sum(
                _required_existing_identity(item) not in exported_keys
                for item in current.records
            )
            if missing_count:
                raise GmailCacheError(
                    "Refusing an incomplete Gmail export that omits "
                    f"{missing_count} existing application role(s)."
                )

    content = {
        "generated_at": snapshot.generated_at,
        "records": list(snapshot.records),
    }
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            json.dump(content, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temp_path.chmod(0o600)
        os.replace(temp_path, destination)
        _fsync_directory(destination.parent)
    except BaseException as exc:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        if isinstance(exc, (OSError, UnicodeError)):
            raise GmailCacheError(
                f"Gmail cache could not be atomically replaced: {exc}"
            ) from exc
        raise
    return GmailCacheSyncResult(
        destination=destination,
        generated_at=snapshot.generated_at,
        record_count=len(snapshot.records),
    )
