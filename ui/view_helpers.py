"""Shared helpers for terminal views (board, HUD, radar)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from rich.text import Text

from jobpilot.core.config import DATA_DIR, DEFAULT_SERVE_PORT

ANSWERS_DIR = DATA_DIR / "answers"


def score_bar(score: int, width: int = 10) -> Text:
    """Render a 0-100 score as a colored block bar."""
    score = max(0, min(100, int(score or 0)))
    filled = round(score / 100 * width)
    bar = Text()
    for i in range(width):
        if i < filled:
            if score >= 75:
                bar.append("█", style="bold green")
            elif score >= 55:
                bar.append("█", style="cyan")
            elif score >= 40:
                bar.append("█", style="yellow")
            else:
                bar.append("█", style="dim")
        else:
            bar.append("░", style="dim")
    bar.append(f" {score}", style="bold")
    return bar


def company_slug(company: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (company or "").strip().lower()).strip("-")


def materials_ready(company: str) -> Optional[Path]:
    slug = company_slug(company)
    if not slug:
        return None
    company_dir = ANSWERS_DIR / slug
    if not company_dir.is_dir():
        compact = slug.replace("-", "")
        alt = ANSWERS_DIR / compact
        company_dir = alt if alt.is_dir() else company_dir
    if not company_dir.is_dir():
        return None
    paste = company_dir / "PASTE_SHEET.txt"
    if paste.exists():
        return paste
    for candidate in sorted(company_dir.glob("*.md")):
        return candidate
    return None


def is_senior_title(title: str) -> bool:
    t = (title or "").lower()
    return "senior" in t and not any(
        x in t for x in ("manager", "principal", "staff", "director", "vp")
    )


def check_dashboard(port: int = DEFAULT_SERVE_PORT) -> bool:
    try:
        import urllib.request

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/queue", timeout=1.5) as resp:
            return resp.status == 200
    except Exception:
        return False


def check_chrome(port: int = 9222) -> bool:
    try:
        import urllib.request

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1.5):
            return True
    except Exception:
        return False