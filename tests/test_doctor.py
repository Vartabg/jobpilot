"""Tests for core/doctor.py — JobPilot data integrity checks."""

from pathlib import Path

from jobpilot.core.application_tracker import ApplicationTracker
from jobpilot.core.profile_store import ProfileStore
from jobpilot.learning.learning_db import LearningDB
from jobpilot.core.doctor import run_doctor


def test_doctor_reports_ok_for_healthy_setup(tmp_path: Path):
    resume = tmp_path / "resume.pdf"
    resume.write_text("fake pdf")

    profile_store = ProfileStore(data_dir=tmp_path)
    profile = profile_store.load()
    profile.first_name = "Garo"
    profile.resume_path = str(resume)
    profile_store.save(profile)

    tracker = ApplicationTracker(data_dir=tmp_path)
    tracker.mark_started("https://linkedin.com/jobs/view/1", "Engineer", "Acme")
    tracker.mark_submitted("https://linkedin.com/jobs/view/1")
    tracker.close()

    db = LearningDB(db_path=tmp_path / "learning.db")
    db.upsert_template("Why do you want this role?", "Because it matches my background.")
    db.record_action(
        action_type="field_approved",
        field_label="Why do you want this role?",
        suggested_value="Because it matches my background.",
        final_value="Because it matches my background.",
        confidence=0.92,
    )
    db.close()

    report = run_doctor(data_dir=tmp_path, check_bro=False)

    assert report.status == "ok"
    assert report.summary["applications"] == 1
    assert report.summary["templates"] == 1
    assert not report.errors


def test_doctor_flags_missing_resume_and_bad_status(tmp_path: Path):
    profile_store = ProfileStore(data_dir=tmp_path)
    profile = profile_store.load()
    profile.resume_path = str(tmp_path / "missing_resume.pdf")
    profile_store.save(profile)

    tracker = ApplicationTracker(data_dir=tmp_path)
    conn = tracker._get_conn()
    conn.execute(
        "INSERT INTO applications (job_url, job_title, company, applied_at, status, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        ("https://linkedin.com/jobs/view/2", "Weird Role", "Odd Co", "2026-04-07T00:00:00", "mystery", "2026-04-07T00:00:00"),
    )
    conn.commit()
    tracker.close()

    report = run_doctor(data_dir=tmp_path, check_bro=False)

    assert report.status == "warn"
    assert any("Resume path not found" in item for item in report.warnings)
    assert any("Invalid application statuses" in item for item in report.warnings)
