import pytest

from jobpilot.engine.adapters.sqlite_ledger import SQLiteLedger
from jobpilot.engine.domain import (
    SCREEN,
    BoardResult,
    BoardTarget,
    EventKind,
    Lane,
    Listing,
    LocationRules,
    Posting,
    Remote,
    Rules,
    Screening,
    Settings,
    Status,
    Workplace,
)
from jobpilot.engine.service.refresh import refresh

T1, T2, T3 = (
    "2026-09-26T12:00:00+00:00",
    "2026-09-27T12:00:00+00:00",
    "2026-09-28T12:00:00+00:00",
)
BOARD = BoardTarget("ashby", "gamma", "Gamma")
SETTINGS = Settings(
    rules=Rules(location=LocationRules(home=("Austin, TX",), remote=Remote.US))
)


def posting(
    job_id, title="Engineer", locations=("United States",), workplace=Workplace.REMOTE
):
    listing = Listing(
        "Gamma",
        title,
        url=f"https://jobs.ashbyhq.com/gamma/{job_id}",
        provider="ashby",
        tenant="gamma",
        provider_job_id=job_id,
    )
    return Posting(
        listing, "Requirements:\n- 2+ years of experience", workplace, locations
    )


class FakeBoards:
    def __init__(self, *postings, error=""):
        self.postings, self.error = postings, error

    def fetch(self, target):
        return BoardResult(target, tuple(self.postings), self.error)


@pytest.fixture
def ledger(tmp_path):
    with SQLiteLedger.in_data_dir(tmp_path) as opened:
        yield opened


def by_id(ledger):
    return {o.provider_job_id: o for o in ledger.opportunities(Lane.JOB)}


def test_new_postings_are_discovered_stored_and_screened(ledger):
    boards = FakeBoards(
        posting("a"), posting("b", locations=("London",), workplace=Workplace.ONSITE)
    )
    report = refresh(ledger, boards, [BOARD], SETTINGS, now=T1)

    assert (report.postings, report.new_roles, report.passed, report.failed) == (
        2,
        2,
        1,
        1,
    )
    assert report.failures_by_rule == {"location": 1}
    roles = by_id(ledger)
    assert [e.kind for e in ledger.events(roles["a"].id)] == [EventKind.DISCOVERED]
    assert ledger.posting(roles["a"].id).description.startswith("Requirements")
    failed = Screening.from_dict(ledger.latest_assessment(roles["b"].id, SCREEN).result)
    assert [check.rule for check in failed.failures] == ["location"]


def test_repeat_refreshes_add_nothing_new(ledger):
    refresh(ledger, FakeBoards(posting("a")), [BOARD], SETTINGS, now=T1)
    report = refresh(ledger, FakeBoards(posting("a")), [BOARD], SETTINGS, now=T2)
    role = by_id(ledger)["a"]
    assert report.new_roles == 0
    assert len(ledger.events(role.id)) == 1
    assert (
        ledger._db.execute("SELECT COUNT(*) FROM assessments").fetchone()[0] == 1
    )  # same inputs


def test_changed_rules_are_screened_again(ledger):
    refresh(ledger, FakeBoards(posting("a")), [BOARD], SETTINGS, now=T1)
    homebody = Settings(
        rules=Rules(location=LocationRules(home=("Austin, TX",), remote=Remote.NONE))
    )
    report = refresh(ledger, FakeBoards(posting("a")), [BOARD], homebody, now=T2)
    assert report.failed == 1
    assert ledger._db.execute("SELECT COUNT(*) FROM assessments").fetchone()[0] == 2


def test_roles_that_disappear_close_and_reopen(ledger):
    refresh(ledger, FakeBoards(posting("a"), posting("b")), [BOARD], SETTINGS, now=T1)
    report = refresh(ledger, FakeBoards(posting("a")), [BOARD], SETTINGS, now=T2)
    assert report.closed == 1
    assert ledger.status(by_id(ledger)["b"].id) is Status.CLOSED

    report = refresh(
        ledger, FakeBoards(posting("a"), posting("b")), [BOARD], SETTINGS, now=T3
    )
    assert report.reopened == 1
    assert ledger.status(by_id(ledger)["b"].id) is Status.NEW


def test_a_failing_board_changes_nothing(ledger):
    refresh(ledger, FakeBoards(posting("a")), [BOARD], SETTINGS, now=T1)
    report = refresh(
        ledger,
        FakeBoards(error="the board answered HTTP 500"),
        [BOARD],
        SETTINGS,
        now=T2,
    )
    assert report.failed_boards == ["ashby:gamma: the board answered HTTP 500"]
    assert report.closed == 0
    assert ledger.status(by_id(ledger)["a"].id) is Status.NEW


def test_applied_roles_are_never_closed(ledger):
    from jobpilot.engine.domain import Event

    refresh(ledger, FakeBoards(posting("a")), [BOARD], SETTINGS, now=T1)
    role = by_id(ledger)["a"]
    ledger.append(Event(role.id, EventKind.APPLIED, T1, "user"))
    report = refresh(ledger, FakeBoards(), [BOARD], SETTINGS, now=T2)
    assert report.closed == 0
    assert ledger.status(role.id) is Status.APPLIED


def test_other_boards_roles_are_left_alone(ledger):
    refresh(ledger, FakeBoards(posting("a")), [BOARD], SETTINGS, now=T1)
    report = refresh(
        ledger, FakeBoards(), [BoardTarget("ashby", "delta")], SETTINGS, now=T2
    )
    assert report.closed == 0
