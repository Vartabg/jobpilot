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


def read_key(timeout: float = 0.12) -> Optional[str]:
    """Read one key if available within timeout seconds."""
    if not sys.stdin.isatty():
        return None
    ready, _, _ = select.select([sys.stdin], [], [], timeout)
    if not ready:
        return None
    ch = sys.stdin.read(1)
    if ch == "\x1b":
        ready2, _, _ = select.select([sys.stdin], [], [], 0.02)
        if ready2:
            ch2 = sys.stdin.read(1)
            if ch2 == "[":
                ch3 = sys.stdin.read(1)
                return {"A": "up", "B": "down", "C": "right", "D": "left"}.get(ch3, ch + ch2 + ch3)
        return "esc"
    if ch == "\x7f":
        return "backspace"
    if ch in ("\r", "\n"):
        return "enter"
    return ch


def osc8_link(url: str, label: str) -> str:
    """OSC-8 hyperlink markup for Rich (iTerm2, WezTerm, Kitty, Ghostty)."""
    safe_url = (url or "").replace("\\", "\\\\").replace("]", "\\]")
    return f"[link={safe_url}]{label}[/link]"