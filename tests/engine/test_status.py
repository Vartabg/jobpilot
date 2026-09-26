import pytest

from jobpilot.engine.domain import (
    Event,
    EventKind,
    Status,
    normalize_timestamp,
    status_of,
)

K = EventKind


def history(*kinds: EventKind) -> list[Event]:
    return [
        Event("opp_1", kind, f"2026-09-{index + 1:02d}T12:00:00+00:00", "test")
        for index, kind in enumerate(kinds)
    ]


@pytest.mark.parametrize(
    ("kinds", "expected"),
    [
        ((), Status.NEW),
        ((K.DISCOVERED,), Status.NEW),
        ((K.DISCOVERED, K.SHORTLISTED), Status.SHORTLISTED),
        ((K.SKIPPED, K.SHORTLISTED), Status.SHORTLISTED),  # the latest decision wins
        ((K.SHORTLISTED, K.KIT_PREPARED), Status.READY),
        ((K.KIT_PREPARED, K.APPLIED), Status.APPLIED),
        (
            (K.APPLIED, K.DISCOVERED),
            Status.APPLIED,
        ),  # seeing it again never lowers status
        (
            (K.APPLIED, K.SKIPPED),
            Status.APPLIED,
        ),  # decisions stop mattering after applying
        (
            (K.APPLIED, K.CLOSED),
            Status.APPLIED,
        ),  # a closed posting only matters if never applied
        ((K.DISCOVERED, K.CLOSED), Status.CLOSED),
        ((K.CLOSED, K.DISCOVERED), Status.NEW),  # reposted
        ((K.APPLIED, K.OUTREACH), Status.APPLIED),
        ((K.APPLIED, K.REPLIED), Status.INTERVIEWING),
        ((K.APPLIED, K.INTERVIEW, K.OFFER), Status.OFFER),
        ((K.OFFER, K.INTERVIEW), Status.OFFER),
        ((K.APPLIED, K.REJECTED), Status.REJECTED),
        (
            (K.APPLIED, K.REJECTED, K.INTERVIEW),
            Status.INTERVIEWING,
        ),  # the latest outcome wins
        ((K.REJECTED, K.APPLIED), Status.APPLIED),  # re-applied later
        ((K.INTERVIEW, K.NO_RESPONSE), Status.NO_RESPONSE),
        ((K.APPLIED, K.WITHDRAWN), Status.WITHDRAWN),
    ],
)
def test_status_of(kinds, expected):
    assert status_of(history(*kinds)) is expected


class TestEvent:
    def test_timestamps_are_normalized_to_utc(self):
        event = Event("opp_1", "applied", "2026-09-25T09:00:00-05:00", "user")
        assert event.occurred_at == "2026-09-25T14:00:00+00:00"
        assert event.kind is EventKind.APPLIED

    def test_naive_timestamps_are_rejected(self):
        with pytest.raises(ValueError):
            normalize_timestamp("2026-09-25T09:00:00")

    def test_unknown_kinds_are_rejected(self):
        with pytest.raises(ValueError):
            Event("opp_1", "hired", "2026-09-25T09:00:00+00:00", "user")

    def test_same_fact_gets_the_same_id(self):
        a = Event("opp_1", K.APPLIED, "2026-09-25T09:00:00+00:00", "user")
        b = Event("opp_1", K.APPLIED, "2026-09-25T14:00:00+05:00", "user")
        assert a.id == b.id

    def test_an_explicit_key_decides_the_id(self):
        a = Event(
            "opp_1",
            K.APPLIED,
            "2026-09-25T09:00:00+00:00",
            "import",
            key="tracker:7:applied",
        )
        b = Event(
            "opp_1",
            K.APPLIED,
            "2026-09-26T09:00:00+00:00",
            "import",
            key="tracker:7:applied",
        )
        assert a.id == b.id

    def test_data_is_read_only(self):
        event = Event(
            "opp_1",
            K.APPLIED,
            "2026-09-25T09:00:00+00:00",
            "user",
            data={"via": "ashby"},
        )
        with pytest.raises(TypeError):
            event.data["via"] = "changed"
