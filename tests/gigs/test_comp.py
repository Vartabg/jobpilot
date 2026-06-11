"""Shared comp parsing — hourly/salary disambiguation used by all scrapers."""
from __future__ import annotations

from jobpilot.gigs.core.scrapers.comp import parse_comp
from jobpilot.gigs.core.scrapers.hackernews import _parse_post
from jobpilot.gigs.core.scrapers.weworkremotely import _parse_comp as wwr_parse_comp


# ---- regression: hourly range misread as salary --------------------------
#
# "$70 - $90 per hour" used to match the salary regex first, get *1000'd to
# "70K-90K/yr", normalize to $35-45/hr, and silently drop a $90/hr gig
# below the $65 floor.


def test_hourly_range_is_not_misread_as_salary() -> None:
    assert parse_comp("Pay: $70 - $90 per hour, 20 hrs/week") == (0.0, 0.0, 90.0)


def test_hourly_range_slash_hr_and_en_dash_variants() -> None:
    assert parse_comp("$70-$90/hr DOE") == (0.0, 0.0, 90.0)
    assert parse_comp("$70 – $90 an hour") == (0.0, 0.0, 90.0)


def test_wwr_parses_hourly_range_as_hourly() -> None:
    assert wwr_parse_comp("$70 - $90 per hour") == (0.0, 0.0, 90.0)


def test_hn_post_parses_hourly_range_as_hourly() -> None:
    sal_min, sal_max, hourly, remote = _parse_post(
        "Acme | Contractor | Remote | $70 - $90 per hour"
    )
    assert (sal_min, sal_max, hourly) == (0.0, 0.0, 90.0)
    assert remote is True


# ---- regression: comma-thousands matched nothing --------------------------


def test_salary_range_comma_thousands() -> None:
    # \d{2,3} couldn't bridge the comma, so "$150,000 - $200,000" parsed as
    # no comp at all (and could never half-match as 150/200 either).
    assert parse_comp("$150,000 - $200,000 base") == (150000.0, 200000.0, 0.0)


def test_hn_post_parses_comma_thousands_salary() -> None:
    sal_min, sal_max, hourly, _ = _parse_post("Acme | Eng | NYC | $150,000 - $200,000")
    assert (sal_min, sal_max, hourly) == (150000.0, 200000.0, 0.0)


# ---- existing formats keep working ----------------------------------------


def test_salary_range_k_suffix() -> None:
    assert parse_comp("$120K-150K plus equity") == (120000.0, 150000.0, 0.0)


def test_unitless_two_digit_range_still_reads_as_thousands() -> None:
    assert parse_comp("$70 - $90") == (70000.0, 90000.0, 0.0)


def test_single_hourly_rate_variants() -> None:
    assert parse_comp("$85 per hour") == (0.0, 0.0, 85.0)
    assert parse_comp("$85/hr") == (0.0, 0.0, 85.0)
    assert parse_comp("$85 hourly") == (0.0, 0.0, 85.0)


def test_salary_range_wins_over_trailing_hourly_mention() -> None:
    assert parse_comp("$150K-$200K (contract: $90/hr)") == (150000.0, 200000.0, 0.0)


def test_no_comp_in_text() -> None:
    assert parse_comp("Competitive pay, great benefits") == (0.0, 0.0, 0.0)
    assert parse_comp("") == (0.0, 0.0, 0.0)
