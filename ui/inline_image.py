"""Render images inline in the terminal.

iTerm2 (and a few others) accept the imgcat / OSC 1337 inline-image protocol,
which draws real graphics *inside* the terminal — not ASCII art. This module
detects support and emits the escape sequence, with a clean no-op fallback so
callers can branch to text when graphics aren't available.
"""

from __future__ import annotations

import base64
import os
import sys
from typing import Optional, TextIO

# OSC 1337 control bytes.
_ESC = "\033"
_BEL = "\007"


def supports_inline_images(stream: Optional[TextIO] = None) -> bool:
    """True when the current terminal can render imgcat inline images.

    Honors JOBPILOT_FORCE_INLINE_IMAGES=1/0 as an override (handy for tests and
    for piping into iTerm from a wrapper). Otherwise requires a TTY and an
    iTerm2-class terminal.
    """
    forced = os.environ.get("JOBPILOT_FORCE_INLINE_IMAGES")
    if forced is not None:
        return forced.strip() not in ("", "0", "false", "no")

    out = stream or sys.stdout
    try:
        if not out.isatty():
            return False
    except Exception:
        return False

    if os.environ.get("TERM_PROGRAM") == "iTerm.app":
        return True
    if os.environ.get("LC_TERMINAL", "").lower() == "iterm2":
        return True
    return False


def format_inline_image(
    data: bytes,
    *,
    name: str = "image.png",
    width: Optional[str] = None,
    height: Optional[str] = None,
    preserve_aspect_ratio: bool = True,
) -> str:
    """Build the OSC 1337 inline-image string for `data` (raw image bytes).

    `width`/`height` use iTerm's units: "auto", an integer = character cells,
    "Npx" = pixels, or "N%" = percent of the session. Wrapped for tmux
    passthrough when running inside tmux so the sequence reaches the outer term.
    """
    b64 = base64.b64encode(data).decode("ascii")
    args = [
        f"name={base64.b64encode(name.encode()).decode('ascii')}",
        f"size={len(data)}",
        "inline=1",
        f"preserveAspectRatio={1 if preserve_aspect_ratio else 0}",
    ]
    if width:
        args.append(f"width={width}")
    if height:
        args.append(f"height={height}")
    seq = f"{_ESC}]1337;File={';'.join(args)}:{b64}{_BEL}"

    if os.environ.get("TMUX"):
        # tmux swallows OSC unless wrapped in its DCS passthrough, with inner
        # ESC bytes doubled.
        inner = seq.replace(_ESC, _ESC + _ESC)
        seq = f"{_ESC}Ptmux;{inner}{_ESC}\\"
    return seq


def print_inline_image(
    data: bytes,
    *,
    name: str = "image.png",
    width: Optional[str] = None,
    height: Optional[str] = None,
    stream: Optional[TextIO] = None,
) -> None:
    """Write an inline image to the terminal, followed by a newline."""
    out = stream or sys.stdout
    out.write(format_inline_image(data, name=name, width=width, height=height))
    out.write("\n")
    out.flush()
