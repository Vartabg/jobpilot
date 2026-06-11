"""Atomic file writes and exclusive locks for JSON/markdown state."""

from __future__ import annotations

import fcntl
import os
import tempfile
from pathlib import Path

from jobpilot.gigs.core.paths import data_dir

# Locks live in the repo's data dir, NOT next to the locked file. A sibling
# lock (e.g. pipeline.md.lock in iCloud) would sync to every device and
# show up in the user's Files app; an flock is only meaningful on this
# machine anyway.
LOCKS_DIR = data_dir() / "locks"


def atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


class file_lock:
    """Exclusive advisory lock on `data/locks/<target filename>.lock`."""

    def __init__(self, target: Path) -> None:
        self._lock_path = LOCKS_DIR / f"{target.name}.lock"

    def __enter__(self) -> file_lock:
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(self._lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(self._fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        fcntl.flock(self._fd, fcntl.LOCK_UN)
        os.close(self._fd)