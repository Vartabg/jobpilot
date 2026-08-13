"""Retired application engine compatibility shell and read-only helpers.

Live browser monitoring, field mutation, uploads, and navigation are disabled.
The remaining chat, profile, and evidence helpers support legacy callers while
the public mutation-shaped methods fail closed at their boundary.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from jobpilot.core import llm_client
from jobpilot.core.application_tracker import ApplicationTracker
from jobpilot.core.autonomy import AutonomyConfig, AutonomyMode
from jobpilot.core.bro_client import get_health, is_bro_running, query_rag
from jobpilot.core.browser_interface import BrowserInterface
from jobpilot.core.engine_helpers import _build_job_context
from jobpilot.core.events import INFO, EventBus
from jobpilot.core.logger import get_logger
from jobpilot.core.profile_store import ProfileStore
from jobpilot.core.question_matcher import QuestionMatcher
from jobpilot.core.resume_tailor import ResumeTailor
from jobpilot.learning.action_recorder import ActionRecorder

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
        """Retired live-form mutation boundary; always fail closed."""
        del field, value
        log.warning("Blocked retired live ATS field-fill request")
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
        """Retired live ATS upload boundary; always fail closed."""
        del app_page, profile, parsed_jd
        log.warning("Blocked retired live ATS file-upload request")

    # -- auto-advance --------------------------------------------------------

    async def auto_advance(self, app_page) -> None:
        """Retired live-form navigation boundary; always fail closed."""
        del app_page
        log.warning("Blocked retired live ATS navigation request")

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
                    advice = self._field_advice(
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
            try:
                reply = llm_client.complete(msg, context=job_context)
            except llm_client.LLMUnavailable as exc:
                reply = str(exc)
            await self.chat.send_message(reply)

    @staticmethod
    def _field_advice(job_title: str, company: str, question: str) -> str:
        """AI advice for one application question via the active backend.

        Resume RAG context is included when the local Bro stack is up. On
        failure the error text is returned as the chat reply (legacy contract).
        """
        rag_context = (
            query_rag(f"{question} {job_title} {company}", top_k=5)
            if is_bro_running()
            else ""
        )

        context = f"""You are helping with a job application.
Job: {job_title} at {company}
Question: {question}

Relevant background from resume:
{rag_context if rag_context else "(No resume indexed yet)"}

Provide a concise, professional answer suggestion."""

        try:
            return llm_client.complete(question, context=context, smart=True)
        except llm_client.LLMUnavailable as exc:
            return str(exc)

    # -- voice dispatch ------------------------------------------------------

    async def handle_voice(self, cmd: dict, app_page) -> None:
        """Dispatch a single voice command."""
        cmd_name = cmd.get("command", "").lower()
        cmd_args = cmd.get("args", {})
        self.events.emit(INFO, message=f"🎤 Voice: {cmd_name} {cmd_args}")
        log.info("Voice command: %s", cmd_name)

        if cmd_name in ("approve", "approve_all"):
            await self.chat.send_message(
                "Live ATS field filling is retired; use the reviewed paste sheet."
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
            await self.chat.send_message(
                "Automated form navigation is retired; continue in your browser."
            )

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
        """Fail closed: browser monitoring and live ATS mutation are retired."""
        del watch
        raise RuntimeError(
            "Live ATS monitoring and filling are retired; use the human paste flow."
        )
