import pytest

from jobpilot.engine.adapters.rules_fit import RulesFit
from jobpilot.engine.adapters.sqlite_ledger import SQLiteLedger
from jobpilot.engine.domain import (
    SCREEN,
    Assessment,
    BoardResult,
    BoardTarget,
    Event,
    EventKind,
    LevelRules,
    Listing,
    LocationRules,
    Posting,
    Remote,
    Rules,
    Settings,
    Targets,
    Workplace,
    screen,
)
from jobpilot.engine.service.fit import assess_fits
from jobpilot.engine.service.ranking import rank_opportunities
from jobpilot.engine.service.refresh import refresh

T1 = "2026-09-27T12:00:00+00:00"
ME = Settings(
    targets=Targets(
        titles=("Forward Deployed Engineer", "Solutions Engineer"),
        skills=("Python", "Customer support"),
    ),
    rules=Rules(
        location=LocationRules(home=("Austin, TX",), remote=Remote.US),
        level=LevelRules(max_years_required=3),
    ),
)


@pytest.fixture
def ledger(tmp_path):
    with SQLiteLedger.in_data_dir(tmp_path) as opened:
        yield opened


def add_role(ledger, job_id, title, description, *, fit_it=True):
    listing = Listing(
        "Acme",
        title,
        url=f"https://jobs.ashbyhq.com/acme/{job_id}",
        provider="ashby",
        tenant="acme",
        provider_job_id=job_id,
    )
    opportunity = ledger.upsert(listing, seen_at=T1)
    posting = Posting(listing, description, Workplace.REMOTE, ("United States",))
    ledger.save_posting(opportunity.id, posting, fetched_at=T1)
    ledger.record_assessment(
        Assessment(
            opportunity.id,
            SCREEN,
            "screen/1",
            T1,
            screen(posting, ME).to_dict(),
            basis=job_id,
        )
    )
    return opportunity


def test_best_fit_first(ledger):
    add_role(
        ledger,
        "1",
        "Solutions Engineer",
        "Requirements:\n- Python experience\n- Customer support experience\n",
    )
    add_role(ledger, "2", "Payroll Manager", "Requirements:\n- Python experience\n")
    assess_fits(ledger, RulesFit(), ME, now=T1)
    rows = rank_opportunities(ledger)
    assert rows[0].opportunity.provider_job_id == "1"
    assert rows[0].score > rows[1].score


def test_unjudged_role_sorts_below_every_judged_role(ledger):
    add_role(ledger, "1", "Payroll Manager", "Requirements:\n- Payroll experience\n")
    add_role(
        ledger, "2", "Forward Deployed Engineer", "Requirements:\n- Python experience\n"
    )
    assess_fits(ledger, RulesFit(), ME, now=T1)
    rows = rank_opportunities(ledger)
    assert rows[0].judged and rows[0].score > 0
    # Both were judged above; add a fresh unjudged role and re-rank.
    add_role(
        ledger, "3", "Forward Deployed Engineer", "Requirements:\n- Python experience\n"
    )
    rows = rank_opportunities(ledger)
    assert rows[-1].opportunity.provider_job_id == "3"
    assert not rows[-1].judged and rows[-1].score == 0.0


def test_failed_and_applied_roles_are_never_ranked(ledger):
    add_role(
        ledger,
        "1",
        "Solutions Engineer",
        "Requirements:\n- 5+ years of experience\n- Python experience\n",
    )
    applied = add_role(
        ledger, "2", "Solutions Engineer", "Requirements:\n- Python experience\n"
    )
    ledger.append(Event(applied.id, EventKind.APPLIED, T1, "user"))
    assess_fits(ledger, RulesFit(), ME, now=T1)
    assert rank_opportunities(ledger) == []


def test_limit_keeps_the_best(ledger):
    add_role(ledger, "1", "Payroll Manager", "Requirements:\n- Payroll experience\n")
    add_role(
        ledger,
        "2",
        "Forward Deployed Engineer",
        "Requirements:\n- Python experience\n- Customer support experience\n",
    )
    assess_fits(ledger, RulesFit(), ME, now=T1)
    rows = rank_opportunities(ledger, limit=1)
    assert [row.opportunity.provider_job_id for row in rows] == ["2"]


class FakeBoards:
    def __init__(self, *postings):
        self.postings = postings

    def fetch(self, target):
        return BoardResult(target, tuple(self.postings))


def test_hand_check_2026_09_25(ledger):
    """The scan that ranked ten copies of the wrong role above the real fit.

    On 2026-09-25 the fresh scan's top 10 were all the same on-site role in
    cities outside the allowed locations, each asking for 5+ years, and the
    best real fit ranked 13th. With screening, fit, and ranking together,
    those roles are excluded and the matching remote role is first.
    """
    board = BoardTarget("ashby", "gamma", "Gamma")
    wrong = [
        Posting(
            Listing(
                f"Vendor {index}",
                "Field Service Technician",
                url=f"https://jobs.ashbyhq.com/gamma/wrong-{index}",
                provider="ashby",
                tenant="gamma",
                provider_job_id=f"wrong-{index}",
            ),
            "Requirements:\n- 5+ years of experience repairing equipment\n",
            Workplace.ONSITE,
            ("Denver, CO",),
        )
        for index in range(10)
    ]
    right = Posting(
        Listing(
            "Gamma",
            "Forward Deployed Engineer",
            url="https://jobs.ashbyhq.com/gamma/right",
            provider="ashby",
            tenant="gamma",
            provider_job_id="right",
        ),
        "Requirements:\n- Python experience\n- Customer support experience\n",
        Workplace.REMOTE,
        ("United States",),
    )
    refresh(ledger, FakeBoards(*wrong, right), [board], ME, now=T1)
    assess_fits(ledger, RulesFit(), ME, now=T1)

    rows = rank_opportunities(ledger)
    assert [row.opportunity.provider_job_id for row in rows] == ["right"]
    assert rows[0].fit is not None and rows[0].fit.tier.value == "strong"
