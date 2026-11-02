import json
import stat

import pytest
from typer.testing import CliRunner

from jobpilot.bridge.settings_cli import app
from jobpilot.bridge.settings_seed import seed_settings
from jobpilot.engine.domain import Remote

PROFILE = {
    "first_name": "Ada",
    "last_name": "Lovelace",
    "email": "ada@example.com",
    "phone": "555-0100",
    "city": "Austin",
    "state": "TX",
    "linkedin_url": "https://linkedin.com/in/ada",
    "github_url": "",
    "portfolio_url": "https://ada.example",
    "authorized_to_work": True,
    "requires_sponsorship": False,
    "open_to_relocation": False,
    "target_titles": ["Solutions Engineer", "Forward Deployed Engineer"],
    "skills": ["Python", "python", "SQL"],
    "domain_experience": {"field service": 4, "bogus": "four"},
}
PREFERENCES = {
    "identity": {"github": "https://github.com/ada", "city": "Elsewhere"},
    "location": {"home_metro_tags": ["austin"], "allow_remote": True},
    "search": {"target_titles": ["solutions engineer", "Implementation Engineer"]},
}
POLICY = {
    "scoring": {
        "refused_companies": {"_doc": "why", "Initech": "reason"},
        "refused_title_keywords": {"staff": "reason"},
    },
    "queue": {
        "title_kill_keywords": ["senior"],
        "location_gate": {"enabled": True, "allowed_locations": ["Remote US"]},
    },
}


class TestSeed:
    def test_seed_from_every_source(self):
        settings = seed_settings(PROFILE, PREFERENCES, POLICY)
        assert settings.profile.name == "Ada Lovelace"
        assert settings.profile.city == "Austin, TX"
        assert settings.profile.links == {
            "linkedin": "https://linkedin.com/in/ada",
            "github": "https://github.com/ada",  # the gigs identity fills the gap
            "portfolio": "https://ada.example",
        }
        assert settings.targets.titles == (
            "Solutions Engineer",
            "Forward Deployed Engineer",
            "Implementation Engineer",
        )
        assert settings.targets.skills == ("Python", "SQL")  # case-insensitive dedupe
        assert settings.targets.domain_experience == {
            "field service": 4
        }  # bad value dropped
        assert settings.rules.location.home == ("Austin, TX", "Remote US")
        assert settings.rules.location.remote is Remote.US
        assert settings.rules.level.exclude_titles == ("senior", "staff")
        assert settings.rules.companies.exclude == ("Initech",)

    def test_seed_with_nothing_known_is_neutral(self):
        settings = seed_settings({}, {}, {})
        assert settings.profile.name == ""
        assert settings.rules.level.exclude_titles == ()
        assert settings.rules.location.remote is Remote.US


@pytest.fixture
def legacy(tmp_path):
    files = {"profile": PROFILE, "preferences": PREFERENCES, "policy": POLICY}
    options = []
    for name, data in files.items():
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(data))
        options += [f"--{name}", str(path)]
    return options


class TestCli:
    def test_init_creates_a_private_file_once(self, tmp_path, legacy):
        target = tmp_path / "data" / "jobpilot.toml"
        runner = CliRunner()
        created = runner.invoke(app, ["init", "--path", str(target), *legacy])
        assert created.exit_code == 0, created.output
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
        original = target.read_text()

        again = runner.invoke(app, ["init", "--path", str(target), *legacy])
        assert again.exit_code == 1
        assert "already exists" in again.output
        assert target.read_text() == original  # never overwritten

    def test_print_writes_nothing(self, tmp_path, legacy):
        target = tmp_path / "jobpilot.toml"
        result = CliRunner().invoke(
            app, ["init", "--print", "--path", str(target), *legacy]
        )
        assert result.exit_code == 0, result.output
        assert "[rules.level]" in result.output
        assert not target.exists()

    def test_check_describes_rules_in_plain_words(self, tmp_path, legacy):
        target = tmp_path / "jobpilot.toml"
        runner = CliRunner()
        runner.invoke(app, ["init", "--path", str(target), *legacy])
        result = runner.invoke(app, ["check", "--path", str(target)])
        assert result.exit_code == 0, result.output
        assert (
            "Location: Austin, TX, Remote US · remote: US only · relocate: no"
            in result.output
        )
        assert "Won't work for: Initech" in result.output

    def test_check_lists_every_problem(self, tmp_path):
        target = tmp_path / "jobpilot.toml"
        target.write_text(
            '[rules.location]\nremote = "mars"\n[rules.travel]\nmax_percent = 500\n'
        )
        result = CliRunner().invoke(app, ["check", "--path", str(target)])
        assert result.exit_code == 1
        assert "rules.location.remote" in result.output
        assert "rules.travel.max_percent" in result.output
