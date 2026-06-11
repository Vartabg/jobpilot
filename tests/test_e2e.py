"""
End-to-End tests — exercise ApplicationEngine with a fully mocked browser.

These tests verify the real engine logic without launching Chrome.
MockBrowser returns fake PageInfo objects, MockOverlay stubs the UI,
and MockChat captures outbound messages.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from jobpilot.core.cdp_bridge import PageInfo
from jobpilot.core.events import EventBus, FIELD_FILLED, APPLICATION_STARTED, APPLICATION_SUBMITTED, APPLICATION_ABANDONED, INFO, WARNING
from jobpilot.core.engine import ApplicationEngine
from jobpilot.core.linkedin_parser import FieldType, SemanticType
from jobpilot.core.autonomy import AutonomyConfig, AutonomyMode

from tests.mock_browser import MockBrowser, MockOverlay, MockChat, MockElement


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeField:
    """Minimal field stand-in with an element and metadata."""

    def __init__(
        self,
        label: str,
        semantic_type: SemanticType,
        field_type: FieldType = FieldType.TEXT,
        confidence: float = 0.9,
        current_value: str = "",
    ):
        self.label = label
        self.semantic_type = semantic_type
        self.field_type = field_type
        self.confidence = confidence
        self.current_value = current_value
        self.element = MockElement(tag="input", input_type="text", value=current_value)


class FakeAppPage:
    """Minimal ApplicationPage stand-in."""

    def __init__(
        self,
        fields: list[FakeField] | None = None,
        current_step: int = 1,
        total_steps: int = 3,
        submit_button_text: str = "Next",
        has_resume_upload: bool = False,
        has_cover_letter: bool = False,
    ):
        self.fields = fields or []
        self.current_step = current_step
        self.total_steps = total_steps
        self.submit_button_text = submit_button_text
        self.has_resume_upload = has_resume_upload
        self.has_cover_letter = has_cover_letter


def _make_engine(
    *,
    browser: MockBrowser | None = None,
    events: EventBus | None = None,
    overlay: MockOverlay | None = None,
    chat: MockChat | None = None,
    autonomy_mode: AutonomyMode = AutonomyMode.SEMI_AUTO,
):
    """Factory for wiring up an engine with all mocked deps."""
    events = events or EventBus()
    browser = browser or MockBrowser()
    overlay = overlay or MockOverlay()
    chat = chat or MockChat()

    profile_store = MagicMock()
    profile_store.load.return_value = MagicMock(
        first_name="Alex", last_name="Test",
        email="alex@test.com", phone="555-0100",
        current_title="Engineer", resume_path=None,
        cover_letter_path=None,
    )
    profile_store.get_field_value.side_effect = lambda k: {
        "first_name": "Alex",
        "last_name": "Test",
        "email": "alex@test.com",
        "phone": "555-0100",
    }.get(k)

    question_matcher = MagicMock()
    question_matcher.match.return_value = MagicMock(answer=None, confidence=0)
    question_matcher.match_with_context.return_value = MagicMock(answer=None, confidence=0)

    action_recorder = MagicMock()
    app_tracker = MagicMock()
    app_tracker.get_status.return_value = None
    app_tracker.get_stats.return_value = {
        "submitted": 0, "abandoned": 0, "in_progress": 0, "total": 0,
    }
    app_tracker.get_recent.return_value = []

    config = AutonomyConfig()
    config.mode = autonomy_mode

    return ApplicationEngine(
        bridge=browser,
        events=events,
        overlay=overlay,
        chat_overlay=chat,
        profile_store=profile_store,
        question_matcher=question_matcher,
        action_recorder=action_recorder,
        app_tracker=app_tracker,
        autonomy_config=config,
    ), events, browser, overlay, chat, action_recorder, app_tracker


# ---------------------------------------------------------------------------
# Tests: Engine startup (no watch)
# ---------------------------------------------------------------------------

class TestEngineStartup:
    """Engine.run(watch=False) goes through startup then exits."""

    @pytest.mark.asyncio
    async def test_startup_emits_page_info(self):
        engine, events, *_ = _make_engine()
        received = []
        events.on(INFO, lambda **kw: received.append(kw.get("message", "")))

        with patch("jobpilot.core.engine.get_health", return_value={"status": "ok", "whisper": "ready"}):
            await engine.run(watch=False)

        assert any("LinkedIn" in msg for msg in received)

    @pytest.mark.asyncio
    async def test_startup_reports_bro_status(self):
        engine, events, *_ = _make_engine()
        received = []
        events.on(INFO, lambda **kw: received.append(kw.get("message", "")))

        with patch("jobpilot.core.engine.get_health", return_value={"status": "ok", "whisper": "ready"}):
            await engine.run(watch=False)

        assert any("Bro: ✓" in msg for msg in received)

    @pytest.mark.asyncio
    async def test_startup_non_linkedin_page(self):
        browser = MockBrowser(page_info=PageInfo(
            url="https://google.com", title="Google",
            is_linkedin=False, is_job_application=False,
        ))
        engine, events, *_ = _make_engine(browser=browser)
        received = []
        events.on(INFO, lambda **kw: received.append(kw.get("message", "")))

        with patch("jobpilot.core.engine.get_health", return_value={}):
            await engine.run(watch=False)

        assert any("Navigate to LinkedIn" in msg for msg in received)


# ---------------------------------------------------------------------------
# Tests: Field filling
# ---------------------------------------------------------------------------

class TestFieldFilling:
    """Engine correctly fills various field types."""

    @pytest.mark.asyncio
    async def test_fill_text_field(self):
        engine, *_ = _make_engine()
        field = FakeField("First name", SemanticType.FIRST_NAME)
        result = await engine.fill_field(field, "Alex")
        assert result is True
        assert field.element._value  # should have content

    @pytest.mark.asyncio
    async def test_fill_select_field(self):
        engine, *_ = _make_engine()
        field = FakeField("Country", SemanticType.COUNTRY, FieldType.SELECT)
        result = await engine.fill_field(field, "United States")
        assert result is True

    @pytest.mark.asyncio
    async def test_fill_checkbox(self):
        engine, *_ = _make_engine()
        field = FakeField("Accept terms", SemanticType.UNKNOWN, FieldType.CHECKBOX)
        field.element._checked = False
        result = await engine.fill_field(field, "yes")
        assert result is True
        assert field.element._checked is True

    @pytest.mark.asyncio
    async def test_uncheck_checkbox(self):
        engine, *_ = _make_engine()
        field = FakeField("Opt out", SemanticType.UNKNOWN, FieldType.CHECKBOX)
        field.element._checked = True
        result = await engine.fill_field(field, "no")
        assert result is True
        assert field.element._checked is False

    @pytest.mark.asyncio
    async def test_fill_retries_on_failure(self):
        engine, *_ = _make_engine()
        field = FakeField("Broken field", SemanticType.FIRST_NAME)
        field.element._visible = False  # will cause _wait_for_stable to fail
        result = await engine.fill_field(field, "value")
        assert result is False


# ---------------------------------------------------------------------------
# Tests: Chat dispatch
# ---------------------------------------------------------------------------

class TestChatDispatch:
    """Chat commands processed correctly by the engine."""

    @pytest.mark.asyncio
    async def test_help(self):
        engine, _, _, _, chat, *_ = _make_engine()
        page_info = PageInfo("https://linkedin.com", "Job", True, True)
        await engine.handle_chat("help", page_info, None, None)
        assert any("help" in m.lower() for m in chat.sent_messages)

    @pytest.mark.asyncio
    async def test_stats(self):
        engine, _, _, _, chat, _, tracker = _make_engine()
        tracker.get_stats.return_value = {
            "submitted": 5, "abandoned": 2, "in_progress": 1, "total": 8,
        }
        page_info = PageInfo("https://linkedin.com", "Job", True, True)
        await engine.handle_chat("stats", page_info, None, None)
        assert any("5" in m for m in chat.sent_messages)

    @pytest.mark.asyncio
    async def test_mode_change(self):
        engine, _, _, _, chat, *_ = _make_engine()
        page_info = PageInfo("https://linkedin.com", "Job", True, True)
        await engine.handle_chat("mode suggest", page_info, None, None)
        assert any("suggest" in m.lower() for m in chat.sent_messages)

    @pytest.mark.asyncio
    async def test_mode_invalid(self):
        engine, _, _, _, chat, *_ = _make_engine()
        page_info = PageInfo("https://linkedin.com", "Job", True, True)
        await engine.handle_chat("mode ultra", page_info, None, None)
        assert any("valid" in m.lower() for m in chat.sent_messages)

    @pytest.mark.asyncio
    async def test_profile_command(self):
        engine, _, _, _, chat, *_ = _make_engine()
        page_info = PageInfo("https://linkedin.com", "Job", True, True)
        await engine.handle_chat("profile", page_info, None, None)
        assert any("Alex" in m for m in chat.sent_messages)

    @pytest.mark.asyncio
    async def test_history_empty(self):
        engine, _, _, _, chat, *_ = _make_engine()
        page_info = PageInfo("https://linkedin.com", "Job", True, True)
        await engine.handle_chat("history", page_info, None, None)
        assert any("No applications" in m for m in chat.sent_messages)

    @pytest.mark.asyncio
    async def test_freeform_message_calls_llm_backend(self):
        engine, _, _, _, chat, *_ = _make_engine()
        page_info = PageInfo("https://linkedin.com", "Job", True, True)
        with patch("jobpilot.core.engine.llm_client.complete", return_value="I can help!"):
            await engine.handle_chat("What is Python?", page_info, None, None)
        assert any("I can help" in m for m in chat.sent_messages)


# ---------------------------------------------------------------------------
# Tests: Voice dispatch
# ---------------------------------------------------------------------------

class TestVoiceDispatch:

    @pytest.mark.asyncio
    async def test_skip_command(self):
        engine, _, _, _, chat, recorder, _ = _make_engine()
        app_page = FakeAppPage(fields=[
            FakeField("Name", SemanticType.FIRST_NAME),
        ])
        await engine.handle_voice({"command": "skip", "args": {}}, app_page)
        assert any("skip" in m.lower() for m in chat.sent_messages)
        recorder.record_field_skipped.assert_called_once()

    @pytest.mark.asyncio
    async def test_status_with_app(self):
        engine, _, _, _, chat, *_ = _make_engine()
        app_page = FakeAppPage(current_step=2, total_steps=4)
        await engine.handle_voice({"command": "status", "args": {}}, app_page)
        assert any("2/4" in m for m in chat.sent_messages)

    @pytest.mark.asyncio
    async def test_status_without_app(self):
        engine, _, _, _, chat, *_ = _make_engine()
        await engine.handle_voice({"command": "status", "args": {}}, None)
        assert any("No active" in m for m in chat.sent_messages)


# ---------------------------------------------------------------------------
# Tests: Protocol compliance
# ---------------------------------------------------------------------------

class TestProtocolCompliance:
    """Verify MockBrowser satisfies BrowserInterface."""

    def test_mock_browser_is_browser_interface(self):
        from jobpilot.core.browser_interface import BrowserInterface
        browser = MockBrowser()
        assert isinstance(browser, BrowserInterface)


# ---------------------------------------------------------------------------
# Tests: Page sequence (multi-step lifecycle)
# ---------------------------------------------------------------------------

class TestPageSequence:
    """Engine reacts correctly to page transitions."""

    @pytest.mark.asyncio
    async def test_page_sequence_navigation(self):
        """MockBrowser returns different pages in sequence."""
        browser = MockBrowser(page_sequence=[
            PageInfo("https://linkedin.com/jobs/view/1", "Job 1", True, True, 1),
            PageInfo("https://linkedin.com/jobs/view/1", "Job 1", True, True, 2),
            PageInfo("https://linkedin.com/feed", "Feed", True, False),
        ])
        info1 = await browser.get_page_info()
        assert info1.application_step == 1
        info2 = await browser.get_page_info()
        assert info2.application_step == 2
        info3 = await browser.get_page_info()
        assert info3.is_job_application is False

    @pytest.mark.asyncio
    async def test_mock_overlay_queue_action(self):
        overlay = MockOverlay()
        overlay.queue_action({"id": 0, "action": "approve"})
        action = await overlay.get_pending_action()
        assert action == {"id": 0, "action": "approve"}
        # Second call returns None
        assert await overlay.get_pending_action() is None

    @pytest.mark.asyncio
    async def test_mock_chat_message_roundtrip(self):
        chat = MockChat()
        chat.queue_message("hello")
        msgs = await chat.get_messages()
        assert msgs == ["hello"]
        # Queue is cleared
        assert await chat.get_messages() == []
