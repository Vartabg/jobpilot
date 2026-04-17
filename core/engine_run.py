"""
Engine run loop — the main watch loop extracted from ApplicationEngine.

This module contains the ``run_watch_loop`` coroutine, which is the 425-line
core of the application engine.  It is separated to keep ``engine.py``
under the 200-line workspace limit.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from jobpilot.core.config import WATCH_LOOP_INTERVAL
from jobpilot.core.events import (
    FIELD_FILLED, FIELD_SKIPPED, FIELD_EDITED,
    APPLICATION_STARTED, APPLICATION_SUBMITTED, APPLICATION_ABANDONED,
    INFO, WARNING, ERROR,
)
from jobpilot.core.linkedin_parser import LinkedInParser, SemanticType
from jobpilot.core.jd_parser import JDParser
from jobpilot.core.selector_registry import NEXT_BUTTON, FILE_INPUTS
from jobpilot.core.bro_client import get_pending_commands
from jobpilot.core.job_scorer import JobScorer
from jobpilot.core.logger import get_logger
from jobpilot.core.resume_tailor import ResumeTailor
from jobpilot.core.engine_helpers import (
    _load_session, _save_session, _clear_session,
)

if TYPE_CHECKING:
    from jobpilot.core.engine import ApplicationEngine

log = get_logger(__name__)


def _truncate_review_value(value: str, *, limit: int = 90) -> str:
    """Keep review-dialog values compact and readable."""
    text = (value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _build_review_fields(engine: "ApplicationEngine", app_page, page_info) -> list[dict[str, str]]:
    """Build the pre-submit review payload shown in the overlay."""
    review_fields: list[dict[str, str]] = []

    title = getattr(page_info, "title", "") or ""
    if title:
        review_fields.append({
            "label": "Job",
            "value": _truncate_review_value(title, limit=72),
        })

    fit_score = getattr(page_info, "fit_score", 0) or 0
    recommendation = getattr(page_info, "fit_recommendation", "") or ""
    if fit_score:
        value = f"{fit_score}/100"
        if recommendation:
            value = f"{value} — {recommendation}"
        review_fields.append({"label": "Fit Score", "value": value})

    matched = getattr(page_info, "fit_matches", []) or []
    if matched:
        review_fields.append({
            "label": "Matched Skills",
            "value": ", ".join(matched[:4]),
        })

    risks = getattr(page_info, "fit_risks", []) or []
    if risks:
        review_fields.append({
            "label": "Review Risks",
            "value": _truncate_review_value("; ".join(risks[:2])),
        })

    try:
        profile = engine.profile_store.load()
    except Exception:
        profile = None

    if profile is not None:
        resume_path = getattr(profile, "resume_path", "") or ""
        if resume_path:
            review_fields.append({
                "label": "Resume",
                "value": Path(str(resume_path)).expanduser().name or str(resume_path),
            })

        authorized = getattr(profile, "authorized_to_work", None)
        requires_sponsorship = getattr(profile, "requires_sponsorship", None)
        if authorized is True and requires_sponsorship is False:
            review_fields.append({
                "label": "Work Auth",
                "value": "US authorized, no sponsorship",
            })
        elif requires_sponsorship is True:
            review_fields.append({
                "label": "Work Auth",
                "value": "Requires sponsorship",
            })

    latest_draft = ResumeTailor.load_latest_draft_summary()
    if latest_draft:
        draft_path = (
            latest_draft.get("pdf_path")
            or latest_draft.get("html_path")
            or latest_draft.get("markdown_path")
            or ""
        )
        if draft_path:
            review_fields.append({
                "label": "Tailored Draft",
                "value": Path(str(draft_path)).expanduser().name,
            })

        draft_title = str(latest_draft.get("title", "") or "").strip()
        draft_company = str(latest_draft.get("company", "") or "").strip()
        draft_target = " @ ".join(part for part in [draft_title, draft_company] if part)
        if draft_target:
            review_fields.append({
                "label": "Draft Target",
                "value": _truncate_review_value(draft_target, limit=72),
            })

    review_fields.extend(
        {
            "label": f.label or f.semantic_type.value,
            "value": _truncate_review_value(f.current_value or ""),
        }
        for f in app_page.fields
    )
    return review_fields


async def run_watch_loop(engine: "ApplicationEngine", *, watch: bool) -> None:
    """Main watch loop — polls Chrome, fills fields, handles chat.

    This is the extracted ``ApplicationEngine.run`` method.  All access
    to engine state goes through the *engine* parameter.
    """
    page_info = await engine.bridge.get_page_info()
    engine.events.emit(
        INFO,
        message=(
            f"  Page: {page_info.title[:50]}..."
            if page_info.title
            else "  Page: (no title)"
        ),
    )
    engine.events.emit(
        INFO,
        message=f"  LinkedIn: {'✓' if page_info.is_linkedin else '✗'}",
    )

    if not page_info.is_linkedin:
        engine.events.emit(
            INFO, message="Navigate to LinkedIn Jobs to get started.",
        )

    health = engine.get_runtime_health()
    bro_s = "✓" if health.get("status") == "ok" else "✗"
    whisper_s = "✓" if health.get("whisper") == "ready" else "✗"
    engine.events.emit(
        INFO, message=f"  Bro: {bro_s}  Whisper: {whisper_s}",
    )

    saved = _load_session()
    if saved:
        engine.events.emit(
            INFO,
            message=(
                f"📂 Found saved session: "
                f"{saved.get('url', '')[:60]}"
            ),
        )

    engine.events.emit(
        INFO,
        message=(
            f"✓ Ready! Mode: {engine.autonomy_config.mode.value}\n"
            "Press Ctrl+C to exit (session will be saved)"
        ),
    )
    log.info("JobPilot started — mode=%s", engine.autonomy_config.mode.value)

    if not watch:
        return

    # ----- watch loop state -----
    parser = LinkedInParser(engine.bridge.page)
    jd_parser = JDParser(engine.bridge.page)
    app_page = None
    parsed_jd = None
    prev_was_application = False
    prev_job_url: Optional[str] = None
    editing_fields: dict[int, str] = {}

    try:
            await engine.bridge.get_active_page()
            page_info = await engine.bridge.get_page_info()
            
            # Reduce polling noise in logs (only log if path changes)
            if page_info.url != prev_job_url:
                log.debug("Polling page: %s", page_info.url)

            # --- lifecycle tracking ---
            if page_info.is_job_application and not prev_was_application:
                prev_status = engine.app_tracker.get_status(page_info.url)
                if prev_status == "submitted":
                    engine.events.emit(
                        WARNING,
                        message="⚠  You already submitted this application!",
                    )
                    await engine.overlay.update_status("⚠ Already Applied")
                    log.info("Duplicate detected: %s", page_info.url)
                elif prev_status == "abandoned":
                    engine.events.emit(
                        INFO,
                        message="↩  Resuming previously abandoned application",
                    )

                # Calculate Fit Analysis immediately
                fit_score = 0
                fit_result = None
                if parsed_jd and (parsed_jd.raw_text or parsed_jd.summary()):
                    try:
                        fit_result = JobScorer().score_parsed_jd(parsed_jd)
                        fit_score = fit_result.score
                    except Exception as exc:
                        log.debug("Fit scoring fallback failed: %s", exc)

                # Save fit context to page_info for reuse in the submit review gate
                page_info.fit_score = fit_score
                page_info.fit_recommendation = getattr(fit_result, "recommendation", "")
                page_info.fit_matches = getattr(fit_result, "matched_skills", [])[:5] if fit_result else []
                page_info.fit_risks = getattr(fit_result, "risks", [])[:3] if fit_result else []

                engine.app_tracker.mark_started(
                    page_info.url, page_info.title or "", "LinkedIn"
                )
                engine.action_recorder.record_application_started(
                    page_info.url, page_info.title or "", "LinkedIn"
                )
                engine.events.emit(
                    APPLICATION_STARTED,
                    title=page_info.title or "",
                    url=page_info.url,
                )
                log.info("Application started: %s", page_info.title)

            elif prev_was_application and not page_info.is_job_application:
                if prev_job_url and "submit" in (
                    page_info.title or ""
                ).lower():
                    await engine.overlay.show_success()
                    engine.action_recorder.record_application_submitted()
                    engine.app_tracker.mark_submitted(prev_job_url)
                    engine.events.emit(APPLICATION_SUBMITTED)
                    log.info("Application submitted: %s", prev_job_url)
                    _clear_session()
                else:
                    step = app_page.current_step if app_page else 1
                    engine.action_recorder.record_application_abandoned(
                        step_number=step
                    )
                    if prev_job_url:
                        engine.app_tracker.mark_abandoned(prev_job_url, step)
                    engine.events.emit(
                        APPLICATION_ABANDONED, step=step,
                    )
                    log.info("Application abandoned at step %d", step)
                app_page = None
                editing_fields.clear()

            prev_was_application = page_info.is_job_application
            prev_job_url = (
                page_info.url if page_info.is_job_application else None
            )

            # Re-inject overlays after navigation
            await engine.chat.ensure_injected()

            # --- chat ---
            user_msgs = await engine.chat.get_messages()
            for msg in user_msgs:
                engine.events.emit(INFO, message=f"User: {msg}")
                await engine.handle_chat(msg, page_info, app_page, parsed_jd)

            # --- voice ---
            pending_cmds = get_pending_commands()
            for cmd in pending_cmds:
                await engine.handle_voice(cmd, app_page)

            # --- edit detection ---
            if editing_fields and app_page and app_page.fields:
                for field_id, suggested_value in list(
                    editing_fields.items()
                ):
                    if field_id < len(app_page.fields):
                        field = app_page.fields[field_id]
                        current = field.current_value or ""
                        if current and current != suggested_value:
                            engine.action_recorder.record_field_edited(
                                field.label,
                                field.semantic_type.value,
                                suggested_value,
                                current,
                                field.confidence,
                            )
                            if (
                                field.semantic_type
                                == SemanticType.CUSTOM_QUESTION
                            ):
                                engine.question_matcher.learn_from_edit(
                                    field.label,
                                    suggested_value,
                                    current,
                                )
                            engine.events.emit(
                                FIELD_EDITED,
                                label=field.label,
                                old=suggested_value,
                                new=current,
                            )
                            await engine.overlay.show_learning_toast(field.label)
                            log.info(
                                "Learned edit: %s: '%s' → '%s'",
                                field.label,
                                suggested_value,
                                current,
                            )
                            del editing_fields[field_id]

            # --- form parsing & filling ---
            if page_info.is_job_application:
                app_page = await parser.parse_application()

                if app_page and app_page.fields:
                    await engine.overlay.update_status(
                        f"Step {app_page.current_step}/"
                        f"{app_page.total_steps}"
                    )

                    profile = engine.profile_store.load()
                    await engine.upload_files(
                        app_page, profile, parsed_jd,
                    )

                    suggestions = []
                    auto_filled_count = 0
                    for i, field in enumerate(app_page.fields):
                        suggestion = {
                            "label": (
                                field.label or field.semantic_type.value
                            ),
                            "confidence": field.confidence,
                            "suggestion": None,
                        }

                        value = engine.profile_store.get_field_value(
                            field.semantic_type.value
                        )
                        if value:
                            suggestion["suggestion"] = value
                            suggestion["confidence"] = max(
                                field.confidence, 0.8
                            )
                        elif (
                            field.semantic_type
                            == SemanticType.CUSTOM_QUESTION
                        ):
                            jd_summary = (
                                parsed_jd.summary() if parsed_jd else ""
                            )
                            match = (
                                engine.question_matcher.match_with_context(
                                    field.label, jd_summary
                                )
                                if jd_summary
                                else engine.question_matcher.match(
                                    field.label
                                )
                            )
                            if match.answer:
                                suggestion["suggestion"] = match.answer
                                suggestion["confidence"] = (
                                    match.confidence
                                )
                                value = match.answer

                        # confidence-gated auto-fill
                        if (
                            value
                            and not field.current_value
                            and engine.autonomy_config.should_auto_fill(
                                suggestion["confidence"]
                            )
                        ):
                            success = await engine.fill_field(field, value)
                            if success:
                                auto_filled_count += 1
                                engine.events.emit(
                                    FIELD_FILLED, label=field.label,
                                )
                                engine.action_recorder.record_field_approved(
                                    field.label,
                                    field.semantic_type.value,
                                    value,
                                    suggestion["confidence"],
                                )
                                log.info(
                                    "Auto-filled: %s (conf=%.0f%%)",
                                    field.label,
                                    suggestion["confidence"] * 100,
                                )

                        suggestions.append(suggestion)

                    # Update overlay with suggestions and fit score
                    fit_score = getattr(page_info, 'fit_score', 0)
                    await engine.overlay.show_suggestions(suggestions, fit_score=fit_score)

                    # progress dashboard
                    filled_count = len(
                        [f for f in app_page.fields if f.current_value]
                    )
                    total_count = len(app_page.fields)
                    apps_today = engine.app_tracker.get_stats().get(
                        "submitted", 0
                    )
                    await engine.overlay.update_progress(
                        filled=filled_count + auto_filled_count,
                        total=total_count,
                        step=app_page.current_step,
                        total_steps=app_page.total_steps,
                        apps_today=apps_today,
                    )

                    # pre-submit review
                    is_final = "submit" in (
                        app_page.submit_button_text or ""
                    ).lower()
                    unfilled = [
                        f
                        for f in app_page.fields
                        if not f.current_value
                    ]
                    if (
                        not unfilled
                        and auto_filled_count > 0
                        and is_final
                    ):
                        review_fields = _build_review_fields(
                            engine,
                            app_page,
                            page_info,
                        )
                        decision = await engine.overlay.show_review(review_fields)
                        if decision == "submit":
                            await engine.auto_advance(app_page)
                            engine.events.emit(
                                INFO,
                                message="✓ User approved submission",
                            )
                            log.info("Pre-submit review: approved")
                        else:
                            engine.events.emit(
                                WARNING,
                                message="✗ Submission cancelled by review",
                            )
                            log.info("Pre-submit review: cancelled")
                    elif not unfilled and auto_filled_count > 0:
                        await engine.auto_advance(app_page)

                    filled_names = [
                        f.label
                        for f in app_page.fields
                        if f.current_value
                    ]
                    _save_session(
                        page_info.url,
                        app_page.current_step,
                        filled_names,
                    )
                else:
                    await engine.overlay.update_status("Ready")
            else:
                await engine.overlay.update_status("Navigate to a job")

            # --- overlay actions ---
            action = await engine.overlay.get_pending_action()
            if action and app_page and app_page.fields:
                field_id = action["id"]
                action_type = action["action"]

                if field_id < len(app_page.fields):
                    field = app_page.fields[field_id]

                    if action_type == "approve":
                        value = engine.profile_store.get_field_value(
                            field.semantic_type.value
                        )
                        if (
                            not value
                            and field.semantic_type
                            == SemanticType.CUSTOM_QUESTION
                        ):
                            match = engine.question_matcher.match(
                                field.label
                            )
                            value = (
                                match.answer if match.answer else None
                            )
                        if value:
                            await engine.fill_field(field, value)
                            engine.events.emit(
                                FIELD_FILLED, label=field.label,
                            )
                            engine.action_recorder.record_field_approved(
                                field.label,
                                field.semantic_type.value,
                                value,
                                field.confidence,
                            )

                    elif action_type == "edit":
                        try:
                            await field.element.click()
                            engine.events.emit(
                                INFO,
                                message=(
                                    f"✎ Editing {field.label} "
                                    "— type your value"
                                ),
                            )
                            engine.action_recorder.start_field_timer()
                            suggested = (
                                engine.profile_store.get_field_value(
                                    field.semantic_type.value
                                )
                                or ""
                            )
                            editing_fields[field_id] = suggested
                        except Exception as e:
                            engine.events.emit(
                                ERROR,
                                message=f"Could not focus field: {e}",
                            )

                    elif action_type == "skip":
                        engine.events.emit(
                            FIELD_SKIPPED, label=field.label,
                        )
                        engine.action_recorder.record_field_skipped(
                            field.label, field.semantic_type.value,
                        )

            await asyncio.sleep(WATCH_LOOP_INTERVAL)

    except KeyboardInterrupt:
        engine.events.emit(
            INFO,
            message="Session saved. Run 'jobpilot start' to resume.",
        )
        log.info("Graceful shutdown — session saved")
    finally:
        engine.app_tracker.close()
        await engine.bridge.disconnect()
