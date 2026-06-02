"""
Tests for core/engine.py — ApplicationEngine with mocked browser/services.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jobpilot.core.events import EventBus, INFO, WARNING, FIELD_FILLED, APPLICATION_STARTED, APPLICATION_SUBMITTED
from jobpilot.core.engine import ApplicationEngine, _build_job_context
from jobpilot.core.engine_run import _build_review_fields
from jobpilot.core.autonomy import AutonomyConfig, AutonomyMode
from jobpilot.core.linkedin_parser import FieldType, SemanticType


# ---------------------------------------------------------------------------
# Lightweight fakes
# ---------------------------------------------------------------------------

@dataclass
class FakePageInfo:
    url: str = "https://linkedin.com/jobs/view/123"
    title: str = "Senior Engineer - Easy Apply"
    is_linkedin: bool = True
    is_job_application: bool = True
    application_step: Optional[int] = 1


@dataclass
class FakeFormField:
    element: MagicMock = field(default_factory=lambda: MagicMock())
    field_type: FieldType = FieldType.TEXT
    semantic_type: SemanticType = SemanticType.EMAIL
    label: str = "Email address"
    placeholder: str = ""
    is_required: bool = True
    current_value: str = ""
    options: list = field(default_factory=list)
    confidence: float = 0.9


@dataclass
class FakeApplicationPage:
    current_step: int = 1
    total_steps: int = 3
    fields: list = field(default_factory=list)
    has_resume_upload: bool = False
    has_cover_letter: bool = False
    submit_button_text: str = "Next"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def events():
    return EventBus()


@pytest.fixture
def mock_bridge():
    bridge = MagicMock()
    bridge.page = MagicMock()
    bridge.get_page_info = AsyncMock(return_value=FakePageInfo())
    bridge.get_active_page = AsyncMock(return_value=MagicMock())
    bridge.disconnect = AsyncMock()
    return bridge


@pytest.fixture
def mock_overlay():
    overlay = MagicMock()
    overlay.update_status = AsyncMock()
    overlay.show_suggestions = AsyncMock()
    overlay.update_progress = AsyncMock()
    overlay.show_review = AsyncMock(return_value="submit")
    overlay.get_pending_action = AsyncMock(return_value=None)
    return overlay


@pytest.fixture
def mock_chat():
    chat = MagicMock()
    chat.send_message = AsyncMock()
    chat.get_messages = AsyncMock(return_value=[])
    chat.ensure_injected = AsyncMock()
    return chat


@pytest.fixture
def mock_profile_store():
    store = MagicMock()
    profile = MagicMock()
    profile.first_name = "Test"
    profile.last_name = "User"
    profile.email = "test@example.com"
    profile.current_title = "Engineer"
    profile.resume_path = ""
    profile.cover_letter_path = ""
    store.load.return_value = profile
    store.get_field_value.return_value = "test@example.com"
    return store


@pytest.fixture
def mock_question_matcher():
    matcher = MagicMock()
    match_result = MagicMock()
    match_result.answer = None
    match_result.confidence = 0.0
    matcher.match.return_value = match_result
    return matcher


@pytest.fixture
def mock_recorder():
    return MagicMock()


@pytest.fixture
def mock_tracker():
    tracker = MagicMock()
    tracker.get_status.return_value = None
    tracker.get_stats.return_value = {"submitted": 0, "abandoned": 0, "in_progress": 0, "total": 0}
    tracker.get_recent.return_value = []
    return tracker


@pytest.fixture
def engine(
    mock_bridge, events, mock_overlay, mock_chat,
    mock_profile_store, mock_question_matcher, mock_recorder, mock_tracker,
):
    return ApplicationEngine(
        bridge=mock_bridge,
        events=events,
        overlay=mock_overlay,
        chat_overlay=mock_chat,
        profile_store=mock_profile_store,
        question_matcher=mock_question_matcher,
        action_recorder=mock_recorder,
        app_tracker=mock_tracker,
        autonomy_config=AutonomyConfig(mode=AutonomyMode.SEMI_AUTO),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestBuildJobContext:
    def test_basic_context(self):
        info = FakePageInfo()
        ctx = _build_job_context(info, None, None)
        assert "LinkedIn" in ctx

    def test_with_application_page(self):
        info = FakePageInfo()
        app = FakeApplicationPage(fields=[FakeFormField()])
        ctx = _build_job_context(info, app, None)
        assert "Step 1/3" in ctx
        assert "Unfilled" in ctx

    def test_non_application(self):
        info = FakePageInfo(is_job_application=False)
        ctx = _build_job_context(info, None, None)
        assert "Easy Apply" not in ctx


class TestChatDispatch:
    @pytest.mark.asyncio
    async def test_help_command(self, engine, mock_chat):
        await engine.handle_chat("help", FakePageInfo(), None, None)
        mock_chat.send_message.assert_called_once()
        msg = mock_chat.send_message.call_args[0][0]
        assert "Commands:" in msg

    @pytest.mark.asyncio
    async def test_profile_command(self, engine, mock_chat):
        await engine.handle_chat("profile", FakePageInfo(), None, None)
        mock_chat.send_message.assert_called_once()
        msg = mock_chat.send_message.call_args[0][0]
        assert "Test User" in msg

    @pytest.mark.asyncio
    async def test_stats_command(self, engine, mock_chat):
        await engine.handle_chat("stats", FakePageInfo(), None, None)
        mock_chat.send_message.assert_called_once()
        msg = mock_chat.send_message.call_args[0][0]
        assert "Applied:" in msg

    @pytest.mark.asyncio
    async def test_mode_command_valid(self, engine, mock_chat):
        with patch("jobpilot.core.engine.set_autonomy_mode", create=True) as _:
            await engine.handle_chat("mode suggest", FakePageInfo(), None, None)
        # Should not crash; mode change handled gracefully

    @pytest.mark.asyncio
    async def test_history_no_apps(self, engine, mock_chat):
        await engine.handle_chat("history", FakePageInfo(), None, None)
        msg = mock_chat.send_message.call_args[0][0]
        assert "No applications" in msg

    @pytest.mark.asyncio
    @patch("jobpilot.core.engine.bro_chat")
    async def test_freeform_message(self, mock_bro, engine, mock_chat):
        mock_bro.return_value = "AI response"
        await engine.handle_chat("what is python?", FakePageInfo(), None, None)
        msg = mock_chat.send_message.call_args[0][0]
        assert msg == "AI response"


class TestVoiceDispatch:
    @pytest.mark.asyncio
    async def test_skip_command(self, engine, mock_chat, mock_recorder):
        app = FakeApplicationPage(fields=[FakeFormField()])
        await engine.handle_voice({"command": "skip"}, app)
        mock_chat.send_message.assert_called_once()
        assert "Skipped" in mock_chat.send_message.call_args[0][0]

    @pytest.mark.asyncio
    async def test_status_command(self, engine, mock_chat):
        app = FakeApplicationPage(current_step=2, total_steps=4)
        await engine.handle_voice({"command": "status"}, app)
        msg = mock_chat.send_message.call_args[0][0]
        assert "2/4" in msg

    @pytest.mark.asyncio
    async def test_status_no_app(self, engine, mock_chat):
        await engine.handle_voice({"command": "status"}, None)
        msg = mock_chat.send_message.call_args[0][0]
        assert "No active" in msg


class TestFillField:
    @pytest.mark.asyncio
    async def test_fill_text_field(self, engine):
        field = FakeFormField()
        field.element.wait_for_element_state = AsyncMock()
        field.element.fill = AsyncMock()
        field.element.type = AsyncMock()
        field.element.press = AsyncMock()

        result = await engine.fill_field(field, "test@example.com")
        assert result is True

    @pytest.mark.asyncio
    async def test_fill_select_field(self, engine):
        field = FakeFormField(field_type=FieldType.SELECT)
        field.element.wait_for_element_state = AsyncMock()
        field.element.select_option = AsyncMock()

        result = await engine.fill_field(field, "Option A")
        assert result is True

    @pytest.mark.asyncio
    async def test_fill_checkbox_check(self, engine):
        field = FakeFormField(field_type=FieldType.CHECKBOX)
        field.element.wait_for_element_state = AsyncMock()
        field.element.is_checked = AsyncMock(return_value=False)
        field.element.check = AsyncMock()

        result = await engine.fill_field(field, "yes")
        assert result is True
        field.element.check.assert_called_once()

    @pytest.mark.asyncio
    async def test_fill_retries_on_failure(self, engine):
        field = FakeFormField()
        field.element.wait_for_element_state = AsyncMock(
            side_effect=RuntimeError("detached")
        )
        result = await engine.fill_field(field, "val")
        assert result is False


class TestReviewGate:
    def test_build_review_fields_include_fit_and_resume_context(self, engine, mock_profile_store):
        profile = mock_profile_store.load.return_value
        profile.resume_path = "/tmp/garo_resume.pdf"
        profile.authorized_to_work = True
        profile.requires_sponsorship = False

        info = FakePageInfo(title="Senior Python Engineer - Easy Apply")
        info.fit_score = 82
        info.fit_recommendation = "Strong fit — worth applying"
        info.fit_matches = ["Python", "FastAPI", "AWS"]
        info.fit_risks = ["No explicit Kubernetes example"]

        app = FakeApplicationPage(
            submit_button_text="Submit application",
            fields=[FakeFormField(current_value="garo@example.com")],
        )

        with patch(
            "jobpilot.core.engine_run.ResumeTailor.load_latest_draft_summary",
            return_value={
                "markdown_path": "/tmp/resume_acme.md",
                "html_path": "/tmp/resume_acme.html",
                "pdf_path": "/tmp/resume_acme.pdf",
                "title": "Senior Python Engineer",
                "company": "Acme AI",
            },
        ):
            review_fields = _build_review_fields(engine, app, info)
        labels = [item["label"] for item in review_fields]
        values = {item["label"]: item["value"] for item in review_fields}

        assert "Fit Score" in labels
        assert values["Fit Score"].startswith("82/100")
        assert "Matched Skills" in labels
        assert "Resume" in labels
        assert values["Resume"] == "garo_resume.pdf"
        assert "Work Auth" in labels
        assert "Tailored Draft" in labels
        assert values["Tailored Draft"] == "resume_acme.pdf"
        assert "Draft Target" in labels
        assert values["Draft Target"] == "Senior Python Engineer @ Acme AI"
        assert "Email address" in labels


class TestResumeUpload:
    @pytest.mark.asyncio
    async def test_upload_files_prefers_latest_tailored_pdf(self, engine, mock_profile_store, tmp_path):
        profile = mock_profile_store.load.return_value
        primary_resume = tmp_path / "profile_resume.pdf"
        tailored_resume = tmp_path / "tailored_resume.pdf"
        primary_resume.write_text("primary resume")
        tailored_resume.write_text("tailored resume")
        profile.resume_path = str(primary_resume)

        file_input = MagicMock()
        file_input.set_input_files = AsyncMock()
        app = FakeApplicationPage(has_resume_upload=True)

        with (
            patch("jobpilot.core.engine.FILE_INPUTS.query_all", new=AsyncMock(return_value=[file_input])),
            patch(
                "jobpilot.core.engine.ResumeTailor.load_latest_draft_summary",
                return_value={"pdf_path": str(tailored_resume)},
            ),
        ):
            await engine.upload_files(app, profile)

        file_input.set_input_files.assert_awaited_once_with(str(tailored_resume))


class TestEventEmission:
    @pytest.mark.asyncio
    async def test_run_emits_info_on_start(self, engine, events, mock_bridge):
        received = []
        events.on(INFO, lambda **kw: received.append(kw))

        # run() with watch=False exits immediately after initial status
        await engine.run(watch=False)
        assert len(received) > 0
        messages = " ".join(r.get("message", "") for r in received)
        assert "Ready" in messages

    @pytest.mark.asyncio
    @patch("jobpilot.core.engine.get_health")
    async def test_run_reports_bro_status(self, mock_health, engine, events, mock_bridge):
        mock_health.return_value = {"status": "ok", "whisper": "ready"}
        received = []
        events.on(INFO, lambda **kw: received.append(kw))

        await engine.run(watch=False)
        messages = " ".join(r.get("message", "") for r in received)
        assert "Bro:" in messages
