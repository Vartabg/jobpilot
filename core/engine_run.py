"""Read-only review helpers and a retired watch-loop compatibility fence."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from jobpilot.core.resume_tailor import ResumeTailor

if TYPE_CHECKING:
    from jobpilot.core.engine import ApplicationEngine


def _truncate_review_value(value: str, *, limit: int = 90) -> str:
    """Keep review-dialog values compact and readable."""
    text = (value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _build_review_fields(
    engine: "ApplicationEngine", app_page, page_info
) -> list[dict[str, str]]:
    """Build a read-only summary of already-observed application values."""
    review_fields: list[dict[str, str]] = []

    title = getattr(page_info, "title", "") or ""
    if title:
        review_fields.append(
            {
                "label": "Job",
                "value": _truncate_review_value(title, limit=72),
            }
        )

    fit_score = getattr(page_info, "fit_score", 0) or 0
    recommendation = getattr(page_info, "fit_recommendation", "") or ""
    if fit_score:
        value = f"{fit_score}/100"
        if recommendation:
            value = f"{value} — {recommendation}"
        review_fields.append({"label": "Fit Score", "value": value})

    matched = getattr(page_info, "fit_matches", []) or []
    if matched:
        review_fields.append(
            {
                "label": "Matched Skills",
                "value": ", ".join(matched[:4]),
            }
        )

    risks = getattr(page_info, "fit_risks", []) or []
    if risks:
        review_fields.append(
            {
                "label": "Review Risks",
                "value": _truncate_review_value("; ".join(risks[:2])),
            }
        )

    try:
        profile = engine.profile_store.load()
    except Exception:
        profile = None

    if profile is not None:
        resume_path = getattr(profile, "resume_path", "") or ""
        if resume_path:
            review_fields.append(
                {
                    "label": "Resume",
                    "value": (
                        Path(str(resume_path)).expanduser().name
                        or str(resume_path)
                    ),
                }
            )

        authorized = getattr(profile, "authorized_to_work", None)
        requires_sponsorship = getattr(profile, "requires_sponsorship", None)
        if authorized is True and requires_sponsorship is False:
            review_fields.append(
                {
                    "label": "Work Auth",
                    "value": "US authorized, no sponsorship",
                }
            )
        elif requires_sponsorship is True:
            review_fields.append(
                {
                    "label": "Work Auth",
                    "value": "Requires sponsorship",
                }
            )

    latest_draft = ResumeTailor.load_latest_draft_summary()
    if latest_draft:
        draft_path = (
            latest_draft.get("pdf_path")
            or latest_draft.get("html_path")
            or latest_draft.get("markdown_path")
            or ""
        )
        if draft_path:
            review_fields.append(
                {
                    "label": "Tailored Draft",
                    "value": Path(str(draft_path)).expanduser().name,
                }
            )

        draft_title = str(latest_draft.get("title", "") or "").strip()
        draft_company = str(latest_draft.get("company", "") or "").strip()
        draft_target = " @ ".join(
            part for part in [draft_title, draft_company] if part
        )
        if draft_target:
            review_fields.append(
                {
                    "label": "Draft Target",
                    "value": _truncate_review_value(draft_target, limit=72),
                }
            )

    review_fields.extend(
        {
            "label": field.label or field.semantic_type.value,
            "value": _truncate_review_value(field.current_value or ""),
        }
        for field in app_page.fields
    )
    return review_fields


async def run_watch_loop(engine: "ApplicationEngine", *, watch: bool) -> None:
    """Fail closed without inspecting or controlling a browser session."""
    del engine, watch
    raise RuntimeError(
        "Live ATS monitoring and filling are retired; use the human paste flow."
    )
