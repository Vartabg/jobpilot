"""
Pilot diary — live, plain-English trail of what the job-search helper is doing.

One active session at a time under data/pilot-diary/:
  current.md     ← symlink / path to the open session (easy to tail)
  YYYY-MM-DD-HHMMSS.md

Design:
- Human-readable markdown (not JSON jargon)
- Auto notes when key jobpilot commands run during an open session
- Explicit notes via `jobpilot note "..."`
- Watch via `jobpilot diary` / `jobpilot watch`
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from jobpilot.core.config import DATA_DIR
from jobpilot.core.logger import get_logger

log = get_logger(__name__)

DIARY_DIR = DATA_DIR / "pilot-diary"
CURRENT_LINK = DIARY_DIR / "current.md"
ACTIVE_META = DIARY_DIR / ".active"


def diary_dir() -> Path:
    DIARY_DIR.mkdir(parents=True, exist_ok=True)
    return DIARY_DIR


def active_path() -> Path | None:
    """Return the open diary file, or None if no session."""
    if CURRENT_LINK.is_symlink() or CURRENT_LINK.is_file():
        try:
            target = CURRENT_LINK.resolve(strict=False)
            if target.is_file():
                return target
        except OSError as exc:
            log.debug("[PilotDiary] Active link resolution failed → %s", exc)
    if ACTIVE_META.is_file():
        raw = ACTIVE_META.read_text(encoding="utf-8").strip()
        if raw:
            path = Path(raw)
            if path.is_file():
                return path
    return None


def is_active() -> bool:
    return active_path() is not None


def start_session(*, label: str = "local pilot") -> Path:
    """Open a new diary session and make it current."""
    diary_dir()
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    path = DIARY_DIR / f"{stamp}.md"
    header = (
        f"# Job search helper diary\n\n"
        f"- Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"- Mode: {label}\n"
        f"- Runtime: {label}\n\n"
        f"## Live trail\n\n"
        f"_This file updates as the helper works. "
        f"Another terminal: `jobpilot diary` or `jobpilot watch`._\n\n"
    )
    path.write_text(header, encoding="utf-8")
    ACTIVE_META.write_text(str(path), encoding="utf-8")
    _point_current(path)
    append("session", f"Started ({label})")
    return path


def end_session(*, summary: str = "") -> Path | None:
    path = active_path()
    if not path:
        return None
    if summary:
        append("done", summary)
    else:
        append("done", "Session closed")
    try:
        if CURRENT_LINK.is_symlink() or CURRENT_LINK.exists():
            CURRENT_LINK.unlink()
    except OSError as exc:
        log.debug("[PilotDiary] Current-link cleanup failed → %s", exc)
    try:
        ACTIVE_META.unlink(missing_ok=True)
    except OSError as exc:
        log.debug("[PilotDiary] Active metadata cleanup failed → %s", exc)
    return path


def _point_current(path: Path) -> None:
    """Point current.md at the session file (symlink when possible)."""
    try:
        if CURRENT_LINK.is_symlink() or CURRENT_LINK.exists():
            CURRENT_LINK.unlink()
        CURRENT_LINK.symlink_to(path.name)  # relative inside diary dir
    except OSError:
        # Fallback: copy path text so tail still works on something stable
        try:
            CURRENT_LINK.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        except OSError as exc:
            log.debug("[PilotDiary] Current-file fallback failed → %s", exc)


def append(kind: str, message: str) -> Path | None:
    """Append one plain-English line to the active diary.

    kind: short tag (session | jobs | note | apply | score | warn | done | tool)
    """
    path = active_path()
    if path is None:
        return None
    msg = " ".join((message or "").strip().split())
    if not msg:
        return path
    tag = (kind or "note").strip().lower()[:16]
    line = f"- **{datetime.now().strftime('%H:%M:%S')}** · {tag} · {msg}\n"
    try:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        return None
    # If current.md is a plain file fallback, refresh it
    if CURRENT_LINK.is_file() and not CURRENT_LINK.is_symlink():
        try:
            CURRENT_LINK.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        except OSError as exc:
            log.debug("[PilotDiary] Current-file refresh failed → %s", exc)
    return path


def auto(kind: str, message: str) -> None:
    """Best-effort auto log (never raises)."""
    try:
        if is_active():
            append(kind, message)
    except Exception as exc:
        log.debug("[PilotDiary] Automatic entry skipped → %s", exc)


def read_tail(lines: int = 40) -> str:
    path = active_path()
    if not path:
        # Fall back to newest diary file
        files = sorted(diary_dir().glob("20*.md"), reverse=True)
        if not files:
            return "(no diary yet — start with `jobpilot`)"
        path = files[0]
        header = "(last session — not live)\n\n"
    else:
        header = f"(live · {path.name})\n\n"
    text = path.read_text(encoding="utf-8")
    parts = text.splitlines()
    body = text if len(parts) <= lines else "\n".join(parts[-lines:]) + "\n"
    return header + body


def watch_loop(poll_s: float = 1.0) -> None:
    """Print new diary lines until Ctrl+C (for a second terminal)."""
    path = active_path()
    if not path:
        print("No live session. Start the helper with: jobpilot")
        return
    print(f"Watching: {path}")
    print("(Ctrl+C to stop)\n")
    # Print existing content first
    print(path.read_text(encoding="utf-8"), end="")
    pos = path.stat().st_size
    try:
        while True:
            try:
                size = path.stat().st_size
            except OSError:
                time.sleep(poll_s)
                continue
            if size > pos:
                with path.open("r", encoding="utf-8") as fh:
                    fh.seek(pos)
                    chunk = fh.read()
                    print(chunk, end="", flush=True)
                    pos = fh.tell()
            elif size < pos:
                # file truncated / new session
                pos = 0
            time.sleep(poll_s)
    except KeyboardInterrupt:
        print("\n(stopped watching)")
