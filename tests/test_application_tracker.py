"""
Tests for ApplicationTracker — SQLite-backed job application deduplication and tracking.
"""

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import jobpilot.core.application_tracker as tracker_module
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

    def test_url_normalization_preserves_indeed_role_identity(
        self, tracker: ApplicationTracker
    ):
        tracker.mark_started(
            "https://www.indeed.com/viewjob?jk=ROLE-A&utm_source=feed",
            "Engineer A",
            "Acme",
        )

        assert tracker.has_applied(
            "https://www.indeed.com/viewjob?jk=ROLE-A&from=web"
        ) is True
        assert tracker.has_applied(
            "https://www.indeed.com/viewjob?jk=ROLE-B"
        ) is False

    def test_greenhouse_alias_and_title_variant_reuse_provider_history(
        self, tracker: ApplicationTracker
    ):
        tracker.log_application(
            company="Acme",
            title="Customer Engineer (Remote)",
            url="https://job-boards.greenhouse.io/acme/jobs/123?gh_src=campaign",
            status="applied",
        )

        alias = "https://boards.greenhouse.io/acme/jobs/123"
        assert tracker.has_applied(alias) is True
        assert tracker.role_status("Acme", "Customer Engineer", alias) == "applied"

    def test_provider_identity_does_not_inherit_same_title_sibling_status(
        self, tracker: ApplicationTracker
    ):
        tracker.log_application(
            company="Acme",
            title="Customer Engineer",
            url="https://boards.greenhouse.io/acme/jobs/123",
            status="rejected",
        )

        assert tracker.role_status(
            "Acme",
            "Customer Engineer",
            "https://boards.greenhouse.io/acme/jobs/456",
        ) is None

    def test_duplicate_start_updates(self, tracker: ApplicationTracker):
        tracker.mark_started("https://linkedin.com/jobs/view/5", "Old Title", "Old Co")
        tracker.mark_submitted("https://linkedin.com/jobs/view/5")
        tracker.mark_started("https://linkedin.com/jobs/view/5", "New Title", "New Co")
        assert tracker.get_status("https://linkedin.com/jobs/view/5") == "started"

    def test_log_application_without_url_uses_synthetic_url(self, tracker: ApplicationTracker):
        app = tracker.log_application(
            company="Titan AI",
            title="Forward Deployed Engineer",
            status="applied",
            applied_at="2026-06-02",
        )

        assert app.job_url.startswith("manual://titan-ai-forward-deployed-engineer/")
        assert tracker.has_applied(app.job_url) is True
        assert tracker.company_status("Titan AI") == "applied"

    def test_log_application_idempotent_by_company_title(self, tracker: ApplicationTracker):
        tracker.log_application(
            company="Titan AI",
            title="Forward Deployed Engineer",
            status="applied",
        )
        tracker.log_application(
            company="titan ai",
            title="Forward Deployed Engineer",
            status="interview",
        )

        recent = tracker.get_recent(limit=10)
        assert len(recent) == 1
        assert recent[0].status == "interview"
        assert tracker.company_status("Titan AI") == "interview"

    def test_distinct_real_requisitions_with_same_title_keep_separate_history(
        self, tracker: ApplicationTracker
    ):
        tracker.log_application(
            company="Acme", title="Engineer",
            url="https://jobs.ashbyhq.com/acme/role-a", status="rejected",
        )
        tracker.log_application(
            company="Acme", title="Engineer",
            url="https://jobs.ashbyhq.com/acme/role-b", status="applied",
        )

        assert tracker.get_status("https://jobs.ashbyhq.com/acme/role-a") == "rejected"
        assert tracker.get_status("https://jobs.ashbyhq.com/acme/role-b") == "applied"
        assert len(tracker.get_recent(limit=10)) == 2

    def test_log_application_without_url_preserves_existing_real_url(self, tracker: ApplicationTracker):
        tracker.log_application(
            company="Titan AI",
            title="Forward Deployed Engineer",
            url="https://jobs.ashbyhq.com/titan-ai/123?utm=x",
            status="applied",
        )
        app = tracker.log_application(
            company="Titan AI",
            title="Forward Deployed Engineer",
            status="interview",
        )

        assert app.job_url == "https://jobs.ashbyhq.com/titan-ai/123"
        assert tracker.get_status("https://jobs.ashbyhq.com/titan-ai/123") == "interview"

    def test_log_application_rejects_unknown_status(self, tracker: ApplicationTracker):
        with pytest.raises(ValueError):
            tracker.log_application(company="Titan AI", status="maybe")

    @pytest.mark.parametrize(
        "status",
        ["offer", "withdrawn", "outreach", "human_reply", "screen", "no_response"],
    )
    def test_extended_status_and_source_are_persisted(
        self, tracker: ApplicationTracker, status: str
    ):
        app = tracker.log_application(
            company=f"Acme {status}", status=status, source="gmail"
        )

        stored = tracker.get_recent(limit=1)[0]
        assert app.source == "gmail"
        assert stored.status == status
        assert stored.source == "gmail"

    def test_started_source_is_persisted(self, tracker: ApplicationTracker):
        tracker.mark_started(
            "https://jobs.example.test/acme/1", "Engineer", "Acme",
            source="linkedin",
        )

        assert tracker.get_recent()[0].source == "linkedin"

    def test_role_status_does_not_inherit_sibling_rejection(
        self, tracker: ApplicationTracker
    ):
        tracker.log_application(
            company="Acme", title="Role A", url="https://acme.test/a",
            status="rejected",
        )

        assert tracker.role_status(
            "Acme", "Role B", "https://acme.test/b"
        ) is None
        assert tracker.role_status(
            "Acme", "Role A", "https://acme.test/a"
        ) == "rejected"

    def test_log_application_url_owned_by_other_row(self, tracker: ApplicationTracker):
        """Regression: re-logging with a URL another row already owns must not
        raise sqlite3.IntegrityError — the URL-owning row is the same
        application and should be updated instead."""
        # Row 1: manual application with a synthetic URL.
        tracker.log_application(company="Acme", title="Engineer", status="applied")
        # Row 2: a started application that owns the real URL.
        tracker.mark_started("https://jobs.example.com/123", "Engineer", "Acme Inc")

        # Re-log row 1's company+title, now pointing at row 2's URL.
        app = tracker.log_application(
            company="Acme",
            title="Engineer",
            url="https://jobs.example.com/123",
            status="interview",
        )

        assert app.job_url == "https://jobs.example.com/123"
        assert tracker.get_status("https://jobs.example.com/123") == "interview"
        assert tracker.company_status("Acme") == "interview"

    def test_log_application_prefers_exact_url_match(self, tracker: ApplicationTracker):
        """When both a URL match and a company+title match exist, the URL row wins."""
        tracker.log_application(company="Acme", title="Engineer", status="applied")
        tracker.mark_started("https://jobs.example.com/9", "Engineer", "Acme")

        tracker.log_application(
            company="Acme",
            title="Engineer",
            url="https://jobs.example.com/9",
            status="rejected",
        )

        assert tracker.get_status("https://jobs.example.com/9") == "rejected"
        # The synthetic-URL row was not the one updated.
        synthetic = [a for a in tracker.get_recent(limit=10) if a.job_url.startswith("manual://")]
        assert len(synthetic) == 1
        assert synthetic[0].status == "applied"

    def test_log_application_company_title_match_uses_most_recent(self, tracker: ApplicationTracker):
        """With multiple company+title rows, the most recently updated one is updated."""
        tracker.mark_started("https://jobs.example.com/old", "Engineer", "Acme")
        tracker.mark_started("https://jobs.example.com/new", "Engineer", "Acme")

        app = tracker.log_application(company="Acme", title="Engineer", status="applied")

        assert app.job_url == "https://jobs.example.com/new"
        assert tracker.get_status("https://jobs.example.com/new") == "applied"
        assert tracker.get_status("https://jobs.example.com/old") == "started"


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

    def test_stats_include_organizer_statuses(self, tracker: ApplicationTracker):
        tracker.log_application("A", status="applied")
        tracker.log_application("B", status="rejected")
        tracker.log_application("C", status="interview")

        stats = tracker.get_stats()
        assert stats["applied"] == 1
        assert stats["rejected"] == 1
        assert stats["interview"] == 1
        assert tracker.get_status_counts() == {
            "applied": 1,
            "interview": 1,
            "rejected": 1,
        }


class TestLifecycle:

    def test_close_and_reopen(self, tmp_path: Path):
        t1 = ApplicationTracker(data_dir=tmp_path)
        t1.mark_started("https://job/x", "Test")
        t1.close()

        t2 = ApplicationTracker(data_dir=tmp_path)
        assert t2.has_applied("https://job/x") is True
        t2.close()

    def test_global_accessor_uses_thread_local_sqlite_connections(
        self, monkeypatch, tmp_path: Path
    ):
        monkeypatch.setattr(tracker_module, "DB_DIR", tmp_path)
        monkeypatch.setattr(tracker_module, "_tracker_state", threading.local())
        local_tracker = tracker_module.get_application_tracker()
        local_tracker.log_application("Acme", "Engineer", status="applied")

        with ThreadPoolExecutor(max_workers=1) as executor:
            worker_total = executor.submit(
                lambda: tracker_module.get_application_tracker().get_stats()["total"]
            ).result()

        assert worker_total == 1
