import pytest

from jobpilot.engine.domain import Remote, Settings, SettingsError, parse_settings

FULL = {
    "profile": {
        "name": "Ada Lovelace",
        "email": "ada@example.com",
        "city": "Austin, TX",
        "links": {"github": "https://github.com/ada"},
        "languages": ["English", "Spanish"],
    },
    "targets": {
        "titles": ["Solutions Engineer"],
        "skills": ["Python", " "],
        "domain_experience": {"field service": 4},
    },
    "rules": {
        "location": {"home": ["Austin, TX"], "remote": "US", "relocate": False},
        "level": {"exclude_titles": ["senior", "staff"], "max_years_required": 4},
        "companies": {"exclude": ["Initech"], "avoid_large": True},
        "pay": {"floor": 0},
        "travel": {"max_percent": 40},
    },
}


def test_empty_settings_are_the_defaults():
    settings, warnings = parse_settings({})
    assert settings == Settings()
    assert warnings == []


def test_a_full_file():
    settings, warnings = parse_settings(FULL)
    assert warnings == []
    assert settings.profile.links == {"github": "https://github.com/ada"}
    assert settings.profile.languages == ("English", "Spanish")
    assert settings.targets.skills == ("Python",)  # blank entries dropped
    assert settings.targets.domain_experience == {"field service": 4}
    assert settings.rules.location.remote is Remote.US  # case-insensitive
    assert settings.rules.level.max_years_required == 4
    assert settings.rules.companies.exclude == ("Initech",)
    assert settings.rules.max_travel_percent == 40


def test_every_problem_is_reported_at_once_with_its_path():
    broken = {
        "profile": {"email": "not-an-email", "work_authorized": "yes"},
        "targets": {"titles": "Solutions Engineer", "domain_experience": {"sales": -1}},
        "rules": {
            "location": {"remote": "mars"},
            "level": {"max_years_required": True},
            "travel": {"max_percent": 150},
        },
    }
    with pytest.raises(SettingsError) as caught:
        parse_settings(broken)
    problems = "\n".join(caught.value.problems)
    for path in (
        "profile.email",
        "profile.work_authorized",
        "targets.titles",
        "targets.domain_experience.sales",
        "rules.location.remote",
        "rules.level.max_years_required",
        "rules.travel.max_percent",
    ):
        assert path in problems
    assert len(caught.value.problems) == 7


def test_unknown_keys_are_kept_and_flagged_not_dropped():
    data = {
        "rules": {"level": {"max_year": 3}, "future": {"on": True}},
        "theme": "dark",
    }
    settings, warnings = parse_settings(data)
    assert dict(settings.extras) == {
        "rules.level.max_year": 3,
        "rules.future": {"on": True},
        "theme": "dark",
    }
    assert len(warnings) == 3 and all("unknown setting" in w for w in warnings)


def test_a_section_that_is_not_a_table():
    with pytest.raises(SettingsError, match="rules must be a table"):
        parse_settings({"rules": ["nope"]})
