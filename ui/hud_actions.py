"""Keyboard actions for the interactive HUD."""

from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

from jobpilot.core.queue_builder import QueueJob, update_job_status
from jobpilot.gigs.core.models import Gig
from jobpilot.ui.view_helpers import materials_ready


def open_url(url: str) -> bool:
    url = (url or "").strip()
    if not url:
        return False
    subprocess.run(["open", url], check=False)
    return True


def copy_text(text: str) -> bool:
    text = (text or "").strip()
    if not text:
        return False
    try:
        subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)
        return True
    except Exception:
        return False


def selected_gig_url(gig: Gig) -> str:
    return (gig.apply_url or gig.url or "").strip()


def set_gig_pipeline_status(gig: Gig, status: str, *, note: str = "") -> str:
    from jobpilot.gigs.core import pipeline

    rows = pipeline.parse()
    rows = pipeline.merge_new_gigs(rows, [gig])
    now = datetime.now().strftime("%Y-%m-%d")
    updated: list = []
    found = False
    for row in rows:
        if row.gig_id == gig.id:
            notes = row.notes or ""
            if note:
                notes = f"{notes} {note}".strip()
            updated.append(pipeline.Row(
                status=status,
                score=gig.fit_score,
                company=row.company or gig.company,
                role=row.role or gig.title,
                pay=row.pay,
                apply=row.apply or selected_gig_url(gig),
                saved=now if status == "saved" else row.saved,
                last_touched=now,
                next_action=row.next_action,
                notes=notes,
                gig_id=gig.id,
            ))
            found = True
        else:
            updated.append(row)
    if not found:
        return "pipeline row not found after merge"
    result = pipeline.write(updated)
    if result.refused:
        return "pipeline write refused (shrink guard)"
    return f"pipeline → {status}"


def skip_job(job: QueueJob) -> str:
    if update_job_status(job.id, "skipped"):
        return f"skipped {job.company}"
    return "could not skip job"


def open_materials(company: str) -> str:
    path = materials_ready(company)
    if not path:
        return "no materials — draft answers first"
    subprocess.run(["open", str(path)], check=False)
    return f"opened {path.name}"


def draft_gig_proposal(gig: Gig) -> str:
    from jobpilot.gigs.core.proposals import build_revenue_brief

    brief = build_revenue_brief(gig)
    out_dir = Path(__file__).resolve().parent.parent / "data" / "gigs" / "drafts"
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = "".join(c if c.isalnum() else "-" for c in (gig.company or "gig")).strip("-").lower()[:40]
    path = out_dir / f"{slug or 'gig'}_{gig.id[:8]}.txt"
    path.write_text(f"{brief.offer}\n\n---\n\n{brief.action}\n")
    subprocess.run(["open", str(path)], check=False)
    return f"draft → {path.name}"


def pick_with_fzf(lines: list[str]) -> Optional[int]:
    if not lines:
        return None
    try:
        proc = subprocess.run(
            ["fzf", "--height=40%", "--reverse", "--prompt=JobPilot> "],
            input="\n".join(lines).encode("utf-8"),
            capture_output=True,
            check=False,
        )
    except FileNotFoundError:
        return None
    if proc.returncode != 0 or not proc.stdout:
        return None
    choice = proc.stdout.decode("utf-8").strip()
    for i, line in enumerate(lines):
        if line == choice:
            return i
    return None