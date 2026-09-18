"""Regression tests for the review-pass fixes re-applied on top of current main.

Covers: salary/experience/title heuristics (job_scorer), queue company-dedup
and schema-drift tolerance (queue_builder).
"""

import json

from jobpilot.core import queue_builder
from jobpilot.core.job_scorer import JobScorer
from jobpilot.core.queue_builder import QueueJob, load_queue, tracker_status_for_job


class TestSalaryHeuristic:
    def test_ignores_non_comp_dollar_amounts(self):
        assert JobScorer._extract_salary("We raised $5M in Series A funding.") == ""
        assert JobScorer._extract_salary("A $20 gift card for referrals.") == ""
        assert JobScorer._extract_salary("Just a $5 application fee.") == ""

    def test_extracts_real_comp(self):
        assert JobScorer._extract_salary(
            "Comp: $120,000 - $150,000 per year"
        ).startswith("$120,000")
        assert JobScorer._extract_salary("Pay is $50/hr").startswith("$50")
        assert JobScorer._extract_salary("Base $120k-$160k").startswith("$120k")


class TestExperienceHeuristic:
    def test_ignores_non_requirement_years(self):
        _, risk = JobScorer._score_experience(
            3, "The company was founded 20 years ago."
        )
        assert risk == ""
        _, risk = JobScorer._score_experience(
            3, "Unlimited PTO after 5 years of tenure."
        )
        assert risk == ""

    def test_reads_real_requirement(self):
        _, risk = JobScorer._score_experience(
            2, "We require 7+ years of experience in Python."
        )
        assert "7" in risk


class TestTitleHeuristic:
    def test_skips_nav_and_urls(self):
        text = "Apply now\nhttps://boards.greenhouse.io/acme\nSenior Solutions Engineer\nAcme Inc"
        assert JobScorer._guess_title(text) == "Senior Solutions Engineer"


class _FakeTracker:
    def __init__(self, applied=(), company_statuses=None):
        self._applied = set(applied)
        self._company = company_statuses or {}

    def has_applied(self, url):
        return url in self._applied

    def get_status(self, url):
        return "applied" if url in self._applied else None

    def company_status(self, company):
        return self._company.get(company)


class TestQueueCompanyDedup:
    def test_new_role_at_applied_company_is_not_hidden(self):
        # Applied to a DIFFERENT role at Acme; a new Acme URL must stay actionable.
        tracker = _FakeTracker(company_statuses={"Acme": "applied"})
        assert tracker_status_for_job(tracker, "https://acme/new-role", "Acme") is None

    def test_rejected_company_marks_dead_ground(self):
        tracker = _FakeTracker(company_statuses={"Acme": "rejected"})
        assert (
            tracker_status_for_job(tracker, "https://acme/new-role", "Acme")
            == "rejected"
        )

    def test_directly_applied_url_still_flagged(self):
        tracker = _FakeTracker(applied={"https://acme/role-a"})
        assert (
            tracker_status_for_job(tracker, "https://acme/role-a", "Acme") == "applied"
        )


class TestQueueSchemaDrift:
    def test_load_queue_tolerates_unknown_keys(self, tmp_path, monkeypatch):
        # A queue.json written by a newer version with an extra field must not
        # crash the load (which would wipe applied/skipped history).
        row = {
            "id": "abc123",
            "company": "Acme",
            "title": "Engineer",
            "url": "https://acme/role",
            "location": "Remote",
            "portal": "greenhouse",
            "track": "tech",
            "fit_score": 80,
            "keywords": ["python"],
            "status": "applied",
            "future_field_from_a_newer_version": "boom",
        }
        qpath = tmp_path / "queue.json"
        qpath.write_text(json.dumps([row]))
        monkeypatch.setattr(queue_builder, "QUEUE_PATH", qpath)

        jobs = load_queue()
        assert len(jobs) == 1
        assert isinstance(jobs[0], QueueJob)
        assert jobs[0].status == "applied"
