"""
Tests for LearningDB — SQLite-backed template and action storage.

All tests use a temporary database via tmp_path so nothing persists.
"""

from __future__ import annotations

import pytest
from pathlib import Path

from jobpilot.learning.learning_db import LearningDB


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path: Path) -> LearningDB:
    """Fresh in-memory-ish LearningDB in a temp dir."""
    return LearningDB(tmp_path / "test_learning.db")


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

class TestTemplates:

    def test_upsert_and_get(self, db: LearningDB):
        db.upsert_template("Why interested?", "I love this role")
        assert db.get_template("Why interested?") == "I love this role"

    def test_get_nonexistent_returns_none(self, db: LearningDB):
        assert db.get_template("Does not exist") is None

    def test_upsert_updates_existing(self, db: LearningDB):
        db.upsert_template("Q", "Answer v1")
        db.upsert_template("Q", "Answer v2")
        assert db.get_template("Q") == "Answer v2"

    def test_delete_template(self, db: LearningDB):
        db.upsert_template("Q", "A")
        assert db.delete_template("Q") is True
        assert db.get_template("Q") is None

    def test_delete_nonexistent_returns_false(self, db: LearningDB):
        assert db.delete_template("Ghost") is False

    def test_get_all_templates(self, db: LearningDB):
        db.upsert_template("Q1", "A1")
        db.upsert_template("Q2", "A2")
        all_t = db.get_all_templates()
        assert all_t == {"Q1": "A1", "Q2": "A2"}

    def test_get_all_empty(self, db: LearningDB):
        assert db.get_all_templates() == {}

    def test_increment_usage(self, db: LearningDB):
        db.upsert_template("Q", "A")
        db.increment_usage("Q")
        db.increment_usage("Q")
        # Verify via raw SQL
        row = db._conn.execute(
            "SELECT times_used FROM templates WHERE question = ?", ("Q",)
        ).fetchone()
        assert row["times_used"] == 2


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

class TestActions:

    def test_record_and_get_stats(self, db: LearningDB):
        db.record_action(action_type="app_started", job_url="https://job/1")
        db.record_action(action_type="field_approved", field_label="Name")
        db.record_action(action_type="field_edited", field_label="Phone")
        db.record_action(action_type="app_submitted")

        stats = db.get_stats()
        assert stats["total_applications"] == 1
        assert stats["submitted"] == 1
        assert stats["fields_approved"] == 1
        assert stats["fields_edited"] == 1
        assert stats["approval_rate"] == 0.5  # 1 approved / 2 total

    def test_empty_stats(self, db: LearningDB):
        stats = db.get_stats()
        assert stats["total_applications"] == 0
        assert stats["approval_rate"] == 0.0

    def test_record_returns_row_id(self, db: LearningDB):
        id1 = db.record_action(action_type="app_started")
        id2 = db.record_action(action_type="app_started")
        assert id2 == id1 + 1

    def test_get_recent_actions(self, db: LearningDB):
        db.record_action(action_type="app_started", job_url="https://job/1")
        db.record_action(action_type="field_approved", field_label="Name")
        db.record_action(action_type="app_submitted")

        recent = db.get_recent_actions(limit=2)
        assert len(recent) == 2
        assert recent[0]["action_type"] == "app_submitted"  # newest first

    def test_record_preserves_all_fields(self, db: LearningDB):
        db.record_action(
            action_type="field_approved",
            timestamp="2026-01-01T00:00:00",
            job_url="https://job/1",
            job_title="Senior Dev",
            company="Acme",
            field_label="Name",
            field_type="text",
            suggested_value="Alex",
            final_value="Alex",
            confidence=0.95,
            time_spent_ms=1200,
            step_number=2,
        )
        recent = db.get_recent_actions(limit=1)
        row = recent[0]
        assert row["job_title"] == "Senior Dev"
        assert row["confidence"] == 0.95
        assert row["step_number"] == 2


# ---------------------------------------------------------------------------
# Advanced queries
# ---------------------------------------------------------------------------

class TestAdvancedQueries:

    def test_approval_rate_by_field(self, db: LearningDB):
        # Name: 2 approved, 1 edited => 66%
        db.record_action(action_type="field_approved", field_label="Name")
        db.record_action(action_type="field_approved", field_label="Name")
        db.record_action(action_type="field_edited", field_label="Name")
        # Email: 1 approved => 100%
        db.record_action(action_type="field_approved", field_label="Email")

        rates = db.approval_rate_by_field()
        assert abs(rates["Name"] - 2/3) < 0.01
        assert rates["Email"] == 1.0

    def test_low_confidence_templates(self, db: LearningDB):
        db.record_action(
            action_type="field_approved",
            field_label="Why?",
            suggested_value="I like it",
            confidence=0.4,
        )
        db.record_action(
            action_type="field_approved",
            field_label="Salary",
            suggested_value="100k",
            confidence=0.9,
        )
        low = db.low_confidence_templates(threshold=0.6)
        assert len(low) == 1
        assert low[0]["field_label"] == "Why?"

    def test_low_confidence_empty(self, db: LearningDB):
        assert db.low_confidence_templates() == []


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

class TestLifecycle:

    def test_close_and_reopen(self, tmp_path: Path):
        db1 = LearningDB(tmp_path / "test.db")
        db1.upsert_template("Q", "A")
        db1.record_action(action_type="app_started")
        db1.close()

        # Reopen same file
        db2 = LearningDB(tmp_path / "test.db")
        assert db2.get_template("Q") == "A"
        assert db2.get_stats()["total_applications"] == 1
        db2.close()
