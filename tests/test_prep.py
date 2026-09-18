"""Tests for core/interview_prep.py — interview prep brief generation."""

from pathlib import Path

from jobpilot.core.interview_prep import InterviewPrepGenerator
from jobpilot.core.profile_store import ProfileStore


def _store_with_profile(tmp_path: Path, **overrides) -> ProfileStore:
    store = ProfileStore(data_dir=tmp_path)
    profile = store.load()
    profile.first_name = "Alex"
    profile.last_name = "Sample"
    profile.city = "Rivertown"
    profile.state = "OH"
    profile.current_title = "Field Service Engineer"
    profile.current_company = "Example Labs"
    profile.custom_answers = {
        "skills": "troubleshooting field service python electronics"
    }
    for key, value in overrides.items():
        setattr(profile, key, value)
    store.save(profile)
    return store


def test_prep_generates_html_brief(tmp_path: Path):
    store = _store_with_profile(tmp_path)
    generator = InterviewPrepGenerator(
        profile_store=store, output_dir=tmp_path, use_bro=False
    )

    result = generator.generate_from_text(
        "Field Service Engineer. Requirements: troubleshooting, on-site repair, customer support.",
        title="Field Service Engineer",
        company="Acme Corp",
        export_pdf=False,
    )

    assert result.output_path.exists()
    assert result.output_path.suffix == ".html"
    html = result.output_path.read_text()
    assert "Interview Brief" in html
    assert "Acme Corp" in html
    # Every section header renders.
    assert "Likely Questions" in html
    assert "Stories to Prepare" in html
    assert "Ask Them" in html


def test_prep_always_has_star_themes_and_questions(tmp_path: Path):
    store = _store_with_profile(tmp_path)
    generator = InterviewPrepGenerator(
        profile_store=store, output_dir=tmp_path, use_bro=False
    )

    result = generator.generate_from_text("Some role with minimal detail.")

    # STAR themes are fixed and always present (template-only, never fabricated).
    assert len(result.star_themes) == 4
    # Universal questions always seed the list even with a sparse JD.
    assert any("Tell me about yourself" in q for q in result.likely_questions)


def test_prep_flags_onsite_location_mismatch(tmp_path: Path):
    store = _store_with_profile(tmp_path)  # candidate is in Rivertown, OH
    generator = InterviewPrepGenerator(
        profile_store=store, output_dir=tmp_path, use_bro=False
    )

    result = generator.generate_from_text(
        "Service Engineer. This is an on-site role based in Metro City. "
        "Requirements: hardware troubleshooting, travel to customer sites.",
        title="Service Engineer",
        company="Globex",
    )

    joined = " ".join(result.gap_notes).lower()
    assert "rivertown" in joined
    assert "on-site" in joined or "onsite" in joined
    # A location mismatch also adds the 'raise it yourself' reminder.
    assert any("location" in r.lower() for r in result.reminders)


def test_prep_no_location_flag_for_remote_role(tmp_path: Path):
    store = _store_with_profile(tmp_path)
    generator = InterviewPrepGenerator(
        profile_store=store, output_dir=tmp_path, use_bro=False
    )

    result = generator.generate_from_text(
        "Fully remote Field Service coordinator. Requirements: scheduling, dispatch.",
        title="Remote Coordinator",
        company="Acme",
    )

    assert generator._location_note(store.load(), result.fit_result) == ""


def test_prep_writes_latest_manifest(tmp_path: Path):
    store = _store_with_profile(tmp_path)
    generator = InterviewPrepGenerator(
        profile_store=store, output_dir=tmp_path, use_bro=False
    )

    generator.generate_from_text("A role.", title="Role", company="Co")

    manifest = tmp_path / "latest_prep.json"
    assert manifest.exists()
