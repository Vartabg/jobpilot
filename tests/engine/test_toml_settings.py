import pytest

from jobpilot.engine.adapters.toml_settings import TomlSettings, render_settings
from jobpilot.engine.domain import (
    CompanyRules,
    LevelRules,
    LocationRules,
    Profile,
    Remote,
    Rules,
    Settings,
    SettingsError,
    Targets,
)

RICH = Settings(
    profile=Profile(
        name='Zoë "Z" O\'Neil',
        email="zoe@example.com",
        phone="555-0100",
        city="Austin, TX",
        links={
            "github": "https://github.com/zoe",
            "personal site": "https://zoe.example",
        },
        languages=("English", "Armenian"),
    ),
    targets=Targets(
        titles=("Forward Deployed Engineer", "Solutions Engineer"),
        skills=("Python", "C#", "back\\slash"),
        domain_experience={"field service": 4, "sales & biz-dev": 2},
    ),
    rules=Rules(
        location=LocationRules(
            home=("Austin, TX",), remote=Remote.ANYWHERE, relocate=True
        ),
        level=LevelRules(exclude_titles=("senior", "staff"), max_years_required=4),
        companies=CompanyRules(exclude=("Initech",), avoid_large=True),
        pay_floor=90_000,
        max_travel_percent=40,
    ),
)


def test_rendered_files_load_back_exactly(tmp_path):
    path = tmp_path / "jobpilot.toml"
    path.write_text(render_settings(RICH))
    settings, warnings = TomlSettings(path).load()
    assert warnings == []
    assert settings == RICH


def test_default_settings_render_and_load(tmp_path):
    path = tmp_path / "jobpilot.toml"
    path.write_text(render_settings(Settings()))
    assert TomlSettings(path).load() == (Settings(), [])


def test_the_file_explains_itself():
    text = render_settings(Settings())
    assert "never rewrites it" in text
    assert "0 means no limit" in text


def test_missing_file_means_defaults_with_a_hint(tmp_path):
    settings, warnings = TomlSettings.in_data_dir(tmp_path).load()
    assert settings == Settings()
    assert "jobpilot settings init" in warnings[0]


def test_invalid_toml_is_a_settings_error(tmp_path):
    path = tmp_path / "jobpilot.toml"
    path.write_text("[rules\nremote = ")
    with pytest.raises(SettingsError, match="isn't valid TOML"):
        TomlSettings(path).load()
