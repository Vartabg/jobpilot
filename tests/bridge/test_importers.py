from types import SimpleNamespace

import pytest

from jobpilot.bridge import importers
from jobpilot.engine.adapters.sqlite_ledger import SQLiteLedger
from jobpilot.engine.domain import EventKind, Lane, Status

DEFAULT = "2026-09-01T12:00:00+00:00"
ASHBY = "https://jobs.ashbyhq.com/acme/uuid-9"


@pytest.fixture
def ledger(tmp_path):
    with SQLiteLedger.in_data_dir(tmp_path) as opened:
        yield opened


def statuses(ledger):
    return {(o.company, o.title): ledger.status(o.id) for o in ledger.opportunities()}


def queue_job(**overrides):
    job = {
        "id": "q1",
        "company": "Acme",
        "title": "Solutions Engineer",
        "url": ASHBY,
        "location": "Remote (US)",
        "portal": "ashby",
        "track": "tech",
        "fit_score": 66,
        "keywords": [],
        "status": "queued",
        "queued_at": "2026-09-25T11:56:45",
        "psyche_score": 7,
        "suppression_reason": "",
    }
    return {**job, **overrides}


def gig_row(**overrides):
    row = {
        "status": "new",
        "score": 60,
        "company": "Beta",
        "role": "Automation build",
        "pay": "$80/hr",
        "apply": "",
        "saved": "",
        "last_touched": "",
        "gig_id": "hn-1",
    }
    return SimpleNamespace(**{**row, **overrides})


class TestQueue:
    def test_statuses_map_to_events(self, ledger):
        jobs = [
            queue_job(),
            queue_job(id="q2", title="Applied role", url=ASHBY + "0", status="applied"),
            queue_job(id="q3", title="Skipped role", url=ASHBY + "1", status="skipped"),
            queue_job(id="q4", title="Viewing role", url=ASHBY + "2", status="viewing"),
        ]
        records, skipped = importers.from_queue(jobs, default_time=DEFAULT)
        report = importers.write(ledger, records, source="queue", skipped=skipped)

        assert (report.records, report.new_opportunities, report.skipped) == (4, 4, [])
        assert statuses(ledger) == {
            ("Acme", "Solutions Engineer"): Status.NEW,
            ("Acme", "Applied role"): Status.APPLIED,
            ("Acme", "Skipped role"): Status.SKIPPED,
            ("Acme", "Viewing role"): Status.NEW,
        }
        discovered = ledger.events(ledger.opportunities()[0].id)[0]
        assert discovered.kind is EventKind.DISCOVERED
        assert discovered.source == "import:queue"

    def test_evidence_derived_applications_are_not_imported(self, ledger):
        job = queue_job(
            status="applied", suppression_reason="application evidence: tracker"
        )
        records, _ = importers.from_queue([job], default_time=DEFAULT)
        importers.write(ledger, records, source="queue")
        (opportunity,) = ledger.opportunities()
        (event,) = ledger.events(opportunity.id)
        assert ledger.status(opportunity.id) is Status.NEW
        assert event.data["legacy_status"] == "applied"
        assert event.data["suppression_reason"] == "application evidence: tracker"

    def test_scores_travel_with_the_discovery(self, ledger):
        records, _ = importers.from_queue([queue_job()], default_time=DEFAULT)
        importers.write(ledger, records, source="queue")
        (event,) = ledger.events(ledger.opportunities()[0].id)
        assert event.data == {
            "fit_score": 66,
            "psyche_score": 7,
            "track": "tech",
            "portal": "ashby",
        }

    def test_rows_without_identity_are_skipped_with_a_reason(self):
        records, skipped = importers.from_queue(
            [queue_job(url="", company="")], default_time=DEFAULT
        )
        assert records == []
        assert skipped and "q1" in skipped[0]


class TestTracker:
    def test_outcomes(self, ledger):
        rows = [
            {
                "id": 1,
                "job_url": ASHBY,
                "job_title": "SE",
                "company": "Acme",
                "applied_at": "2026-09-25",
                "status": "applied",
                "updated_at": "2026-09-25T13:07:47",
            },
            {
                "id": 2,
                "job_url": ASHBY + "x",
                "job_title": "FDE",
                "company": "Acme",
                "applied_at": "2026-09-01",
                "status": "rejected",
                "updated_at": "2026-09-10T09:00:00",
            },
            {
                "id": 3,
                "job_url": ASHBY + "y",
                "job_title": "CE",
                "company": "Acme",
                "applied_at": "2026-09-02",
                "status": "interview",
                "updated_at": "2026-09-12T09:00:00",
            },
            {
                "id": 4,
                "job_url": "manual://beta-analyst/2026-09-03",
                "job_title": "Analyst",
                "company": "Beta",
                "applied_at": "2026-09-03",
                "status": "abandoned",
                "updated_at": "2026-09-03T10:00:00",
            },
        ]
        records, skipped = importers.from_tracker(rows, default_time=DEFAULT)
        importers.write(ledger, records, source="tracker", skipped=skipped)
        assert statuses(ledger) == {
            ("Acme", "SE"): Status.APPLIED,
            ("Acme", "FDE"): Status.REJECTED,
            ("Acme", "CE"): Status.INTERVIEWING,
            ("Beta", "Analyst"): Status.SKIPPED,
        }

    def test_synthetic_urls_fall_back_to_company_and_title(self):
        rows = [
            {
                "id": 9,
                "job_url": "manual://beta-analyst/2026-09-03",
                "job_title": "Analyst",
                "company": "Beta",
                "applied_at": "2026-09-03",
                "status": "applied",
                "updated_at": "",
            }
        ]
        (record,), _ = importers.from_tracker(rows, default_time=DEFAULT)
        assert record.listing.identity_key() == "role:beta:analyst"


class TestPipeline:
    @pytest.mark.parametrize(
        ("legacy", "expected"),
        [
            ("new", Status.NEW),
            ("saved", Status.SHORTLISTED),
            ("drafted", Status.READY),
            ("sent", Status.APPLIED),
            ("replied", Status.INTERVIEWING),
            ("interview", Status.INTERVIEWING),
            ("hired", Status.OFFER),
            ("passed", Status.SKIPPED),
            ("archived", Status.SKIPPED),
            ("ghosted", Status.NO_RESPONSE),
            ("maybe later", Status.NEW),
        ],
    )
    def test_statuses(self, ledger, legacy, expected):
        records, _ = importers.from_pipeline(
            [gig_row(status=legacy)], first_seen={}, default_time=DEFAULT
        )
        importers.write(ledger, records, source="pipeline")
        (opportunity,) = ledger.opportunities()
        assert opportunity.lane is Lane.GIG
        assert ledger.status(opportunity.id) is expected

    def test_custom_labels_are_kept(self, ledger):
        records, _ = importers.from_pipeline(
            [gig_row(status="maybe later")], first_seen={}, default_time=DEFAULT
        )
        importers.write(ledger, records, source="pipeline")
        (event,) = ledger.events(ledger.opportunities()[0].id)
        assert event.data["legacy_status"] == "maybe later"

    def test_identity_prefers_the_apply_url_then_the_gig_id(self):
        with_url, _ = importers.from_pipeline(
            [gig_row(apply="[Apply](https://jobs.lever.co/beta/abc-1)")],
            first_seen={},
            default_time=DEFAULT,
        )
        without_url, _ = importers.from_pipeline(
            [gig_row()], first_seen={}, default_time=DEFAULT
        )
        assert with_url[0].listing.identity_key() == "lever:beta:abc-1"
        assert without_url[0].listing.identity_key() == "gigpilot:hn-1"

    def test_dates_come_from_first_seen_and_the_row(self, ledger):
        row = gig_row(status="sent", saved="2026-09-20", last_touched="2026-09-22")
        records, _ = importers.from_pipeline(
            [row],
            first_seen={"hn-1": "2026-09-18T08:00:00+00:00"},
            default_time=DEFAULT,
        )
        importers.write(ledger, records, source="pipeline")
        discovered, applied = ledger.events(ledger.opportunities()[0].id)
        assert discovered.occurred_at == "2026-09-18T08:00:00+00:00"
        assert applied.kind is EventKind.APPLIED
        assert applied.occurred_at > discovered.occurred_at


class TestRerunsAndOverlap:
    def test_reimporting_adds_nothing(self, ledger):
        records, _ = importers.from_queue(
            [queue_job(status="applied")], default_time=DEFAULT
        )
        first = importers.write(ledger, records, source="queue")
        again = importers.write(ledger, records, source="queue")
        assert (first.events_added, again.events_added) == (2, 0)
        assert (again.new_opportunities, again.events_already_recorded) == (0, 2)

    def test_status_changes_since_the_last_import_are_added(self, ledger):
        rows = [gig_row(status="sent")]
        importers.write(
            ledger,
            importers.from_pipeline(rows, first_seen={}, default_time=DEFAULT)[0],
            source="pipeline",
        )
        rows = [gig_row(status="replied")]
        report = importers.write(
            ledger,
            importers.from_pipeline(rows, first_seen={}, default_time=DEFAULT)[0],
            source="pipeline",
        )
        assert report.events_added == 1
        assert ledger.status(ledger.opportunities()[0].id) is Status.INTERVIEWING

    def test_tracker_and_queue_share_one_role_and_one_application(self, ledger):
        tracker_rows = [
            {
                "id": 1,
                "job_url": ASHBY,
                "job_title": "SE",
                "company": "Acme",
                "applied_at": "2026-09-25",
                "status": "applied",
                "updated_at": "",
            }
        ]
        importers.write(
            ledger,
            importers.from_tracker(tracker_rows, default_time=DEFAULT)[0],
            source="tracker",
        )
        report = importers.write(
            ledger,
            importers.from_queue(
                [queue_job(url=ASHBY + "?src=li", status="applied")],
                default_time=DEFAULT,
            )[0],
            source="queue",
        )
        (opportunity,) = ledger.opportunities()
        kinds = [event.kind for event in ledger.events(opportunity.id)]
        assert kinds.count(EventKind.APPLIED) == 1
        assert report.events_already_recorded == 1
