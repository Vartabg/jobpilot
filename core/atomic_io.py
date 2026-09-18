"""Atomic file writes for core state (queue.json, profile.json, etc.).

Mirrors `jobpilot.gigs.core.io_lock.atomic_write_text` so the core lane gets the
same crash-safety the gigs lane already has: write to a temp file in the same
directory, fsync, then `os.replace` (atomic on POSIX). A crash mid-write can
never leave a truncated queue.json/profile.json behind.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


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
