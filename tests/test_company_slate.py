"""The default queue is a verified, role-specific company slate."""

from datetime import UTC, datetime, timedelta

import jobpilot.core.queue_builder as qb
from jobpilot.core.queue_builder import QueueJob


def _job(**overrides) -> QueueJob:
    values = {
        "id": "role-1",
        "company": "Acme",
        "title": "Customer Engineer",
        "url": "https://jobs.example.test/acme/1",
        "location": "Austin, Texas",
        "portal": "greenhouse",
        "track": "both",
        "fit_score": 78,
        "keywords": ["customer"],
        "status": "queued",
        "decision": "apply_now",
        "assessment_status": "assessed",
        "qualification_lower_bound": 78,
        "work_context_match": 82,
        "evidence_coverage": 75,
        "legitimacy_state": "recommend",
        "evidence_grade": "A",
        "verified_at": datetime(2026, 8, 13, 12, tzinfo=UTC).isoformat(),
    }
    values.update(overrides)
    return QueueJob(**values)


def test_company_slate_keeps_one_best_active_role_per_company() -> None:
    jobs = [
        _job(
            id="stretch",
            title="Senior Customer Engineer",
            decision="stretch",
            qualification_lower_bound=63,
        ),
        _job(
            id="ready",
            title="Customer Engineer",
            decision="apply_now",
            qualification_lower_bound=78,
        ),
        _job(id="other", company="Globex", title="Field Engineer"),
    ]

    changed, companies = qb.apply_company_slate(jobs)

    assert (changed, companies) == (1, 1)
    by_id = {job.id: job for job in jobs}
    assert by_id["ready"].status == "queued"
    assert by_id["stretch"].status == "skipped"
    assert by_id["stretch"].suppression_reason == (
        "alternate role at company: Customer Engineer"
    )
    assert by_id["other"].status == "queued"


def test_company_slate_ignores_legacy_fit_and_psyche_tie_breakers() -> None:
    jobs = [
        _job(id="evidenced", fit_score=1, psyche_score=0),
        _job(
            id="keyword-heavy",
            title="Forward Deployed Creative Designer",
            fit_score=100,
            psyche_score=10_000,
        ),
    ]

    qb.apply_company_slate(jobs)

    assert {job.id for job in jobs if job.status == "queued"} == {"evidenced"}


def test_stale_verification_downgrades_apply_now_to_investigate() -> None:
    now = datetime(2026, 8, 13, 12, tzinfo=UTC)
    stale = _job(verified_at=(now - timedelta(hours=25)).isoformat())

    changed = qb.expire_stale_recommendations([stale], now=now)

    assert changed == 1
    assert stale.decision == "investigate"
    assert stale.legitimacy_state == "hold"
    assert stale.status == "queued"
    assert "Refresh" in stale.suppression_reason
    assert qb.is_apply_ready(stale, now=now) is False


def test_current_verified_recommendation_is_apply_ready() -> None:
    now = datetime(2026, 8, 13, 12, tzinfo=UTC)
    ready = _job(verified_at=(now - timedelta(hours=2)).isoformat())

    assert qb.is_apply_ready(ready, now=now) is True


def test_unverified_legacy_queue_row_fails_closed() -> None:
    legacy = _job(
        decision="investigate",
        legitimacy_state="hold",
        verified_at="",
    )

    assert qb.is_apply_ready(legacy) is False
