"""
Extended ProfileStore tests beyond test_core.py basics.

Covers edge cases: missing fields, defaults, field mapping, update method.
"""

import json
from pathlib import Path

import pytest

from jobpilot.core.profile_store import ProfileStore, UserProfile


@pytest.fixture()
def store(tmp_path: Path) -> ProfileStore:
    profile_data = {
        "first_name": "Jane",
        "last_name": "Doe",
        "email": "jane@example.com",
        "phone": "555-0000",
        "city": "Austin",
        "state": "TX",
        "country": "United States",
        "zip_code": "78701",
        "linkedin_url": "https://linkedin.com/in/janedoe",
        "portfolio_url": "https://janedoe.dev",
        "github_url": "https://github.com/janedoe",
        "resume_path": "/tmp/resume.pdf",
        "authorized_to_work": True,
        "requires_sponsorship": False,
        "years_of_experience": 8,
        "current_title": "Staff Engineer",
        "current_company": "BigTech",
        "desired_salary": "200k",
        "custom_answers": {"clearance": "No"},
    }
    (tmp_path / "profile.json").write_text(json.dumps(profile_data))
    return ProfileStore(data_dir=tmp_path)


class TestFieldMapping:
    def test_email_field(self, store: ProfileStore):
        assert store.get_field_value("email") == "jane@example.com"

    def test_phone_field(self, store: ProfileStore):
        assert store.get_field_value("phone") == "555-0000"

    def test_first_name_field(self, store: ProfileStore):
        assert store.get_field_value("first_name") == "Jane"

    def test_linkedin_url(self, store: ProfileStore):
        assert "linkedin.com" in store.get_field_value("linkedin")

    def test_unknown_field_returns_empty(self, store: ProfileStore):
        val = store.get_field_value("nonexistent_xyz_field")
        assert val is None or val == ""


class TestUpdate:
    def test_update_changes_field(self, store: ProfileStore):
        store.load()
        store.update(first_name="Janet")
        profile = store.load()
        assert profile.first_name == "Janet"


class TestDefaults:
    def test_empty_dir_creates_default_profile(self, tmp_path: Path):
        s = ProfileStore(data_dir=tmp_path)
        p = s.load()
        assert isinstance(p, UserProfile)
        assert p.first_name == ""
        assert p.years_of_experience == 0


class TestCustomAnswers:
    def test_custom_answers_loaded(self, store: ProfileStore):
        profile = store.load()
        assert profile.custom_answers.get("clearance") == "No"


def test_save_keeps_fields_the_model_does_not_declare(tmp_path):
    (tmp_path / "profile.json").write_text(
        json.dumps(
            {
                "first_name": "Ada",
                "skills": ["Python"],
                "target_titles": ["Solutions Engineer"],
                "domain_experience": {"field service": 4},
                "future_key": {"nested": True},
            }
        )
    )
    store = ProfileStore(data_dir=tmp_path)
    profile = store.load()
    profile.first_name = "Ada L."
    store.save(profile)

    saved = json.loads((tmp_path / "profile.json").read_text())
    assert saved["first_name"] == "Ada L."
    assert saved["skills"] == ["Python"]
    assert saved["target_titles"] == ["Solutions Engineer"]
    assert saved["domain_experience"] == {"field service": 4}
    assert saved["future_key"] == {"nested": True}


def test_unreadable_profile_is_backed_up_before_overwrite(tmp_path):
    (tmp_path / "profile.json").write_text("{broken")
    store = ProfileStore(data_dir=tmp_path)
    store.load()
    store.update(first_name="Ada")

    assert (tmp_path / "profile.json.bak").read_text() == "{broken"
    assert json.loads((tmp_path / "profile.json").read_text())["first_name"] == "Ada"
