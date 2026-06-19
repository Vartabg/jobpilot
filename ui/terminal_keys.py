"""Non-blocking single-key input for interactive terminal UIs (macOS/Linux)."""

from __future__ import annotations

import select
import sys
import termios
import tty
from contextlib import contextmanager
from typing import Iterator, Optional


@contextmanager
def raw_stdin() -> Iterator[None]:
    """Put stdin in cbreak mode; restore on exit."""
    if not sys.stdin.isatty():
        yield
        return
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


_ARROW_FINALS = {"A": "up", "B": "down", "C": "right", "D": "left"}


def _read_escape_sequence(timeout: float = 0.02) -> Optional[str]:
    """Parse CSI (\\e[…) or SS3 (\\eO…) arrow sequences from iTerm/macOS."""
    ready, _, _ = select.select([sys.stdin], [], [], timeout)
    if not ready:
        return "esc"
    lead = sys.stdin.read(1)
    if lead == "O":
        ready2, _, _ = select.select([sys.stdin], [], [], timeout)
        if not ready2:
            return "esc"
        final = sys.stdin.read(1)
        return _ARROW_FINALS.get(final, "esc")
    if lead != "[":
        return "esc"
    # CSI: read until final byte A–Z (handles \\e[B and \\e[1;2B)
    parts: list[str] = []
    for _ in range(12):
        ready2, _, _ = select.select([sys.stdin], [], [], timeout)
        if not ready2:
            break
        final = sys.stdin.read(1)
        if final in _ARROW_FINALS:
            return _ARROW_FINALS[final]
        parts.append(final)
    return "esc"


def read_key(timeout: float = 0.12) -> Optional[str]:
    """Read one key if available within timeout seconds."""
    if not sys.stdin.isatty():
        return None
    ready, _, _ = select.select([sys.stdin], [], [], timeout)
    if not ready:
        return None
    ch = sys.stdin.read(1)
    if ch == "\x1b":
        return _read_escape_sequence()
    if ch == "\x7f":
        return "backspace"
    if ch in ("\r", "\n"):
        return "enter"
    return ch


def osc8_link(url: str, label: str) -> str:
    """OSC-8 hyperlink markup for Rich (iTerm2, WezTerm, Kitty, Ghostty)."""
    safe_url = (url or "").replace("\\", "\\\\").replace("]", "\\]")
    return f"[link={safe_url}]{label}[/link]"