"""One-time migration: take existing `applied.jsonl` and seed `pipeline.md`.

Idempotent — safe to run multiple times; only adds rows that aren't already
present. A marker file skips re-reading after the first successful run.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from jobpilot.gigs.core import pipeline
from jobpilot.gigs.core.paths import data_dir

APPLIED_PATH = data_dir() / "applied.jsonl"
MIGRATED_MARKER = data_dir() / ".applied_migrated"


def migrate_applied_into_pipeline(
    applied_path: Path = APPLIED_PATH,
    pipeline_path: Path = pipeline.PIPELINE_PATH,
) -> int:
    """Read applied.jsonl and add corresponding rows to pipeline.md as 'sent'.

    Returns the number of rows added. Idempotent — IDs already in the
    pipeline are skipped.
    """
    if MIGRATED_MARKER.exists():
        return 0
    if not applied_path.exists():
        MIGRATED_MARKER.write_text("no applied.jsonl\n")
        return 0

    existing = pipeline.parse(pipeline_path)
    existing_ids = {r.gig_id for r in existing}

    added = 0
    try:
        for line in applied_path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            gid = row.get("id")
            if not gid or gid in existing_ids:
                continue
            saved_at = row.get("saved_at", "")
            saved_short = ""
            if saved_at:
                try:
                    dt = datetime.fromisoformat(saved_at)
                    saved_short = dt.strftime("%-m/%-d")
                except Exception:
                    pass
            existing.append(pipeline.Row(
                status="sent",
                score=int(row.get("fit_score", 0)),
                company=row.get("company", "") or "",
                role=(row.get("title", "") or "").split("|")[0].strip()[:80],
                pay="",
                apply=row.get("apply_url", "") or row.get("url", "") or "",
                saved=saved_short,
                last_touched=saved_short,
                next_action="follow up if no reply by Friday",
                notes="migrated from applied.jsonl",
                gig_id=gid,
            ))
            existing_ids.add(gid)
            added += 1
    except Exception:
        return added

    if added:
        pipeline.write(existing, pipeline_path)
    MIGRATED_MARKER.write_text(f"added={added}\n")
    return added