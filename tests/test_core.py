"""
Tests for question_matcher.py — fuzzy matching, normalization, and template management.
"""

import json
import tempfile
from pathlib import Path

import pytest
from jobpilot.core.question_matcher import (
    QuestionMatcher,
    MatchResult,
)
from jobpilot.learning.learning_db import LearningDB, _reset_learning_db


@pytest.fixture(autouse=True)
def _reset_db_singleton():
    """Reset the learning DB singleton cache between tests."""
    _reset_learning_db()
    yield
    _reset_learning_db()


@pytest.fixture
def templates_dir(tmp_path: Path):
    """Provide a temp directory with sample templates seeded into SQLite."""
    db = LearningDB(tmp_path / "learning.db")
    db.upsert_template("Are you located in New York City?", "Yes")
    db.upsert_template("Are you open to hybrid work?", "Yes")
    db.upsert_template("Do you require visa sponsorship?", "No")
    db.upsert_template("Are you authorized to work in the United States?", "Yes")
    db.close()
    return tmp_path


@pytest.fixture
def matcher(templates_dir: Path):
    return QuestionMatcher(data_dir=templates_dir)


class TestExactMatch:
    """Tests for exact question matching."""

    def test_exact_match_returns_answer(self, matcher: QuestionMatcher):
        result = matcher.match("Are you located in New York City?")
        assert result.answer == "Yes"
        assert result.confidence >= 0.9

    def test_exact_match_case_insensitive(self, matcher: QuestionMatcher):
        result = matcher.match("are you located in new york city?")
        assert result.answer == "Yes"


class TestFuzzyMatch:
    """Tests for fuzzy string matching."""

    def test_close_match_found(self, matcher: QuestionMatcher):
        result = matcher.match("Are you currently located in NYC?")
        assert result.answer is not None
        assert result.confidence > 0.5

    def test_no_match_for_unrelated_question(self, matcher: QuestionMatcher):
        result = matcher.match("What is your favorite color?")
        assert result.answer is None or result.confidence < 0.5


class TestNormalization:
    """Tests for question text normalization."""

    def test_whitespace_handling(self, matcher: QuestionMatcher):
        result = matcher.match("  Are you located in New York City?  ")
        assert result.answer == "Yes"


class TestTemplatePersistence:
    """Tests for learning/saving new templates."""

    def test_add_template(self, matcher: QuestionMatcher, templates_dir: Path):
        matcher.add_template("Do you have a security clearance?", "No")
        # Reload templates
        new_matcher = QuestionMatcher(data_dir=templates_dir)
        result = new_matcher.match("Do you have a security clearance?")
        assert result.answer == "No"


# ===================================================================
# Profile Store Tests
# ===================================================================

from jobpilot.core.profile_store import ProfileStore, UserProfile


@pytest.fixture
def profile_dir(tmp_path: Path):
    """Create a temp dir with a sample profile."""
    profile_data = {
        "first_name": "Test",
        "last_name": "User",
        "email": "test@example.com",
        "phone": "555-1234",
        "city": "San Francisco",
        "state": "CA",
        "country": "United States",
        "zip_code": "94102",
        "linkedin_url": "",
        "portfolio_url": "",
        "github_url": "",
        "resume_path": "",
        "authorized_to_work": True,
        "requires_sponsorship": False,
        "years_of_experience": 5,
        "current_title": "Engineer",
        "current_company": "TestCo",
        "desired_salary": "",
        "custom_answers": {},
    }
    (tmp_path / "profile.json").write_text(json.dumps(profile_data))
    return tmp_path


@pytest.fixture
def store(profile_dir: Path):
    return ProfileStore(data_dir=profile_dir)


class TestProfileLoad:
    def test_load_returns_profile(self, store: ProfileStore):
        profile = store.load()
        assert isinstance(profile, UserProfile)
        assert profile.first_name == "Test"
        assert profile.email == "test@example.com"

    def test_load_missing_file_returns_default(self, tmp_path: Path):
        store = ProfileStore(data_dir=tmp_path)
        profile = store.load()
        assert isinstance(profile, UserProfile)
        assert profile.first_name == ""


class TestProfileSave:
    def test_save_persists(self, store: ProfileStore, profile_dir: Path):
        profile = store.load()
        profile.first_name = "Modified"
        store.save(profile)

        raw = json.loads((profile_dir / "profile.json").read_text())
        assert raw["first_name"] == "Modified"


class TestFieldMapping:
    def test_get_known_field(self, store: ProfileStore):
        value = store.get_field_value("email")
        assert value == "test@example.com"

    def test_get_unknown_field(self, store: ProfileStore):
        value = store.get_field_value("nonexistent_field_xyz")
        assert value is None or value == ""


# ===================================================================
# Action Recorder Tests
# ===================================================================

from jobpilot.learning.action_recorder import ActionRecorder


@pytest.fixture
def recorder(tmp_path: Path):
    return ActionRecorder(data_dir=tmp_path)


class TestRecording:
    def test_record_field_approved(self, recorder: ActionRecorder, tmp_path: Path):
        recorder.record_field_approved("Email", "email", "test@example.com", 0.95)
        
        stats = recorder.get_stats()
        assert stats["fields_approved"] == 1

    def test_record_application_lifecycle(self, recorder: ActionRecorder, tmp_path: Path):
        recorder.record_application_started("https://linkedin.com/job/1", "Senior Engineer", "LinkedIn")
        recorder.record_field_approved("Name", "name", "Test", 0.9)
        recorder.record_application_submitted()
        
        stats = recorder.get_stats()
        assert stats["total_applications"] == 1
        assert stats["submitted"] == 1
        assert stats["fields_approved"] == 1

    def test_record_application_abandoned(self, recorder: ActionRecorder, tmp_path: Path):
        recorder.record_application_started("https://linkedin.com/job/2", "Manager", "LinkedIn")
        recorder.record_application_abandoned(step_number=2)
        
        stats = recorder.get_stats()
        assert stats["abandoned"] == 1


class TestStats:
    def test_empty_stats(self, recorder: ActionRecorder):
        stats = recorder.get_stats()
        assert stats["total_applications"] == 0
        assert stats["submitted"] == 0

    def test_stats_after_submissions(self, recorder: ActionRecorder):
        recorder.record_application_started("url1", "Job 1")
        recorder.record_application_submitted()
        recorder.record_application_started("url2", "Job 2")
        recorder.record_application_abandoned(step_number=1)
        
        stats = recorder.get_stats()
        assert stats["total_applications"] == 2
        assert stats["submitted"] == 1
        assert stats["abandoned"] == 1


# ===================================================================
# LinkedIn Parser Tests
# ===================================================================

from jobpilot.core.linkedin_parser import LinkedInParser, FieldType, SemanticType


class TestSemanticTypeInference:
    """Test _infer_semantic_type with known labels."""

    @pytest.fixture
    def parser(self):
        # Parser needs a page, but _infer_semantic_type doesn't use it
        class FakePage:
            pass
        return LinkedInParser(FakePage())

    @pytest.mark.parametrize("label,expected", [
        ("First name", SemanticType.FIRST_NAME),
        ("Last name", SemanticType.LAST_NAME),
        ("Email address", SemanticType.EMAIL),
        ("Phone number", SemanticType.PHONE),
        ("City", SemanticType.CITY),
        ("LinkedIn Profile URL", SemanticType.LINKEDIN_URL),
    ])
    def test_common_field_labels(self, parser, label, expected):
        semantic_type, confidence = parser._infer_semantic_type(
            label, "", FieldType.TEXT
        )
        assert semantic_type == expected
        assert confidence > 0.5

    def test_unknown_field(self, parser):
        semantic_type, confidence = parser._infer_semantic_type(
            "Random gibberish xyz", "", FieldType.TEXT
        )
        assert semantic_type == SemanticType.UNKNOWN
