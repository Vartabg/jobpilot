"""Tests for autonomous work-style scoring."""

from jobpilot.core.work_style import (
    is_contract_friendly,
    is_schedule_rigid,
    is_w2_only,
    score_work_style,
    title_seniority_penalty,
)


def test_contract_friendly_detects_hourly():
    assert is_contract_friendly("Freelance Python contractor, hourly rate", title="Integration consultant")


def test_w2_only_detects_full_time_employee():
    assert is_w2_only("This is a full-time employee only role with benefits")


def test_schedule_rigid_detects_nine_to_five():
    assert is_schedule_rigid("Must work 9-5 with daily standup", title="Engineer")


def test_score_work_style_boosts_async_contract():
    delta, reasons = score_work_style(
        "Async contract role with flexible hours and milestone deliverables",
        title="Implementation consultant",
    )
    assert delta > 0
    assert any("contract" in r or "async" in r for r in reasons)


def test_senior_title_penalty():
    pen, kw = title_seniority_penalty("Senior Software Engineer", ("senior software",))
    assert pen < 0
    assert kw


def test_non_senior_title_no_penalty():
    pen, _ = title_seniority_penalty("Implementation Engineer", ("senior software",))
    assert pen == 0