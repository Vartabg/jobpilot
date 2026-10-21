import pytest

from jobpilot.engine.adapters.rules_fit import RulesFit
from jobpilot.engine.adapters.sqlite_ledger import SQLiteLedger
from jobpilot.engine.domain import (
    SCREEN,
    Assessment,
    Event,
    EventKind,
    Listing,
    Posting,
    Settings,
    Targets,
    Workplace,
)
from jobpilot.engine.domain.fit import FIT, Fit, ModelError, Tier
from jobpilot.engine.service.fit import assess_fits

T1, T2 = "2026-09-27T12:00:00+00:00", "2026-09-28T12:00:00+00:00"
ME = Settings(
    targets=Targets(
        titles=("Forward Deployed Engineer", "Solutions Engineer"), skills=("Python",)
    )
)


@pytest.fixture
def ledger(tmp_path):
    with SQLiteLedger.in_data_dir(tmp_path) as opened:
        yield opened


def add_role(ledger, job_id, title, *, passed=True):
    listing = Listing(
        "Acme",
        title,
        url=f"https://jobs.ashbyhq.com/acme/{job_id}",
        provider="ashby",
        tenant="acme",
        provider_job_id=job_id,
    )
    opportunity = ledger.upsert(listing, seen_at=T1)
    posting = Posting(
        listing,
        "Requirements:\n- Python experience\n",
        Workplace.REMOTE,
        ("United States",),
    )
    ledger.save_posting(opportunity.id, posting, fetched_at=T1)
    ledger.record_assessment(
        Assessment(
            opportunity.id,
            SCREEN,
            "screen/1",
            T1,
            {
                "passed": passed,
                "checks": []
                if passed
                else [
                    {
                        "rule": "location",
                        "outcome": "fail",
                        "detail": "On-site elsewhere",
                    }
                ],
            },
            basis=job_id,
        )
    )
    return opportunity


class CountingFit(RulesFit):
    def __init__(self):
        self.seen = []

    def assess(self, posting, settings):
        self.seen.append(posting.listing.title)
        return super().assess(posting, settings)


def test_only_open_roles_that_passed_screening_are_judged(ledger):
    judged = add_role(ledger, "1", "Forward Deployed Engineer")
    add_role(ledger, "2", "Solutions Engineer", passed=False)
    applied = add_role(ledger, "3", "Solutions Engineer II")
    ledger.append(Event(applied.id, EventKind.APPLIED, T1, "user"))

    report = assess_fits(ledger, RulesFit(), ME, now=T1)
    assert (report.candidates, report.assessed) == (1, 1)
    fit = Fit.from_dict(ledger.latest_assessment(judged.id, FIT).result)
    assert fit.tier is Tier.STRONG


def test_most_relevant_first_and_limit_defers_the_rest(ledger):
    add_role(ledger, "1", "Payroll Manager")
    add_role(ledger, "2", "Forward Deployed Engineer")
    checker = CountingFit()
    report = assess_fits(ledger, checker, ME, now=T1, limit=1)
    assert checker.seen == ["Forward Deployed Engineer"]
    assert (report.assessed, report.deferred) == (1, 1)


def test_unchanged_inputs_are_not_judged_again(ledger):
    add_role(ledger, "1", "Forward Deployed Engineer")
    assess_fits(ledger, RulesFit(), ME, now=T1)
    checker = CountingFit()
    report = assess_fits(ledger, checker, ME, now=T2)
    assert checker.seen == [] and report.up_to_date == 1
    assert report.by_tier == {"strong": 1}


def test_new_evidence_triggers_a_new_judgment(ledger):
    add_role(ledger, "1", "Forward Deployed Engineer")
    assess_fits(ledger, RulesFit(), ME, now=T1)
    more = Settings(targets=Targets(titles=ME.targets.titles, skills=("Python", "Go")))
    assert assess_fits(ledger, RulesFit(), more, now=T2).assessed == 1


def test_a_failing_checker_is_reported_not_fatal(ledger):
    add_role(ledger, "1", "Forward Deployed Engineer")

    class Broken:
        name = "broken"

        def assess(self, posting, settings):
            raise ModelError("offline")

    report = assess_fits(ledger, Broken(), ME, now=T1)
    assert report.assessed == 0
    assert "offline" in report.failed[0]
