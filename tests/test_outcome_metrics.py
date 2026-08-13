from datetime import UTC, datetime, timedelta

from jobpilot.core.outcome_metrics import summarize_outcomes


def _event(role: str, kind: str, when: datetime, channel: str = "ats") -> dict:
    return {
        "opportunity_id": role,
        "event_type": kind,
        "occurred_at": when.isoformat(),
        "acquisition_channel": channel,
        "lane": "field_service" if role == "a" else "technical_presales",
    }


def test_summary_is_time_scoped_and_counts_unique_roles() -> None:
    now = datetime(2026, 8, 13, tzinfo=UTC)
    events = [
        _event("a", "applied", now - timedelta(days=2)),
        _event("a", "human_reply", now - timedelta(days=1)),
        _event("a", "interview", now, "referral"),
        _event("b", "applied", now - timedelta(days=3)),
        _event("b", "rejected", now - timedelta(days=1)),
        _event("old", "offer", now - timedelta(days=40)),
    ]

    result = summarize_outcomes(events, days=30, now=now)

    assert result["roles"] == 2
    assert result["stages"]["applied"] == 2
    assert result["stages"]["interview"] == 1
    assert result["stages"]["offer"] == 0
    assert result["positive_human_response"] == 1
    assert result["channels"] == {"ats": 1}
    assert result["lanes"] == {"technical_presales": 1}


def test_sparse_cohort_refuses_weight_updates() -> None:
    now = datetime(2026, 8, 13, tzinfo=UTC)
    sparse = [
        _event(str(index), "rejected", now)
        for index in range(19)
    ]
    ready = [*sparse, _event("offer", "offer", now)]

    assert summarize_outcomes(sparse, now=now)["may_recalibrate"] is False
    assert summarize_outcomes(ready, now=now)["may_recalibrate"] is True


def test_silence_is_not_implicitly_rejection() -> None:
    now = datetime(2026, 8, 13, tzinfo=UTC)
    result = summarize_outcomes(
        [_event("pending", "applied", now - timedelta(days=20))],
        now=now,
    )

    assert result["decided"] == 0
    assert result["stages"]["rejected"] == 0
    assert result["stages"]["no_response"] == 0


def test_lane_cohorts_count_only_decided_roles_once() -> None:
    now = datetime(2026, 8, 13, tzinfo=UTC)
    events = [
        _event("a", "applied", now),
        {
            "opportunity_id": "a",
            "event_type": "rejected",
            "occurred_at": now.isoformat(),
            "source": "manual",
            "provenance": {},
        },
    ]

    result = summarize_outcomes(events, now=now)

    assert result["lanes"] == {"field_service": 1}


def test_channels_count_decided_roles_once_and_ignore_transport_sources() -> None:
    now = datetime(2026, 8, 13, tzinfo=UTC)
    events = [
        {
            "opportunity_id": "a",
            "event_type": "discovered",
            "occurred_at": now.isoformat(),
            "source": "ashby",
            "provenance": {},
        },
        {
            "opportunity_id": "a",
            "event_type": "rejected",
            "occurred_at": now.isoformat(),
            "source": "dashboard",
            "provenance": {"acquisition_channel": "referral"},
        },
    ]

    assert summarize_outcomes(events, now=now)["channels"] == {"referral": 1}


def test_same_stage_from_multiple_evidence_sources_counts_once() -> None:
    now = datetime(2026, 8, 13, tzinfo=UTC)
    events = [
        {
            "opportunity_id": "a",
            "event_type": "applied",
            "occurred_at": now.isoformat(),
            "source": "dashboard",
        },
        {
            "opportunity_id": "a",
            "event_type": "applied",
            "occurred_at": now.isoformat(),
            "source": "tracker",
        },
    ]

    result = summarize_outcomes(events, now=now)
    assert result["stages"]["applied"] == 1
    assert result["roles"] == 1
