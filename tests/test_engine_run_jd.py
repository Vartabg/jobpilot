"""
Tests for core/engine_run.py — JD parsing wired into the watch loop.

Regression tests for the dead-feature bug where the watch loop created a
``JDParser`` but never called it, so ``parsed_jd`` stayed ``None`` forever:
the fit score always computed 0, cover-letter generation never triggered,
and contextual answer matching (``match_with_context``) was never reached.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jobpilot.core.autonomy import AutonomyConfig, AutonomyMode
from jobpilot.core.engine import ApplicationEngine
from jobpilot.core.engine_run import run_watch_loop
from jobpilot.core.events import EventBus
from jobpilot.core.linkedin_parser import FieldType, SemanticType

JOB_URL = "https://www.linkedin.com/jobs/view/123"

JD_TEXT = """
About the job: We are hiring a Senior Python Engineer at Acme AI to build
agentic developer tools used by millions of job seekers.

Requirements:
• 5+ years of Python experience
• Experience with FastAPI and PostgreSQL
• Familiarity with AWS and Docker

Nice to have:
• Kubernetes experience

Salary: $150,000 - $180,000 /yr. This role is remote.
"""


# ---------------------------------------------------------------------------
# Lightweight fakes
# ---------------------------------------------------------------------------

@dataclass
class FakePageInfo:
    url: str = JOB_URL
    title: str = "Senior Python Engineer - Easy Apply"
    is_linkedin: bool = True
    is_job_application: bool = True
    application_step: Optional[int] = 1


@dataclass
class FakeFormField:
    element: MagicMock = field(default_factory=lambda: MagicMock())
    field_type: FieldType = FieldType.TEXT
    semantic_type: SemanticType = SemanticType.CUSTOM_QUESTION
    label: str = "Why do you want to work here?"
    placeholder: str = ""
    is_required: bool = True
    current_value: str = ""
    options: list = field(default_factory=list)
    confidence: float = 0.5


@dataclass
class FakeApplicationPage:
    current_step: int = 1
    total_steps: int = 3
    fields: list = field(default_factory=list)
    has_resume_upload: bool = False
    has_cover_letter: bool = False
    submit_button_text: str = "Next"


class FakeJDPage:
    """Fake Playwright page rendering a LinkedIn job description."""

    url = JOB_URL

    async def title(self) -> str:
        return "Senior Python Engineer | Acme AI | LinkedIn"

    async def evaluate(self, js):
        # The JDParser text-anchor strategy extracts the JD body via JS.
        return JD_TEXT

    async def query_selector(self, selector):
        return None

    async def query_selector_all(self, selector):
        return []


class FakeBrokenJDPage(FakeJDPage):
    """Fake page where every JD extraction hook blows up."""

    async def title(self) -> str:
        raise RuntimeError("page crashed")

    async def evaluate(self, js):
        raise RuntimeError("page crashed")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def events():
    return EventBus()


@pytest.fixture
def page_info():
    return FakePageInfo()


@pytest.fixture
def mock_bridge(page_info):
    bridge = MagicMock()
    bridge.page = FakeJDPage()
    bridge.get_page_info = AsyncMock(return_value=page_info)
    bridge.get_active_page = AsyncMock(return_value=bridge.page)
    bridge.disconnect = AsyncMock()
    return bridge


@pytest.fixture
def mock_overlay():
    overlay = MagicMock()
    overlay.update_status = AsyncMock()
    overlay.show_suggestions = AsyncMock()
    overlay.update_progress = AsyncMock()
    overlay.show_review = AsyncMock(return_value="cancel")
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
    store.get_field_value.return_value = None
    return store


@pytest.fixture
def mock_question_matcher():
    matcher = MagicMock()
    match_result = MagicMock()
    match_result.answer = None
    match_result.confidence = 0.0
    matcher.match.return_value = match_result
    matcher.match_with_context.return_value = match_result
    return matcher


@pytest.fixture
def mock_tracker():
    tracker = MagicMock()
    tracker.get_status.return_value = None
    tracker.get_stats.return_value = {
        "submitted": 0, "abandoned": 0, "in_progress": 0, "total": 0,
    }
    return tracker


@pytest.fixture
def engine(
    mock_bridge, events, mock_overlay, mock_chat,
    mock_profile_store, mock_question_matcher, mock_tracker,
):
    return ApplicationEngine(
        bridge=mock_bridge,
        events=events,
        overlay=mock_overlay,
        chat_overlay=mock_chat,
        profile_store=mock_profile_store,
        question_matcher=mock_question_matcher,
        action_recorder=MagicMock(),
        app_tracker=mock_tracker,
        autonomy_config=AutonomyConfig(mode=AutonomyMode.SEMI_AUTO),
    )


@pytest.fixture
def mock_scorer():
    scorer = MagicMock()
    fit_result = MagicMock()
    fit_result.score = 77
    fit_result.recommendation = "Strong fit"
    fit_result.matched_skills = ["Python", "FastAPI"]
    fit_result.risks = []
    scorer.score_parsed_jd.return_value = fit_result
    return scorer


async def _run_one_iteration(engine, *, app_page=None, scorer=None):
    """Run a single watch-loop iteration against the mocked engine.

    ``asyncio.sleep`` is patched to raise ``KeyboardInterrupt`` so the
    loop performs exactly one full pass and then shuts down via its own
    graceful-exit path.
    """
    linkedin_parser = MagicMock()
    linkedin_parser.parse_application = AsyncMock(return_value=app_page)
    scorer = scorer if scorer is not None else MagicMock()

    with (
        patch("jobpilot.core.engine_run.LinkedInParser", return_value=linkedin_parser),
        patch("jobpilot.core.engine_run.JobScorer", return_value=scorer),
        patch("jobpilot.core.engine_run._load_session", return_value=None),
        patch("jobpilot.core.engine_run._save_session"),
        patch("jobpilot.core.engine_run._clear_session"),
        patch("jobpilot.core.engine_run.get_pending_commands", return_value=[]),
        patch("jobpilot.core.engine.get_health", return_value={"status": "ok", "whisper": "ready"}),
        patch("jobpilot.core.engine_run.asyncio.sleep", new=AsyncMock(side_effect=KeyboardInterrupt)),
    ):
        await run_watch_loop(engine, watch=True)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestWatchLoopParsesJD:
    @pytest.mark.asyncio
    async def test_jd_is_parsed_and_feeds_fit_score(
        self, engine, page_info, mock_scorer,
    ):
        """Landing on a job page must produce a real parsed_jd and a
        non-zero fit score (previously parsed_jd stayed None forever)."""
        engine.handle_chat = AsyncMock()
        engine.chat.get_messages = AsyncMock(return_value=["hello"])

        await _run_one_iteration(engine, scorer=mock_scorer)

        # The fit-score path received the parsed JD
        mock_scorer.score_parsed_jd.assert_called_once()
        parsed_jd = mock_scorer.score_parsed_jd.call_args[0][0]
        assert parsed_jd is not None
        assert parsed_jd.title == "Senior Python Engineer"
        assert parsed_jd.company == "Acme AI"
        assert "Python" in parsed_jd.skills
        assert page_info.fit_score == 77
        assert page_info.fit_recommendation == "Strong fit"

        # The chat/answer path received the same parsed JD
        engine.handle_chat.assert_awaited_once()
        chat_parsed_jd = engine.handle_chat.call_args[0][3]
        assert chat_parsed_jd is parsed_jd

    @pytest.mark.asyncio
    async def test_parsed_jd_threads_into_uploads_and_answers(
        self, engine, mock_question_matcher, mock_scorer,
    ):
        """With form fields present, parsed_jd must reach upload_files and
        the contextual answer matcher (match_with_context)."""
        engine.upload_files = AsyncMock()
        engine.fill_field = AsyncMock(return_value=True)
        question_field = FakeFormField()
        app_page = FakeApplicationPage(fields=[question_field])

        await _run_one_iteration(engine, app_page=app_page, scorer=mock_scorer)

        engine.upload_files.assert_awaited_once()
        uploaded_jd = engine.upload_files.call_args[0][2]
        assert uploaded_jd is not None
        assert uploaded_jd.raw_text.strip() == JD_TEXT.strip()

        mock_question_matcher.match_with_context.assert_called_once()
        label, jd_summary = mock_question_matcher.match_with_context.call_args[0]
        assert label == question_field.label
        assert "Acme AI" in jd_summary
        mock_question_matcher.match.assert_not_called()

    @pytest.mark.asyncio
    async def test_jd_parse_failure_does_not_crash_loop(
        self, engine, mock_bridge, page_info, mock_scorer,
    ):
        """A blown-up JD parse must log and continue with parsed_jd=None."""
        mock_bridge.page = FakeBrokenJDPage()
        mock_bridge.get_active_page = AsyncMock(return_value=mock_bridge.page)

        # Must not raise
        await _run_one_iteration(engine, scorer=mock_scorer)

        mock_scorer.score_parsed_jd.assert_not_called()
        assert page_info.fit_score == 0

    @pytest.mark.asyncio
    async def test_no_parse_on_non_job_pages(
        self, engine, mock_bridge, mock_scorer,
    ):
        """Off job pages the parser must not run and no fit score is set."""
        info = FakePageInfo(
            url="https://www.linkedin.com/feed/",
            title="Feed | LinkedIn",
            is_job_application=False,
        )
        mock_bridge.get_page_info = AsyncMock(return_value=info)

        await _run_one_iteration(engine, scorer=mock_scorer)

        mock_scorer.score_parsed_jd.assert_not_called()
        assert not hasattr(info, "fit_score")
