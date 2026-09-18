import pytest

from product.drafts import prepare
from product.matching import match
from product.models import JobUpdate, Listing, Profile
from product.store import Store


def test_tracking_survives_refresh_and_reopen(tmp_path):
    store = Store(tmp_path)
    job = Listing(
        id="one",
        company="Example",
        title="Support",
        url="https://example.com/job",
        provider="manual",
    )
    store.save_jobs([job])
    store.update_job(
        "one", JobUpdate(status="applied", note="Sent myself", follow_up="2026-09-14")
    )
    store.save_jobs([job.model_copy(update={"title": "Support specialist"})])
    restored = Store(tmp_path).job("one")
    assert restored.status == "applied"
    assert restored.note == "Sent myself"
    assert restored.title == "Support specialist"
    with pytest.raises(KeyError):
        store.job("missing")


def test_matching_and_drafting_do_not_invent_experience():
    p = Profile(
        full_name="Alex Example",
        resume_text="I wrote Python scripts for a volunteer group.\nI repaired a community laptop.",
        skills=["Python"],
        keywords=["support"],
    )
    job = Listing(
        id="one",
        company="Example",
        title="Support",
        url="",
        provider="manual",
        description="Required: five years of Python experience.\nMust have a degree.",
    )
    fit = match(p, job)
    assert fit.matched == ["Python"]
    assert "Must have a degree." in fit.questions
    draft = prepare(p, job)
    assert "I wrote Python scripts" in draft.text
    assert "I have five years" not in draft.text
    assert "unverified" in draft.text.lower()


def test_profile_and_backup_exclude_other_runtime_files(tmp_path):
    store = Store(tmp_path)
    store.save_profile(Profile(full_name="Alex", resume_text="Some actual experience"))
    exported = store.export()
    assert exported["profile"]["full_name"] == "Alex"
    assert "token" not in exported
    assert set(exported) == {"schema_version", "profile", "jobs", "drafts"}
