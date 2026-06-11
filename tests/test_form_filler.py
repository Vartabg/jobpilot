from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from jobpilot.core.form_filler import (
    DECLINE_TO_SELF_IDENTIFY,
    _answer_yesno_radios,
    _check_required_acknowledgments,
    _preferred_resume_upload,
    _radio_option_matches,
    _yesno_for_question,
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
# Fakes for the async DOM helpers
# ---------------------------------------------------------------------------

class _FakeRadio:
    def __init__(self, label: str, value: str = ""):
        self.label = label
        self.value = value
        self.checked = False

    async def get_attribute(self, name: str):
        return self.value if name == "value" else None

    async def check(self):
        self.checked = True


class _FakeRadioGroup:
    def __init__(self, question: str, radios: list[_FakeRadio]):
        self.question = question
        self.radios = radios

    async def query_selector_all(self, selector: str):
        return self.radios


class _FakePage:
    """Answers page.evaluate(js, element) with the element's stored label."""

    def __init__(self, elements):
        self.elements = elements

    async def query_selector_all(self, selector: str):
        return self.elements

    async def evaluate(self, script: str, arg=None):
        if isinstance(arg, _FakeRadioGroup):
            return arg.question
        return getattr(arg, "label", "")


class _FakeCheckbox:
    def __init__(self, label: str):
        self.label = label
        self.checked = False

    async def is_visible(self):
        return True

    async def is_checked(self):
        return self.checked

    async def get_attribute(self, name: str):
        return None

    async def check(self):
        self.checked = True


@pytest.mark.asyncio
async def test_answer_radios_picks_exact_no_not_none_of_the_above():
    none_radio = _FakeRadio("None of the above")
    not_sure_radio = _FakeRadio("Not sure")
    no_radio = _FakeRadio("No")
    group = _FakeRadioGroup(
        "Will you require visa sponsorship?",
        [none_radio, not_sure_radio, no_radio],
    )
    page = _FakePage([group])

    answered = await _answer_yesno_radios(page, UserProfile())

    assert no_radio.checked is True
    assert none_radio.checked is False
    assert not_sure_radio.checked is False
    assert len(answered) == 1


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


# ---------------------------------------------------------------------------
# Acknowledgment checkboxes — auto-checks must be surfaced for review
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_auto_checked_acknowledgments_are_tagged_for_review():
    ack = _FakeCheckbox("I acknowledge the privacy policy")
    marketing = _FakeCheckbox("I agree to receive marketing newsletters")
    page = _FakePage([ack, marketing])

    checked = await _check_required_acknowledgments(page)

    assert ack.checked is True
    assert marketing.checked is False
    assert len(checked) == 1
    assert "I acknowledge the privacy policy" in checked[0]
    assert "[checked for you — please read]" in checked[0]
