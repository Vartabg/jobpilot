from pathlib import Path
from unittest.mock import patch

import pytest

from jobpilot.core.form_filler import _preferred_resume_upload, submit_application
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
