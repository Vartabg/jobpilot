"""
Tests that every AI feature routes through core/llm_client.py.

Bro is optional: with the local stack down, any configured backend (e.g.
Gemini) must still power answer drafting, job-fit verdicts, question
fallback/enrichment, and cover letters. All llm_client calls are mocked —
no network access.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from jobpilot.core import cover_letter_gen
from jobpilot.core.application_answerer import ApplicationAnswerer
from jobpilot.core.job_scorer import JobScorer
from jobpilot.core.llm_client import LLMUnavailable
from jobpilot.core.question_matcher import QuestionMatcher
from jobpilot.core.profile_store import UserProfile

LONG_REPLY = (
    "I built a small automation tool that handled the exact workflow this "
    "role describes, and I can walk through the trade-offs I made."
)


class SyntheticProfileStore:
    def load(self) -> UserProfile:
        return UserProfile(
            first_name="Riley",
            last_name="Nguyen",
            current_title="Operations Coordinator",
            years_of_experience=7,
            authorized_to_work=True,
        )


def _answerer(tmp_path: Path) -> ApplicationAnswerer:
    accounts_path = tmp_path / "true_accounts.json"
    accounts_path.write_text(json.dumps({
        "accounts": [
            {
                "id": "clinic-scheduler",
                "title": "Improved scheduling at a community clinic",
                "summary": "Built a scheduling tracker that cut patient wait times.",
                "skills": ["scheduling", "operations"],
            }
        ]
    }))
    return ApplicationAnswerer(
        profile_store=SyntheticProfileStore(),
        accounts_path=accounts_path,
        use_bro=True,
    )


# ---------------------------------------------------------------------------
# ApplicationAnswerer
# ---------------------------------------------------------------------------

class TestAnswererRouting:
    @patch("jobpilot.core.application_answerer.llm_client.complete", return_value=LONG_REPLY)
    @patch("jobpilot.core.application_answerer.llm_client.is_available", return_value=True)
    def test_ai_draft_uses_llm_client(self, _avail, mock_complete, tmp_path):
        draft = _answerer(tmp_path).draft("Why are you interested in this role?")

        assert draft.source == "ai"
        assert draft.answer.startswith("I built a small automation tool")
        assert mock_complete.call_args[1]["smart"] is True

    @patch(
        "jobpilot.core.application_answerer.llm_client.complete",
        side_effect=LLMUnavailable("down"),
    )
    @patch("jobpilot.core.application_answerer.llm_client.is_available", return_value=True)
    def test_backend_failure_falls_back_to_accounts(self, _avail, _complete, tmp_path):
        draft = _answerer(tmp_path).draft("Why are you interested in this role?")

        assert draft.source == "fallback"
        assert draft.answer  # account-grounded fallback still drafts
        assert any("AI backend was unavailable" in w for w in draft.warnings)

    @patch("jobpilot.core.application_answerer.llm_client.is_available", return_value=False)
    def test_no_backend_skips_ai_entirely(self, _avail, tmp_path):
        with patch("jobpilot.core.application_answerer.llm_client.complete") as mock_complete:
            draft = _answerer(tmp_path).draft("Why are you interested in this role?")

        assert draft.source == "fallback"
        mock_complete.assert_not_called()


# ---------------------------------------------------------------------------
# JobScorer
# ---------------------------------------------------------------------------

class TestScorerRouting:
    def _score(self):
        scorer = JobScorer(profile_store=SyntheticProfileStore(), use_bro=True)
        return scorer.score_text(
            "Coordinate schedules and operations for a busy clinic. Remote.",
            title="Operations Coordinator",
            company="Acme Health",
        )

    @patch("jobpilot.core.job_scorer.is_bro_running", return_value=False)
    @patch(
        "jobpilot.core.job_scorer.llm_client.complete",
        return_value="Strong operations fit. Watch the clinical-domain gap.",
    )
    @patch("jobpilot.core.job_scorer.llm_client.is_available", return_value=True)
    def test_ai_summary_via_llm_client_without_bro(self, _avail, mock_complete, _bro):
        result = self._score()
        assert result.ai_summary == "Strong operations fit. Watch the clinical-domain gap."
        # No Bro → no RAG context is passed to the backend.
        assert mock_complete.call_args[1]["context"] is None

    @patch("jobpilot.core.job_scorer.is_bro_running", return_value=False)
    @patch(
        "jobpilot.core.job_scorer.llm_client.complete",
        side_effect=LLMUnavailable("down"),
    )
    @patch("jobpilot.core.job_scorer.llm_client.is_available", return_value=True)
    def test_backend_failure_yields_empty_summary(self, _avail, _complete, _bro):
        result = self._score()
        assert result.ai_summary == ""
        assert result.score >= 0  # scoring itself is unaffected


# ---------------------------------------------------------------------------
# QuestionMatcher
# ---------------------------------------------------------------------------

class TestQuestionMatcherRouting:
    @patch("jobpilot.core.bro_client.is_bro_running", return_value=False)
    @patch("jobpilot.core.llm_client.complete", return_value="Answer: I bring 7 years of operations work.")
    @patch("jobpilot.core.llm_client.is_available", return_value=True)
    def test_rag_fallback_works_without_bro_using_profile_context(
        self, _avail, mock_complete, _bro, tmp_path
    ):
        matcher = QuestionMatcher(data_dir=tmp_path)
        with patch.object(
            QuestionMatcher, "_profile_context", return_value="Current title: Operations Coordinator"
        ):
            result = matcher.match("What do you bring to this team?")

        assert result.matched_template == "[AI Generated from Resume]"
        assert result.answer == "I bring 7 years of operations work."
        assert result.confidence == 0.6
        prompt = mock_complete.call_args[0][0]
        assert "Operations Coordinator" in prompt

    @patch("jobpilot.core.bro_client.is_bro_running", return_value=False)
    @patch("jobpilot.core.llm_client.is_available", return_value=True)
    def test_rag_fallback_refuses_without_any_grounding_context(
        self, _avail, _bro, tmp_path
    ):
        matcher = QuestionMatcher(data_dir=tmp_path)
        with (
            patch.object(QuestionMatcher, "_profile_context", return_value=""),
            patch("jobpilot.core.llm_client.complete") as mock_complete,
        ):
            result = matcher.match("What do you bring to this team?")

        assert result.answer is None
        mock_complete.assert_not_called()

    @patch(
        "jobpilot.core.llm_client.complete",
        return_value="Tailored answer: I want this role because Acme ships fast.",
    )
    def test_enrichment_routes_through_llm_client(self, _complete, tmp_path):
        matcher = QuestionMatcher(data_dir=tmp_path)
        matcher.add_template("Why are you interested in this role?", "Because it fits.")

        result = matcher.match_with_context(
            "Why are you interested in this role?",
            jd_summary="Acme Health, Operations Coordinator, remote.",
        )

        assert result.answer == "I want this role because Acme ships fast."

    @patch("jobpilot.core.llm_client.complete", side_effect=LLMUnavailable("down"))
    def test_enrichment_failure_returns_template_answer(self, _complete, tmp_path):
        matcher = QuestionMatcher(data_dir=tmp_path)
        matcher.add_template("Why are you interested in this role?", "Because it fits.")

        result = matcher.match_with_context(
            "Why are you interested in this role?",
            jd_summary="Acme Health, Operations Coordinator, remote.",
        )

        assert result.answer == "Because it fits."


# ---------------------------------------------------------------------------
# Cover letter generator
# ---------------------------------------------------------------------------

LETTER = (
    "Dear Hiring Manager,\n\n"
    "I am writing to apply for the Operations Coordinator role at Acme Health. "
    "My seven years of scheduling and operations work map directly to your needs.\n\n"
    "Sincerely, Riley Nguyen"
)


class TestCoverLetterRouting:
    @pytest.fixture(autouse=True)
    def _isolated_cache(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cover_letter_gen, "DATA_DIR", tmp_path / "cover_letters")

    @patch("jobpilot.core.bro_client.is_bro_running", return_value=False)
    @patch("jobpilot.core.llm_client.complete", return_value=LETTER)
    @patch("jobpilot.core.llm_client.is_available", return_value=True)
    def test_generates_without_bro(self, _avail, mock_complete, _bro):
        letter = cover_letter_gen.generate_cover_letter(
            jd_title="Operations Coordinator",
            jd_company="Acme Health",
            jd_requirements=["Scheduling"],
            jd_raw_text="Operations Coordinator at Acme Health. Scheduling required.",
            candidate_name="Riley Nguyen",
        )

        assert letter == LETTER
        assert mock_complete.call_args[1]["smart"] is True
        cached = list((cover_letter_gen.DATA_DIR).glob("*.txt"))
        assert len(cached) == 1

    @patch("jobpilot.core.llm_client.is_available", return_value=False)
    def test_returns_none_without_any_backend(self, _avail):
        letter = cover_letter_gen.generate_cover_letter(
            jd_title="Operations Coordinator",
            jd_company="Acme Health",
            jd_requirements=[],
            jd_raw_text="unique-no-backend-jd-text",
        )
        assert letter is None

    @patch("jobpilot.core.bro_client.is_bro_running", return_value=False)
    @patch("jobpilot.core.llm_client.complete", side_effect=LLMUnavailable("down"))
    @patch("jobpilot.core.llm_client.is_available", return_value=True)
    def test_backend_failure_returns_none(self, _avail, _complete, _bro):
        letter = cover_letter_gen.generate_cover_letter(
            jd_title="Operations Coordinator",
            jd_company="Acme Health",
            jd_requirements=[],
            jd_raw_text="unique-backend-down-jd-text",
        )
        assert letter is None
