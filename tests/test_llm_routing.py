"""
Tests the boundary between advisory AI and deterministic external drafts.

Bro is optional: with the local stack down, any configured backend (e.g.
Gemini) may power job-fit advice and question fallback/enrichment. Application
answers and cover letters remain deterministic. All llm_client calls are
mocked, so these tests perform no network access.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from jobpilot.core import cover_letter_gen
from jobpilot.core.application_answerer import ApplicationAnswerer
from jobpilot.core.job_scorer import JobScorer
from jobpilot.core.llm_client import LLMUnavailable
from jobpilot.core.profile_store import UserProfile
from jobpilot.core.question_matcher import QuestionMatcher

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
    def test_external_answer_draft_stays_account_grounded(self, tmp_path):
        draft = _answerer(tmp_path).draft(
            "Why are you interested in this role?",
            jd_text="Coordinate scheduling and operations for a busy clinic.",
            title="Operations Coordinator",
        )

        assert draft.source == "account_grounded"
        assert "clinic" in draft.answer.lower() or "scheduling" in draft.answer.lower()

    def test_answer_draft_does_not_depend_on_backend(self, tmp_path):
        draft = _answerer(tmp_path).draft(
            "Why are you interested in this role?",
            jd_text="Coordinate scheduling and operations for a busy clinic.",
            title="Operations Coordinator",
        )

        assert draft.source == "account_grounded"
        assert draft.answer  # account-grounded fallback still drafts
        assert any("true accounts" in warning for warning in draft.warnings)


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
# Cover letter truth boundary
# ---------------------------------------------------------------------------

LETTER = (
    "Dear Hiring Manager,\n\n"
    "I am writing to apply for the Operations Coordinator role at Acme Health. "
    "My seven years of scheduling and operations work map directly to your needs.\n\n"
    "Sincerely, Riley Nguyen"
)


class TestCoverLetterTruthBoundary:
    @pytest.fixture(autouse=True)
    def _isolated_cache(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cover_letter_gen, "DATA_DIR", tmp_path / "cover_letters")

    @patch("jobpilot.core.llm_client.complete", return_value=LETTER)
    def test_generates_without_using_model_prose(self, mock_complete):
        letter = cover_letter_gen.generate_cover_letter(
            jd_title="Operations Coordinator",
            jd_company="Acme Health",
            jd_requirements=["Scheduling"],
            jd_raw_text="Operations Coordinator at Acme Health. Scheduling required.",
            candidate_name="Riley Nguyen",
        )

        assert "Operations Coordinator" in letter
        assert "Acme Health" in letter
        assert "Riley Nguyen" in letter
        assert "seven years" not in letter.lower()
        mock_complete.assert_not_called()
        cached = list((cover_letter_gen.DATA_DIR).glob("*.txt"))
        assert len(cached) == 1

    def test_generates_without_any_backend(self):
        letter = cover_letter_gen.generate_cover_letter(
            jd_title="Operations Coordinator",
            jd_company="Acme Health",
            jd_requirements=[],
            jd_raw_text="unique-no-backend-jd-text",
        )
        assert "preparation draft" in letter.lower()

    @patch("jobpilot.core.llm_client.complete", side_effect=LLMUnavailable("down"))
    def test_backend_is_never_called(self, complete):
        letter = cover_letter_gen.generate_cover_letter(
            jd_title="Operations Coordinator",
            jd_company="Acme Health",
            jd_requirements=[],
            jd_raw_text="unique-backend-down-jd-text",
        )
        assert letter
        complete.assert_not_called()
