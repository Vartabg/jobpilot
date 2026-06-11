"""Learn from pipeline passes — sync Notes into feedback ledger.

Tag passes in pipeline Notes with a short reason code, e.g.:

  pass:wrong-stack
  pass:low-pay
  pass:wrong-role
  pass:spam

Plain text notes are still stored; codes drive aggregation for tuning.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from jobpilot.gigs.core import pipeline
from jobpilot.gigs.core.io_lock import atomic_write_text, file_lock
from jobpilot.gigs.core.paths import data_dir

DATA_DIR = data_dir()
FEEDBACK_PATH = DATA_DIR / "feedback.jsonl"
INDEX_PATH = DATA_DIR / "feedback_index.json"

# \b keeps "bypass:foo" from matching while letting the code appear anywhere
# in the cell ("too low — pass:low-pay"), not just at the start.
_PASS_CODE_RE = re.compile(
    r"\bpass:\s*([a-z][a-z0-9_-]*)",
    re.IGNORECASE,
)

KNOWN_REASONS = frozenset({
    "wrong-stack",
    "low-pay",
    "wrong-role",
    "spam",
    "location",
    "contract-only",
    "duplicate",
    "other",
})


@dataclass(frozen=True)
class FeedbackEntry:
    gig_id: str
    company: str
    role: str
    reason: str
    notes: str
    recorded_at: str


def parse_pass_reason(notes: str) -> str:
    """Extract `pass:<code>` from anywhere in Notes; default to 'other'."""
    text = (notes or "").strip()
    match = _PASS_CODE_RE.search(text)
    if match:
        code = match.group(1).lower().replace("_", "-")
        return code if code in KNOWN_REASONS else "other"
    return "other"


def _load_index_unlocked() -> dict[str, str]:
    if not INDEX_PATH.exists():
        return {}
    try:
        data = json.loads(INDEX_PATH.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_index(index: dict[str, str]) -> None:
    with file_lock(INDEX_PATH):
        atomic_write_text(INDEX_PATH, json.dumps(index, indent=2))


def sync_from_pipeline(
    rows: list[pipeline.Row] | None = None,
) -> int:
    """Append new pass feedback from pipeline rows. Returns entries added."""
    rows = rows if rows is not None else pipeline.parse()
    index = _load_index_unlocked()
    added = 0
    now = datetime.now().isoformat()

    with file_lock(FEEDBACK_PATH):
        FEEDBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
        for row in rows:
            if row.status not in {"passed", "archived"} or not row.gig_id:
                continue
            reason = parse_pass_reason(row.notes)
            fingerprint = f"{row.gig_id}|{reason}|{row.notes.strip()}"
            if index.get(row.gig_id) == fingerprint:
                continue
            entry = {
                "gig_id": row.gig_id,
                "company": row.company,
                "role": row.role,
                "reason": reason,
                "notes": row.notes,
                "recorded_at": now,
            }
            with FEEDBACK_PATH.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry) + "\n")
            index[row.gig_id] = fingerprint
            added += 1

    if added:
        _save_index(index)
    return added


def aggregate_reasons() -> dict[str, int]:
    """Count pass reasons from the feedback ledger."""
    counts: dict[str, int] = {}
    if not FEEDBACK_PATH.exists():
        return counts
    for line in FEEDBACK_PATH.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        reason = row.get("reason", "other")
        counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items(), key=lambda x: -x[1]))


def format_summary() -> str:
    counts = aggregate_reasons()
    if not counts:
        return (
            "No pass feedback yet. When you pass a gig, add Notes like "
            "`pass:wrong-stack` or `pass:low-pay`."
        )
    lines = ["Pass feedback (aggregate):", ""]
    for reason, n in counts.items():
        lines.append(f"  {reason:16} {n}")
    lines.append("")
    lines.append("Codes: " + ", ".join(sorted(KNOWN_REASONS)))
    return "\n".join(lines)