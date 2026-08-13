"""Pure read-only context helpers for the retired application engine."""

from __future__ import annotations


def _build_job_context(page_info, app_page, parsed_jd=None) -> str:
    """Build context from values already observed by a caller."""
    parts = ["User is on LinkedIn."]

    if parsed_jd:
        parts.append(parsed_jd.summary(300))
    elif page_info and page_info.title:
        job_title = (
            page_info.title.replace("Easy Apply", "").replace("|", "-").strip()
        )
        parts.append(f"Job: {job_title}")

    if page_info and page_info.is_job_application:
        parts.append("They are in an Easy Apply form.")
        if app_page:
            parts.append(f"Step {app_page.current_step}/{app_page.total_steps}")
            if app_page.fields:
                unfilled = [
                    field for field in app_page.fields if not field.current_value
                ]
                filled = [
                    field for field in app_page.fields if field.current_value
                ]
                if unfilled:
                    names = [
                        field.label or field.semantic_type.value
                        for field in unfilled[:3]
                    ]
                    parts.append(f"Unfilled: {', '.join(names)}")
                if filled:
                    parts.append(f"{len(filled)} fields already filled.")

    return " ".join(parts)
