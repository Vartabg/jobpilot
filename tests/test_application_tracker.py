"""
Tests for ApplicationTracker — SQLite-backed job application deduplication and tracking.
"""

import pytest
from pathlib import Path
from jobpilot.core.application_tracker import ApplicationTracker


@pytest.fixture()
def tracker(tmp_path: Path) -> ApplicationTracker:
    return ApplicationTracker(data_dir=tmp_path)


class TestMarkAndQuery:

    def test_mark_started(self, tracker: ApplicationTracker):
        tracker.mark_started("https://linkedin.com/jobs/view/123", "SWE", "Acme")
        assert tracker.has_applied("https://linkedin.com/jobs/view/123") is True
        assert tracker.get_status("https://linkedin.com/jobs/view/123") == "started"

    def test_has_applied_false_for_unknown(self, tracker: ApplicationTracker):
        assert tracker.has_applied("https://example.com/unknown") is False

    def test_mark_submitted(self, tracker: ApplicationTracker):
        tracker.mark_started("https://linkedin.com/jobs/view/1", "SWE", "Acme")
        tracker.mark_submitted("https://linkedin.com/jobs/view/1")
        assert tracker.get_status("https://linkedin.com/jobs/view/1") == "submitted"

    def test_mark_abandoned(self, tracker: ApplicationTracker):
        tracker.mark_started("https://linkedin.com/jobs/view/2", "PM", "Corp")
        tracker.mark_abandoned("https://linkedin.com/jobs/view/2", step_reached=3)
        assert tracker.get_status("https://linkedin.com/jobs/view/2") == "abandoned"

    def test_url_normalization_strips_query(self, tracker: ApplicationTracker):
        tracker.mark_started("https://linkedin.com/jobs/view/99?ref=feed", "Job", "Co")
        assert tracker.has_applied("https://linkedin.com/jobs/view/99") is True

    def test_duplicate_start_updates(self, tracker: ApplicationTracker):
        tracker.mark_started("https://linkedin.com/jobs/view/5", "Old Title", "Old Co")
        tracker.mark_submitted("https://linkedin.com/jobs/view/5")
        tracker.mark_started("https://linkedin.com/jobs/view/5", "New Title", "New Co")
        assert tracker.get_status("https://linkedin.com/jobs/view/5") == "started"


class TestRecent:

    def test_get_recent(self, tracker: ApplicationTracker):
        tracker.mark_started("https://job/1", "Job A", "Co A")
        tracker.mark_started("https://job/2", "Job B", "Co B")
        recent = tracker.get_recent(limit=10)
        assert len(recent) == 2

    def test_get_recent_respects_limit(self, tracker: ApplicationTracker):
        for i in range(5):
            tracker.mark_started(f"https://job/{i}", f"Job {i}", "Co")
        recent = tracker.get_recent(limit=3)
        assert len(recent) == 3


class TestStats:

    def test_empty_stats(self, tracker: ApplicationTracker):
        stats = tracker.get_stats()
        assert stats["total"] == 0
        assert stats["submitted"] == 0

    def test_stats_counts(self, tracker: ApplicationTracker):
        tracker.mark_started("https://job/1", "A")
        tracker.mark_submitted("https://job/1")
        tracker.mark_started("https://job/2", "B")
        tracker.mark_abandoned("https://job/2")
        tracker.mark_started("https://job/3", "C")

        stats = tracker.get_stats()
        assert stats["total"] == 3
        assert stats["submitted"] == 1
        assert stats["abandoned"] == 1
        assert stats["in_progress"] == 1


class TestLifecycle:

    def test_close_and_reopen(self, tmp_path: Path):
        t1 = ApplicationTracker(data_dir=tmp_path)
        t1.mark_started("https://job/x", "Test")
        t1.close()

        t2 = ApplicationTracker(data_dir=tmp_path)
        assert t2.has_applied("https://job/x") is True
        t2.close()
