"""Truth-bounded cover-letter drafts with no model-generated claims."""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

from jobpilot.core.config import DATA_DIR as JOBPILOT_DATA_DIR
from jobpilot.core.logger import get_logger

log = get_logger(__name__)

DATA_DIR = JOBPILOT_DATA_DIR / "cover_letters"


def _jd_hash(jd_text: str) -> str:
    """Deterministic short hash of a JD for cache keying."""
    return hashlib.sha256(jd_text.encode()).hexdigest()[:12]


def generate_cover_letter(
    jd_title: str,
    jd_company: str,
    jd_requirements: list[str],
    jd_raw_text: str,
    candidate_name: str = "",
    candidate_title: str = "",
) -> str:
    """Create a conservative draft from explicit arguments only.

    Job requirements are deliberately not converted into candidate claims.
    The user must add specific evidence from their resume or true-account bank
    before sending.
    """
    del jd_requirements
    hash_key = _jd_hash(jd_raw_text)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = DATA_DIR / f"{hash_key}.txt"

    role = jd_title.strip() or "the open role"
    company = jd_company.strip() or "your organization"
    name = candidate_name.strip() or "The Candidate"
    explicit_title = candidate_title.strip()
    background = (
        f"My current or most recent title is {explicit_title}. "
        if explicit_title
        else ""
    )
    letter = (
        f"{datetime.now().strftime('%B %d, %Y')}\n\n"
        "Dear Hiring Manager,\n\n"
        f"I am applying for {role} at {company}. {background}"
        "I would welcome the chance to discuss the role and connect each "
        "qualification to specific, documented examples from my background.\n\n"
        "This is a preparation draft. Before sending, I will add only examples "
        "that are supported by my resume or verified account bank.\n\n"
        f"Sincerely,\n{name}"
    )
    cache_path.write_text(letter)
    log.info("Generated truth-bounded cover-letter draft: %s", cache_path.name)
    return letter


def get_cached_path(jd_raw_text: str) -> Path | None:
    """Return the cached cover-letter path if it exists."""
    path = DATA_DIR / f"{_jd_hash(jd_raw_text)}.txt"
    return path if path.exists() else None
