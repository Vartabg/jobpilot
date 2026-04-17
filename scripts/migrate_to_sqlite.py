#!/usr/bin/env python3
"""
migrate_to_sqlite.py — one-shot migration from JSON/JSONL to SQLite.

Reads existing ``data/templates.json`` and ``data/actions.jsonl``, writes
them into ``data/learning.db``, then renames the originals to ``.bak``.

Safe to run multiple times — it skips already-imported rows.
"""

from __future__ import annotations

import json
from pathlib import Path

from jobpilot.core.config import DATA_DIR
from jobpilot.learning.learning_db import LearningDB


def migrate(data_dir: Path | None = None) -> None:
    data_dir = data_dir or DATA_DIR
    db = LearningDB(data_dir / "learning.db")

    templates_path = data_dir / "templates.json"
    actions_path = data_dir / "actions.jsonl"

    # --- templates ---
    if templates_path.exists():
        try:
            data = json.loads(templates_path.read_text())
            questions = data.get("questions", {})
            imported = 0
            for q, a in questions.items():
                if db.get_template(q) is None:
                    db.upsert_template(q, a)
                    imported += 1
            print(f"✓ Imported {imported} templates ({len(questions)} total in file)")
            templates_path.rename(templates_path.with_suffix(".json.bak"))
            print(f"  Renamed {templates_path.name} → {templates_path.name}.bak")
        except Exception as e:
            print(f"✗ Templates migration failed: {e}")
    else:
        print(f"  No {templates_path.name} found — skipping")

    # --- actions ---
    if actions_path.exists():
        try:
            imported = 0
            total = 0
            with open(actions_path) as f:
                for line in f:
                    total += 1
                    try:
                        action = json.loads(line.strip())
                        db.record_action(
                            action_type=action.get("action_type", "unknown"),
                            timestamp=action.get("timestamp"),
                            job_url=action.get("job_url"),
                            job_title=action.get("job_title"),
                            company=action.get("company"),
                            field_label=action.get("field_label"),
                            field_type=action.get("field_type"),
                            suggested_value=action.get("suggested_value"),
                            final_value=action.get("final_value"),
                            confidence=action.get("confidence"),
                            time_spent_ms=action.get("time_spent_ms"),
                            step_number=action.get("step_number"),
                        )
                        imported += 1
                    except json.JSONDecodeError:
                        continue
            print(f"✓ Imported {imported} actions ({total} lines in file)")
            actions_path.rename(actions_path.with_suffix(".jsonl.bak"))
            print(f"  Renamed {actions_path.name} → {actions_path.name}.bak")
        except Exception as e:
            print(f"✗ Actions migration failed: {e}")
    else:
        print(f"  No {actions_path.name} found — skipping")

    db.close()
    print(f"\n✓ Migration complete → {data_dir / 'learning.db'}")


if __name__ == "__main__":
    migrate()
