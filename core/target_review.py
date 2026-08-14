"""Model-neutral, role-scoped review state for application preparation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jobpilot.core.config import DATA_DIR

REVIEWED_TARGETS_DIR = DATA_DIR / "reports"
REVIEWED_TARGETS_GLOB = "agent-reviewed-targets-*.json"
LEGACY_REVIEWED_TARGETS_GLOB = "claude-vetted-targets-*.json"


class TargetReviewError(RuntimeError):
    """A configured target-review report could not be read safely."""


def resolve_target_review_path(
    *,
    directory: Path = REVIEWED_TARGETS_DIR,
    override: Path | None = None,
) -> Path | None:
    """Return the newest neutral report, then a legacy compatible report."""
    if override is not None:
        path = Path(override)
        return path if path.is_file() else None
    for pattern in (REVIEWED_TARGETS_GLOB, LEGACY_REVIEWED_TARGETS_GLOB):
        candidates = [path for path in directory.glob(pattern) if path.is_file()]
        if candidates:
            return max(candidates, key=lambda path: path.stat().st_mtime)
    return None


def normalize_review_text(value: object) -> str:
    return " ".join(str(value or "").strip().lower().split())


def load_review_targets(review_path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(review_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise TargetReviewError(
            "The target-review report could not be read. Generate a valid JSON report and retry."
        ) from exc
    targets = data.get("targets", []) if isinstance(data, dict) else []
    return [target for target in targets if isinstance(target, dict)]


def find_review_target(job: object, review_path: Path) -> dict[str, Any] | None:
    company = normalize_review_text(getattr(job, "company", ""))
    title = normalize_review_text(getattr(job, "title", ""))
    url = normalize_review_text(getattr(job, "url", ""))
    company_matches: list[dict[str, Any]] = []
    for target in load_review_targets(review_path):
        target_url = normalize_review_text(target.get("url"))
        if target_url and target_url == url:
            return target
        if normalize_review_text(target.get("company")) == company:
            company_matches.append(target)
    for target in company_matches:
        target_title = normalize_review_text(target.get("title"))
        if target_title and target_title == title:
            return target
    titleless = [
        target
        for target in company_matches
        if not normalize_review_text(target.get("title"))
    ]
    return titleless[0] if len(titleless) == 1 else None


def review_state_for_job(
    job: object,
    *,
    review_path: Path | None = None,
) -> str:
    path = review_path or resolve_target_review_path()
    if path is None:
        return "unconfigured"
    try:
        target = find_review_target(job, path)
    except TargetReviewError:
        return "blocked"
    if target is None:
        return "unvetted"
    decision = normalize_review_text(target.get("decision"))
    materials = normalize_review_text(target.get("materials_status"))
    if decision in {"kill", "closed"}:
        return "closed"
    if decision != "keep":
        return "blocked"
    return "ready" if materials == "ready" else "not_ready"
