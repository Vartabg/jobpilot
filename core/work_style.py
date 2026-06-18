"""Work-style signals — autonomy, async, contract, anti-9-5.

Shared by the jobs lane (queue + job_scorer) and gigs lane (scorer).
Reads optional loved/hated tokens from ``data/psyche_profile.json`` when
present; the keyword lists below are the neutral shipped defaults.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from jobpilot.core.config import DATA_DIR

PSYCHE_PATH = DATA_DIR / "psyche_profile.json"

CONTRACT_GREEN = (
    "contract", "1099", "freelance", "consultant", "hourly", "fractional",
    "project-based", "project based", "c2c", "corp-to-corp", "independent contractor",
    "contractor", "part-time", "part time", "retainer", "sow", "statement of work",
)
W2_ONLY_RED = (
    "full-time employee only", "full time employee only", "w2 only", "w-2 only",
    "benefits eligible", "full time w2", "salaried full-time", "salaried full time",
    "fte only", "permanent employee",
)
AUTONOMY_GREEN = (
    "async", "asynchronous", "flexible hours", "flexible schedule",
    "own your schedule", "results-oriented", "results oriented", "milestone",
    "project deadline", "self-directed", "self directed", "minimal meetings",
    "no micromanagement", "outcome-based", "outcome based", "work autonomously",
    "set your own hours", "choose your hours", "deadline-driven", "deliverable",
)
SCHEDULE_RED = (
    "9-5", "9 to 5", "nine to five", "core hours", "daily standup", "daily stand-up",
    "daily standups", "synchronous", "overlap hours", "hours of overlap",
    "must be online", "in-office 5 days", "on-site 5 days", "on site 5 days",
    "five days in office", "5 days in office", "fixed schedule", "shift work",
    "punch in", "clock in", "attendance policy", "time tracking required",
)
URGENCY_GREEN = (
    "start immediately", "asap", "as soon as possible", "urgent", "2-week",
    "two week", "short sprint", "start next week", "immediate start",
)
JACK_OF_ALL_TRADES_GREEN = (
    "implementation", "integration", "deployment", "solutions", "generalist",
    "technical support", "field service", "customer engineer", "customer-facing",
    "wear many hats", "multi-disciplinary", "cross-functional builder",
)


def _haystack(text: str, *, title: str = "") -> str:
    return f"{(title or '').lower()} {(text or '').lower()}"


def _hits(haystack: str, phrases: tuple[str, ...]) -> list[str]:
    return [p for p in phrases if p in haystack]


@lru_cache(maxsize=1)
def _psyche_tokens() -> tuple[list[str], list[str]]:
    try:
        raw = json.loads(PSYCHE_PATH.read_text())
    except Exception:
        return [], []
    loved = raw.get("loved_signals", {}) or {}
    hated = raw.get("hated_signals", {}) or {}
    loved_title = list(loved.get("title") or [])
    hated_title = list(hated.get("title") or [])
    loved_note = list(loved.get("company_or_note") or [])
    hated_note = list(hated.get("company_or_note") or [])
    return loved_title + loved_note, hated_title + hated_note


def is_contract_friendly(text: str, *, title: str = "") -> bool:
    hay = _haystack(text, title=title)
    return bool(_hits(hay, CONTRACT_GREEN))


def is_w2_only(text: str, *, title: str = "") -> bool:
    hay = _haystack(text, title=title)
    return bool(_hits(hay, W2_ONLY_RED))


def is_schedule_rigid(text: str, *, title: str = "") -> bool:
    hay = _haystack(text, title=title)
    return bool(_hits(hay, SCHEDULE_RED))


def score_work_style(text: str, *, title: str = "") -> tuple[int, list[str]]:
    """Return a -35..+25 adjustment and human-readable reason tokens."""
    hay = _haystack(text, title=title)
    delta = 0
    reasons: list[str] = []

    for phrase in _hits(hay, AUTONOMY_GREEN):
        delta += 4
        reasons.append(f"+async:{phrase}")
    for phrase in _hits(hay, CONTRACT_GREEN):
        delta += 3
        reasons.append(f"+contract:{phrase}")
    for phrase in _hits(hay, URGENCY_GREEN):
        delta += 2
        reasons.append(f"+urgent:{phrase}")
    for phrase in _hits(hay, JACK_OF_ALL_TRADES_GREEN):
        delta += 2
        reasons.append(f"+generalist:{phrase}")

    for phrase in _hits(hay, SCHEDULE_RED):
        delta -= 6
        reasons.append(f"-schedule:{phrase}")
    if is_w2_only(hay):
        delta -= 8
        reasons.append("-w2-only")

    loved, hated = _psyche_tokens()
    for tok in loved:
        if tok and tok in hay:
            delta += 2
            reasons.append(f"+psyche:{tok}")
    for tok in hated:
        if tok and tok in hay:
            delta -= 3
            reasons.append(f"-psyche:{tok}")

    # Cap contributions so one JD cannot dominate the entire score.
    delta = max(-35, min(25, delta))
    return delta, reasons[:6]


def title_seniority_penalty(title: str, deprioritize_keywords: tuple[str, ...]) -> tuple[int, str]:
    """Penalty when the title matches configured senior-IC deprioritize terms."""
    title_l = (title or "").lower()
    for kw in deprioritize_keywords:
        if kw and kw in title_l:
            return -18, kw
    # Bare "senior" without manager/principal/staff (those are usually killed elsewhere)
    if re.search(r"\bsenior\b", title_l) and not any(
        x in title_l for x in ("manager", "principal", "staff", "director", "vp")
    ):
        return -12, "senior"
    return 0, ""