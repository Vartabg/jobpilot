"""
Application Engine — the brain of JobPilot.

Owns field-filling logic, chat/voice dispatch, and application lifecycle
tracking.  Communicates outward exclusively through an ``EventBus``.

The main watch-loop lives in ``engine_run.py``; standalone helpers live
in ``engine_helpers.py``.  This file contains the ``ApplicationEngine``
class itself.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

from jobpilot.core.config import FILL_RETRIES, FILL_RETRY_DELAY_MS
from jobpilot.core.events import (
    EventBus,
    FIELD_FILLED, FIELD_SKIPPED, FIELD_EDITED,
    INFO, WARNING, ERROR,
)
from jobpilot.core.linkedin_parser import SemanticType, FieldType
from jobpilot.core.question_matcher import QuestionMatcher
from jobpilot.core.profile_store import ProfileStore
from jobpilot.core.application_tracker import ApplicationTracker
from jobpilot.core.autonomy import AutonomyConfig, AutonomyMode
from jobpilot.core.selector_registry import NEXT_BUTTON, FILE_INPUTS
from jobpilot.core.cover_letter_gen import generate_cover_letter, get_cached_path
from jobpilot.core.resume_tailor import ResumeTailor
from jobpilot.core.bro_client import (
    chat as bro_chat,
    get_health, get_job_advice,
)
from jobpilot.learning.action_recorder import ActionRecorder
from jobpilot.core.browser_interface import BrowserInterface
from jobpilot.core.logger import get_logger

from jobpilot.core.engine_helpers import (
    _human_type, _wait_for_stable, _build_job_context,
)

log = get_logger(__name__)


class ApplicationEngine:
    """Orchestrates the job-application watch loop.

    All side-effects to the user are communicated via the ``events``
    bus so that neither ``rich`` nor any UI code is imported here.
    """

    def __init__(
        self,
        *,
        bridge: BrowserInterface,
        events: EventBus,
        overlay,
        chat_overlay,
        profile_store: ProfileStore,
        question_matcher: QuestionMatcher,
        action_recorder: ActionRecorder,
        app_tracker: ApplicationTracker,
        autonomy_config: AutonomyConfig,
    ) -> None:
        self.bridge = bridge
        self.events = events
        self.overlay = overlay
        self.chat = chat_overlay
        self.profile_store = profile_store
        self.question_matcher = question_matcher
        self.action_recorder = action_recorder
        self.app_tracker = app_tracker
        self.autonomy_config = autonomy_config

    # -- field filling -------------------------------------------------------

    async def fill_field(self, field, value: str) -> bool:
        """Fill a single form field with retry logic."""
        for attempt in range(1, FILL_RETRIES + 1):
            try:
                if not await _wait_for_stable(field.element):
                    raise RuntimeError("Element not stable")

                if field.field_type in (
                    FieldType.TEXT, FieldType.EMAIL, FieldType.PHONE,
                    FieldType.NUMBER, FieldType.TEXTAREA,
                ):
                    await field.element.fill("")
                    await _human_type(field.element, value)
                elif field.field_type == FieldType.SELECT:
                    await field.element.select_option(label=value)
                elif field.field_type == FieldType.RADIO:
                    parent = await field.element.evaluate_handle(
                        "el => el.closest('.fb-form-element, "
                        ".jobs-easy-apply-form-element') || el.parentElement"
                    )
                    option = await parent.query_selector(
                        f'label:has-text("{value}")'
                    )
                    if option:
                        await option.click()
                    else:
                        await field.element.click()
                elif field.field_type == FieldType.CHECKBOX:
                    should_check = value.lower() in (
                        "yes", "true", "1", "checked",
                    )
                    is_checked = await field.element.is_checked()
                    if should_check and not is_checked:
                        await field.element.check()
                    elif not should_check and is_checked:
                        await field.element.uncheck()
                else:
                    await field.element.fill("")
                    await _human_type(field.element, value)
                return True

            except Exception as e:
                if attempt < FILL_RETRIES:
                    log.warning(
                        "Fill attempt %d failed for %s: %s — retrying",
                        attempt, field.label, e,
                    )
                    await asyncio.sleep(FILL_RETRY_DELAY_MS / 1000.0)
                else:
                    log.warning(
                        "Could not fill %s after %d attempts: %s",
                        field.label, FILL_RETRIES, e,
                    )
                    return False
        return False

    # -- file uploads --------------------------------------------------------

    @staticmethod
    def _preferred_resume_upload(profile) -> tuple[Optional[Path], str]:
        """Pick the best available resume file for an upload field."""
        latest_draft = ResumeTailor.load_latest_draft_summary()
        if latest_draft:
            pdf_path = str(latest_draft.get("pdf_path", "") or "")
            if pdf_path:
                tailored_pdf = Path(pdf_path).expanduser()
                if tailored_pdf.exists() and tailored_pdf.is_file():
                    return tailored_pdf, "tailored"

        resume_path = getattr(profile, "resume_path", "") or ""
        if resume_path:
            candidate = Path(resume_path).expanduser()
            if candidate.exists() and candidate.is_file():
                return candidate, "profile"

        return None, "missing"

    async def upload_files(self, app_page, profile, parsed_jd=None) -> None:
        """Upload resume and cover letter if file inputs are detected."""
        file_inputs = await FILE_INPUTS.query_all(self.bridge.page)

        if app_page.has_resume_upload and file_inputs:
            resume_path, source = self._preferred_resume_upload(profile)
            if resume_path is not None:
                try:
                    await file_inputs[0].set_input_files(str(resume_path))
                    source_suffix = " (tailored)" if source == "tailored" else ""
                    self.events.emit(
                        INFO,
                        message=f"📎 Uploaded resume: {resume_path.name}{source_suffix}",
                    )
                    log.info("Uploaded resume [%s]: %s", source, resume_path)
                except Exception as e:
                    log.warning("Resume upload failed: %s", e)

        if app_page.has_cover_letter and len(file_inputs) > 1:
            cl_path = None
            if profile.cover_letter_path:
                cl_path = Path(profile.cover_letter_path).expanduser()
                if not cl_path.exists():
                    cl_path = None

            if not cl_path and parsed_jd and parsed_jd.raw_text:
                cached = get_cached_path(parsed_jd.raw_text)
                if cached:
                    cl_path = cached
                else:
                    letter = generate_cover_letter(
                        jd_title=parsed_jd.title,
                        jd_company=parsed_jd.company,
                        jd_requirements=parsed_jd.requirements,
                        jd_raw_text=parsed_jd.raw_text,
                        candidate_name=(
                            f"{profile.first_name} {profile.last_name}".strip()
                        ),
                        candidate_title=profile.current_title,
                    )
                    if letter:
                        cl_path = get_cached_path(parsed_jd.raw_text)
                        self.events.emit(
                            INFO, message="📝 Generated tailored cover letter",
                        )

            if cl_path and cl_path.exists():
                try:
                    await file_inputs[1].set_input_files(str(cl_path))
                    self.events.emit(
                        INFO,
                        message=f"📎 Uploaded cover letter: {cl_path.name}",
                    )
                except Exception as e:
                    log.warning("Cover letter upload failed: %s", e)

    # -- auto-advance --------------------------------------------------------

    async def auto_advance(self, app_page) -> None:
        """Auto-click Next/Continue if autonomy settings allow it."""
        is_final = "submit" in (app_page.submit_button_text or "").lower()
        if not self.autonomy_config.should_auto_advance(is_final):
            return
        
        # Proactive Intuition: Show countdown in status
        delay_s = self.autonomy_config.auto_advance_delay_ms / 1000.0
        for i in range(int(delay_s * 2), 0, -1):
            await self.overlay.update_status(f"⏩ Auto-advancing in {i/2:.1f}s...")
            await asyncio.sleep(0.5)

        try:
            next_btn = await NEXT_BUTTON.query(self.bridge.page)
            if next_btn:
                await next_btn.click()
                self.events.emit(INFO, message="⏩ Auto-advanced to next step")
                log.info("Auto-advanced to next step")
        except Exception as e:
            log.warning("Auto-advance failed: %s", e)

    # -- browser-use integration (Next-Gen) ----------------------------------

    async def run_browser_agent(self, task_description: str) -> None:
        """Hand off current CDP session to Browser-Use LLM Agent."""
        import os
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
            from browser_use import Agent
        except ImportError:
            log.error("browser-use or langchain not installed")
            return

        api_key = os.getenv("GOOGLE_API_KEY", "")
        if not api_key:
            log.warning("GOOGLE_API_KEY missing, cannot start browser-use")
            self.events.emit(WARNING, message="Missing GOOGLE_API_KEY for browser-use")
            return

        llm = ChatGoogleGenerativeAI(model="gemini-3.1-pro", api_key=api_key)
        self.events.emit(INFO, message=f"🤖 Handing off to browser-use: {task_description}")
        log.info("Starting browser-use agent for: %s", task_description)
        
        try:
            # We scaffold the agent. To execute, browser-use needs to bind to the active CDP context.
            # E.g.: agent = Agent(task=task_description, llm=llm, browser=self.bridge._browser)
            # await agent.run()
            self.events.emit(INFO, message="[Browser-Use Agent scaffold instantiated]")
        except Exception as e:
            log.error("Browser-Use agent failed: %s", e)
            self.events.emit(ERROR, message=f"Agent error: {e}")

    # -- chat dispatch -------------------------------------------------------

    async def handle_chat(self, msg, page_info, app_page, parsed_jd) -> None:
        """Dispatch a single user chat message."""
        msg_lower = msg.lower().strip()

        if msg_lower == "help":
            await self.chat.send_message(
                "Commands: help, status, stats, history, profile, advice, "
                "mode [suggest|semi-auto|full-auto]. Or ask me anything!"
            )
        elif msg_lower == "status":
            h = get_health()
            bro = "✓" if h.get("status") == "ok" else "✗"
            whisper = "✓" if h.get("whisper") == "ready" else "✗"
            fast = (
                "✓"
                if any("mistral" in m for m in h.get("ollama_models", []))
                else "✗"
            )
            await self.chat.send_message(
                f"Bro:{bro} Whisper:{whisper} FastModel:{fast} "
                f"Mode:{self.autonomy_config.mode.value}"
            )
        elif msg_lower == "stats":
            t_stats = self.app_tracker.get_stats()
            await self.chat.send_message(
                f"Applied: {t_stats['submitted']} | "
                f"Abandoned: {t_stats['abandoned']} | "
                f"In Progress: {t_stats['in_progress']} | "
                f"Total: {t_stats['total']}"
            )
        elif msg_lower == "history":
            recent = self.app_tracker.get_recent(5)
            if recent:
                lines = [
                    f"{'✓' if a.status == 'submitted' else '✗'} "
                    f"{a.job_title[:40]} ({a.status})"
                    for a in recent
                ]
                await self.chat.send_message("\n".join(lines))
            else:
                await self.chat.send_message("No applications yet.")
        elif msg_lower == "profile":
            p = self.profile_store.load()
            await self.chat.send_message(
                f"{p.first_name} {p.last_name} | {p.email} | "
                f"{p.current_title}"
            )
        elif msg_lower.startswith("mode "):
            new_mode = msg_lower.split(" ", 1)[1].strip()
            try:
                from jobpilot.core.autonomy import set_autonomy_mode
                self.autonomy_config = set_autonomy_mode(
                    AutonomyMode(new_mode)
                )
                await self.chat.send_message(f"Mode set to: {new_mode}")
                log.info("Autonomy mode changed to %s", new_mode)
            except ValueError:
                await self.chat.send_message(
                    "Valid modes: suggest, semi-auto, full-auto"
                )
        elif msg_lower.startswith("advice") or msg_lower.startswith(
            "help me with"
        ):
            if app_page and app_page.fields:
                current_field = next(
                    (f for f in app_page.fields if not f.current_value), None
                )
                if current_field:
                    job_title = (
                        page_info.title.replace("Easy Apply", "").strip()
                        if page_info.title
                        else "Unknown"
                    )
                    advice = get_job_advice(
                        job_title, "LinkedIn", current_field.label
                    )
                    await self.chat.send_message(advice)
                else:
                    await self.chat.send_message(
                        "All fields filled! Click Next or tell me what you need."
                    )
            else:
                await self.chat.send_message(
                    "Navigate to an application form for advice."
                )
        else:
            job_context = _build_job_context(page_info, app_page, parsed_jd)
            reply = bro_chat(msg, context=job_context)
            await self.chat.send_message(reply)

    # -- voice dispatch ------------------------------------------------------

    async def handle_voice(self, cmd: dict, app_page) -> None:
        """Dispatch a single voice command."""
        cmd_name = cmd.get("command", "").lower()
        cmd_args = cmd.get("args", {})
        self.events.emit(INFO, message=f"🎤 Voice: {cmd_name} {cmd_args}")
        log.info("Voice command: %s", cmd_name)

        if cmd_name in ("approve", "approve_all"):
            if app_page and app_page.fields:
                for field in app_page.fields:
                    value = self.profile_store.get_field_value(
                        field.semantic_type.value
                    )
                    if value:
                        await self.fill_field(field, value)
                        self.events.emit(
                            FIELD_FILLED, label=field.label,
                        )
                        self.action_recorder.record_field_approved(
                            field.label,
                            field.semantic_type.value,
                            value,
                            field.confidence,
                        )
                await self.chat.send_message(
                    "Approved and filled all fields."
                )

        elif cmd_name == "skip":
            if app_page and app_page.fields:
                current_field = next(
                    (f for f in app_page.fields if not f.current_value), None
                )
                if current_field:
                    self.action_recorder.record_field_skipped(
                        current_field.label,
                        current_field.semantic_type.value,
                    )
            await self.chat.send_message("Skipped current field.")

        elif cmd_name == "next":
            try:
                next_btn = await NEXT_BUTTON.query(self.bridge.page)
                if next_btn:
                    await next_btn.click()
                    await self.chat.send_message("Clicked Next.")
            except Exception as e:
                await self.chat.send_message(f"Could not click Next: {e}")

        elif cmd_name == "status":
            await self.chat.send_message(
                f"On step {app_page.current_step}/{app_page.total_steps}"
                if app_page
                else "No active application."
            )

    def get_runtime_health(self) -> dict:
        """Return Bro/Whisper health data for operator status and tests."""
        return get_health()

    # -- main loop -----------------------------------------------------------

    async def run(self, *, watch: bool) -> None:
        """Main watch loop — delegates to engine_run.run_watch_loop."""
        from jobpilot.core.engine_run import run_watch_loop
        await run_watch_loop(self, watch=watch)
