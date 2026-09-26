from datetime import datetime

from jobpilot.bridge.timestamps import to_utc

DEFAULT = "2026-09-01T00:00:00+00:00"


def test_zoned_timestamps_are_exact():
    assert to_utc("2026-09-25T09:30:00-05:00", DEFAULT) == (
        "2026-09-25T14:30:00+00:00",
        False,
    )


def test_naive_timestamps_are_read_as_local_time():
    local = datetime(2026, 9, 25, 11, 56, 45).astimezone()
    expected = local.astimezone().isoformat(timespec="seconds")
    value, estimated = to_utc("2026-09-25T11:56:45.900871", DEFAULT)
    assert estimated is False
    assert datetime.fromisoformat(value) == datetime.fromisoformat(expected)


def test_bare_dates_become_local_noon_and_are_estimated():
    value, estimated = to_utc("2026-09-25", DEFAULT)
    assert estimated is True
    assert datetime.fromisoformat(value).astimezone().date().isoformat() == "2026-09-25"


def test_missing_or_garbled_values_fall_back_to_the_default():
    assert to_utc("", DEFAULT) == (DEFAULT, True)
    assert to_utc("last tuesday", DEFAULT) == (DEFAULT, True)
