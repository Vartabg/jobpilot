import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from jobpilot.core.form_filler import (
    DECLINE_TO_SELF_IDENTIFY,
    _field_to_value,
    _preferred_resume_upload,
    _radio_option_matches,
    _yesno_for_question,
    fill_application,
    submit_application,
)
from jobpilot.core.profile_store import UserProfile


def test_preferred_resume_upload_uses_latest_tailored_pdf(tmp_path: Path):
    profile_pdf = tmp_path / "profile.pdf"
    tailored_pdf = tmp_path / "tailored.pdf"
    profile_pdf.write_text("profile")
    tailored_pdf.write_text("tailored")

    profile = UserProfile(resume_path=str(profile_pdf))

    with patch(
        "jobpilot.core.form_filler.ResumeTailor.load_latest_draft_summary",
        return_value={"pdf_path": str(tailored_pdf)},
    ):
        path, source = _preferred_resume_upload(profile)

    assert path == tailored_pdf
    assert source == "tailored"


def test_preferred_resume_upload_falls_back_to_profile_resume(tmp_path: Path):
    profile_pdf = tmp_path / "profile.pdf"
    profile_pdf.write_text("profile")

    profile = UserProfile(resume_path=str(profile_pdf))

    with patch(
        "jobpilot.core.form_filler.ResumeTailor.load_latest_draft_summary",
        return_value=None,
    ):
        path, source = _preferred_resume_upload(profile)

    assert path == profile_pdf
    assert source == "profile"


@pytest.mark.asyncio
async def test_submit_application_is_disabled_by_policy():
    ok, message = await submit_application()

    assert ok is False
    assert "Auto-submit disabled" in message


def test_unknown_work_authorization_never_becomes_yes_or_no():
    profile = UserProfile()

    assert _field_to_value("Are you authorized to work in the US?", profile) is None
    assert _field_to_value("Will you require sponsorship?", profile) is None


@pytest.mark.asyncio
async def test_fill_application_is_disabled_by_policy():
    result = await fill_application(
        "https://jobs.example.test/acme/1",
        "Customer Engineer",
        "Acme",
        UserProfile(),
    )

    assert result.success is False
    assert result.stopped_before_submit is True
    assert "paste sheet" in result.error.lower()


def test_form_filler_module_contains_no_browser_mutation_capability():
    import jobpilot.core.form_filler as form_filler

    source = inspect.getsource(form_filler)
    forbidden = (
        "async_playwright",
        "connect_over_cdp",
        "set_input_files",
        ".click(",
        ".check(",
        ".fill(",
        ".type(",
    )

    assert all(token not in source for token in forbidden)


def test_unknown_experience_never_becomes_none_or_zero_text():
    assert _field_to_value("Years of experience", UserProfile()) == ""


def test_unset_profile_does_not_invent_employer_salary_or_referral() -> None:
    profile = UserProfile()

    assert _field_to_value("Current employer", profile) == ""
    assert _field_to_value("Desired salary", profile) == ""
    assert _field_to_value("How did you hear about this job?", profile) is None


def test_willingness_and_conflict_answers_require_explicit_user_evidence() -> None:
    profile = UserProfile()

    assert _yesno_for_question("Are you willing to relocate?", profile) is None
    assert _yesno_for_question("Do you have a conflict of interest?", profile) is None

    profile.custom_answers = {
        "Are you willing to relocate?": "No",
        "Do you have a conflict of interest?": "Yes",
    }
    assert _yesno_for_question("Are you willing to relocate?", profile) == "No"
    assert _yesno_for_question("Do you have a conflict of interest?", profile) == "Yes"


# ---------------------------------------------------------------------------
# Radio option matching — exact / word-boundary, never loose substring
# ---------------------------------------------------------------------------

def test_radio_no_does_not_match_none_of_the_above():
    assert _radio_option_matches("No", "None of the above", "") is False
    assert _radio_option_matches("No", "Not sure", "") is False
    assert _radio_option_matches("No", "No", "") is True


def test_radio_matches_value_attribute_exactly():
    assert _radio_option_matches("No", "", "no") is True
    assert _radio_option_matches("No", "", "none") is False


def test_radio_word_boundary_matches_verbose_labels():
    assert _radio_option_matches("No", "No, I do not require sponsorship", "") is True
    assert _radio_option_matches("Yes", "Yes, I am authorized", "") is True
    assert _radio_option_matches(
        "Protected veteran",
        "I identify as one or more of the classifications of a protected veteran",
        "",
    ) is True


# ---------------------------------------------------------------------------
# Demographics — never hardcoded, profile-driven, decline by default
# ---------------------------------------------------------------------------

def test_demographics_default_to_decline_when_unset():
    profile = UserProfile()

    for question in (
        "Veteran Status",
        "What is your gender?",
        "Gender Identity",
        "Race/Ethnicity",
        "Are you Hispanic or Latino?",
        "Disability Status",
        "Sexual Orientation",
    ):
        answer = _yesno_for_question(question, profile)
        assert answer == DECLINE_TO_SELF_IDENTIFY, question

    # The old hardcoded veteran claim must be gone
    assert "protected veteran" not in _yesno_for_question(
        "Are you a protected veteran?", UserProfile()
    ).lower()


def test_demographics_override_from_profile_demographics_block():
    profile = SimpleNamespace(
        demographics={"veteran": "I identify as a protected veteran"},
        custom_answers={},
    )

    answer = _yesno_for_question("Are you a protected veteran?", profile)

    assert answer == "I identify as a protected veteran"
    # Other demographics still decline
    assert _yesno_for_question("Disability Status", profile) == DECLINE_TO_SELF_IDENTIFY


def test_demographics_fall_back_to_custom_answers():
    profile = UserProfile(
        custom_answers={"Veteran Status": "I identify as a protected veteran"}
    )

    answer = _yesno_for_question("Are you a protected veteran?", profile)

    assert answer == "I identify as a protected veteran"
