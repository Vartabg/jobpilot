"""A refreshed queue is built from source, role, and candidate evidence."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

import jobpilot.core.queue_builder as qb
from jobpilot.core.opportunity_ledger import OpportunityLedger
from jobpilot.core.opportunity_models import SourceObservation, SourceRunResult
from jobpilot.core.policy_config import policy_from_dict, set_policy
from jobpilot.core.portal_scanner import PortalJob, ScanTarget
from jobpilot.core.profile_store import UserProfile

FULL_JD = """
Responsibilities
- Lead customer discovery workshops and product demonstrations.
- Troubleshoot hardware and software integrations at customer sites.
Required qualifications
- Systems troubleshooting experience.
- Clear technical communication with customers.
Preferred qualifications
- Python automation experience.
Work environment
- Work independently through ambiguous technical problems.
Logistics
- Must be authorized to work in the United States without sponsorship.
"""


@pytest.fixture(autouse=True)
def _neutral_policy_for_each_test():
    """Never let a developer's private policy change these unit tests."""
    set_policy(policy_from_dict({}))
    try:
        yield
    finally:
        set_policy(None)


def test_scan_keywords_include_new_evidence_supported_title_families() -> None:
    scanner = qb.PortalScanner(keywords=qb.ALL_KEYWORDS)

    for title in (
        "Deployment Strategist",
        "Implementation Consultant",
        "Customer Success Manager",
        "Three.js Visualization Developer",
    ):
        assert scanner._matched_keywords(title, "Acme", "Remote"), title


def test_explicit_target_titles_extend_discovery_keywords() -> None:
    keywords = qb._discovery_keywords(
        UserProfile(target_titles=["Technical Program Liaison"])
    )

    assert "technical program liaison" in keywords


def test_apply_ready_rejects_non_https_posting_url() -> None:
    job = qb.QueueJob(
        id="unsafe", company="Acme", title="Implementation Engineer",
        url="javascript:alert(1)", location="Remote", portal="ashby",
        track="tech", fit_score=100, keywords=[], decision="apply_now",
        assessment_status="assessed", legitimacy_state="recommend",
        verified_at=datetime.now(UTC).isoformat(),
    )

    assert qb.is_apply_ready(job) is False


def _ready_job(now: datetime) -> qb.QueueJob:
    return qb.QueueJob(
        id="ready",
        company="Acme",
        title="Implementation Engineer",
        url="https://jobs.ashbyhq.com/acme/role-1",
        location="Remote",
        portal="ashby",
        provider_job_id="role-1",
        track="tech",
        fit_score=90,
        keywords=[],
        decision="apply_now",
        assessment_status="assessed",
        legitimacy_state="recommend",
        verified_at=now.isoformat(),
    )


def _record_ready_provenance(
    ledger: OpportunityLedger,
    job: qb.QueueJob,
    now: datetime,
) -> None:
    opportunity_id = ledger.upsert_opportunity(
        job.company,
        job.title,
        canonical_url=job.url,
        provider=job.portal,
        provider_job_id=job.provider_job_id,
        identity_key="ashby:acme:role-1",
    )
    ledger.record_observation(
        opportunity_id,
        SourceObservation(
            provider="ashby",
            tenant="acme",
            provider_job_id="role-1",
            canonical_url=job.url,
            fetched_at=now.isoformat(),
            listing_state="listed",
            description=FULL_JD,
        ),
    )
    ledger.record_assessment(
        opportunity_id,
        "legitimacy",
        {"state": "recommend", "verified_at": now.isoformat()},
        assessed_at=now.isoformat(),
    )
    ledger.record_assessment(
        opportunity_id,
        "role_decision",
        {"status": "assessed", "decision": "apply_now"},
        assessed_at=now.isoformat(),
    )


def test_action_provenance_requires_exact_current_ledger_evidence(
    tmp_path: Path,
) -> None:
    now = datetime.now(UTC)
    job = _ready_job(now)
    ledger_path = tmp_path / "opportunities.db"
    ledger = OpportunityLedger(db_path=ledger_path)
    try:
        _record_ready_provenance(ledger, job, now)
    finally:
        ledger.close()

    assert qb.has_current_action_provenance(
        job,
        now=now,
        ledger_path=ledger_path,
    )

    forged = _ready_job(now)
    forged.url = "https://jobs.ashbyhq.com/acme/role-2"
    forged.provider_job_id = "role-2"
    assert not qb.has_current_action_provenance(
        forged,
        now=now,
        ledger_path=ledger_path,
    )


def test_action_provenance_rechecks_configured_history_freshness(
    tmp_path: Path,
) -> None:
    now = datetime.now(UTC)
    job = _ready_job(now)
    ledger_path = tmp_path / "opportunities.db"
    ledger = OpportunityLedger(db_path=ledger_path)
    try:
        _record_ready_provenance(ledger, job, now)
    finally:
        ledger.close()
    set_policy(policy_from_dict({
        "application_evidence": {
            "fail_closed": True,
            "gmail_cache_path": str(tmp_path / "missing-gmail-cache.json"),
            "gmail_cache_max_age_hours": 24,
        },
    }))

    try:
        allowed = qb.has_current_action_provenance(
            job,
            now=now,
            ledger_path=ledger_path,
        )
    finally:
        set_policy(None)

    assert allowed is False


def test_action_provenance_missing_or_corrupt_ledger_fails_without_creating(
    tmp_path: Path,
) -> None:
    now = datetime.now(UTC)
    job = _ready_job(now)
    missing = tmp_path / "missing.db"

    assert not qb.has_current_action_provenance(
        job,
        now=now,
        ledger_path=missing,
    )
    assert not missing.exists()

    corrupt = tmp_path / "corrupt.db"
    corrupt.write_text("not sqlite")
    assert not qb.has_current_action_provenance(
        job,
        now=now,
        ledger_path=corrupt,
    )


def test_presentation_downgrades_orphaned_cached_recommendation(
    monkeypatch,
) -> None:
    job = _ready_job(datetime.now(UTC))
    monkeypatch.setattr(
        qb,
        "has_current_action_provenance",
        lambda _job, **_kwargs: False,
    )

    assert qb.restrict_queue_for_action_provenance([job]) == 1
    assert job.decision == "investigate"
    assert "ledger corroboration" in job.suppression_reason


class _Tracker:
    def has_applied(self, _url: str) -> bool:
        return False

    def get_status(self, _url: str):
        return None


class _Evidence:
    imported = False

    def ensure_usable(self) -> None:
        return None

    def match(self, _company: str, _title: str, _url: str):
        return None

    def import_into(self, _ledger) -> int:
        self.imported = True
        return 0


def _portal_job(company: str, title: str, role_id: str) -> PortalJob:
    return PortalJob(
        company=company,
        title=title,
        url=f"https://jobs.ashbyhq.com/{company.lower()}/{role_id}",
        location="Austin, Texas",
        portal="ashby",
        matched_keywords=["customer", "technical"],
        description=FULL_JD,
        provider_tenant=company.lower(),
        provider_job_id=role_id,
        fetched_at=datetime.now(UTC).isoformat(),
        listing_state="listed",
    )


def test_build_queue_persists_evidence_and_skips_unsupported_family(
    monkeypatch,
    tmp_path: Path,
) -> None:
    jobs = [
        _portal_job("Acme", "Implementation Engineer", "role-1"),
        _portal_job("Globex", "Forward Deployed Creative Designer", "role-2"),
    ]

    class _Scanner:
        def __init__(self, **_kwargs):
            self.last_run_results = []

        @staticmethod
        def load_targets():
            return [ScanTarget("ashby", "acme", "Acme")]

        def scan_targets(self, _targets):
            self.last_run_results = [
                SourceRunResult("ashby", "acme", "success", result_count=2)
            ]
            return jobs

    accounts = tmp_path / "true_accounts.json"
    accounts.write_text(json.dumps({
        "version": 1,
        "accounts": [{
            "id": "customer-field",
            "title": "Customer-site technical work",
            "summary": "Diagnosed hardware and software issues at customer sites.",
            "details": ["Explained repairs and trained customers."],
            "skills": ["systems troubleshooting", "technical communication"],
        }],
    }))
    ledger_path = tmp_path / "opportunities.db"
    application_evidence = _Evidence()
    monkeypatch.setattr(qb, "PortalScanner", _Scanner)
    monkeypatch.setattr(qb, "QUEUE_PATH", tmp_path / "queue.json")
    monkeypatch.setattr(qb, "TRUE_ACCOUNTS_PATH", accounts)
    monkeypatch.setattr(qb, "get_application_tracker", lambda: _Tracker())
    monkeypatch.setattr(
        qb,
        "get_application_evidence_index",
        lambda _tracker=None: application_evidence,
    )
    monkeypatch.setattr(
        qb,
        "get_profile_store",
        lambda: type("Store", (), {"load": lambda self: UserProfile(
            skills=["Python automation"],
            authorized_to_work=True,
            requires_sponsorship=False,
        )})(),
    )
    monkeypatch.setattr(
        qb,
        "OpportunityLedger",
        lambda: OpportunityLedger(db_path=ledger_path),
    )
    set_policy(policy_from_dict({}))

    result = qb.build_queue(limit=10)

    by_title = {job.title: job for job in result}
    supported = by_title["Implementation Engineer"]
    unsupported = by_title["Forward Deployed Creative Designer"]
    assert supported.decision == "apply_now"
    assert supported.legitimacy_state == "recommend"
    assert supported.evidence_grade == "A"
    assert supported.qualification_lower_bound is not None
    assert supported.matched_accounts == ["customer-field"]
    assert supported.psyche_score == 0
    assert qb.is_apply_ready(supported)
    assert unsupported.decision == "skip"
    assert unsupported.status == "skipped"
    assert application_evidence.imported

    ledger = OpportunityLedger(db_path=ledger_path)
    try:
        assert ledger.table_count("opportunities") == 2
        assert ledger.table_count("observations") == 2
        assert ledger.table_count("assessments") == 4
        assert ledger.table_count("events") >= 4
    finally:
        ledger.close()
        set_policy(None)


def test_incomplete_application_history_allows_search_but_blocks_apply(
    monkeypatch,
    tmp_path: Path,
) -> None:
    job = _portal_job("Acme", "Implementation Engineer", "role-1")

    class _Scanner:
        def __init__(self, **_kwargs):
            self.last_run_results = []

        @staticmethod
        def load_targets():
            return [ScanTarget("ashby", "acme", "Acme")]

        def scan_targets(self, _targets):
            self.last_run_results = [
                SourceRunResult("ashby", "acme", "success", result_count=1)
            ]
            return [job]

    class _IncompleteEvidence(_Evidence):
        errors = ("Gmail application cache is stale",)

    ledger_path = tmp_path / "opportunities.db"
    monkeypatch.setattr(qb, "PortalScanner", _Scanner)
    monkeypatch.setattr(qb, "QUEUE_PATH", tmp_path / "queue.json")
    monkeypatch.setattr(qb, "TRUE_ACCOUNTS_PATH", tmp_path / "missing-accounts.json")
    monkeypatch.setattr(qb, "get_application_tracker", lambda: _Tracker())
    monkeypatch.setattr(
        qb,
        "get_application_evidence_index",
        lambda _tracker=None: _IncompleteEvidence(),
    )
    monkeypatch.setattr(
        qb,
        "get_profile_store",
        lambda: type("Store", (), {"load": lambda self: UserProfile(
            skills=["Systems troubleshooting", "technical communication", "Python"],
            authorized_to_work=True,
            requires_sponsorship=False,
        )})(),
    )
    monkeypatch.setattr(
        qb,
        "OpportunityLedger",
        lambda: OpportunityLedger(db_path=ledger_path),
    )
    set_policy(policy_from_dict({
        "application_evidence": {"fail_closed": True},
    }))

    try:
        result = qb.build_queue(limit=10)
        ledger = OpportunityLedger(db_path=ledger_path)
        try:
            opportunity_id = ledger.iter_opportunities()[0]["id"]
            stored = ledger.latest_assessment(opportunity_id, "role_decision")
        finally:
            ledger.close()
    finally:
        set_policy(None)

    assert len(result) == 1
    assert result[0].decision == "investigate"
    assert "Application history is incomplete" in result[0].suppression_reason
    assert qb.is_apply_ready(result[0]) is False
    assert stored is not None
    assert stored["result"]["decision"] == "investigate"


def test_unseen_cached_active_role_is_held_for_investigation(
    monkeypatch,
    tmp_path: Path,
) -> None:
    prior = qb.QueueJob(
        id="cached",
        company="Acme",
        title="Implementation Engineer",
        url="https://jobs.ashbyhq.com/acme/cached",
        location="Austin, Texas",
        portal="ashby",
        track="both",
        fit_score=88,
        keywords=["implementation"],
        decision="apply_now",
        assessment_status="assessed",
        legitimacy_state="recommend",
        evidence_grade="A",
        verified_at=datetime.now(UTC).isoformat(),
    )

    class _EmptyScanner:
        def __init__(self, **_kwargs):
            self.last_run_results = []

        @staticmethod
        def load_targets():
            return [ScanTarget("ashby", "acme", "Acme")]

        def scan_targets(self, _targets):
            self.last_run_results = [
                SourceRunResult("ashby", "acme", "failure", error="scan failed")
            ]
            return []

    monkeypatch.setattr(qb, "PortalScanner", _EmptyScanner)
    monkeypatch.setattr(qb, "load_queue", lambda: [prior])
    monkeypatch.setattr(qb, "get_application_tracker", lambda: _Tracker())
    monkeypatch.setattr(
        qb, "get_application_evidence_index", lambda _tracker=None: _Evidence()
    )
    monkeypatch.setattr(
        qb, "OpportunityLedger", lambda: OpportunityLedger(db_path=tmp_path / "ledger.db")
    )
    set_policy(policy_from_dict({}))

    try:
        result = qb.build_queue(limit=10)
    finally:
        set_policy(None)

    assert len(result) == 1
    held = result[0]
    assert held.decision == "investigate"
    assert held.assessment_status == "unscorable"
    assert held.legitimacy_state == "hold"
    assert held.verified_at == ""
    assert qb.is_apply_ready(held) is False
