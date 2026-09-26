"""The settings file: ``jobpilot.toml`` in the data folder.

The engine reads this file and never rewrites it, so the user's edits and
comments are always safe. ``render_settings`` writes a new, commented file only
when one is being created.
"""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Iterable, Mapping
from pathlib import Path

from jobpilot.engine.domain import Settings, SettingsError, parse_settings

SETTINGS_FILENAME = "jobpilot.toml"
_BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")


class TomlSettings:
    """SettingsSource backed by one TOML file."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    @classmethod
    def in_data_dir(cls, data_dir: Path) -> TomlSettings:
        return cls(Path(data_dir) / SETTINGS_FILENAME)

    def load(self) -> tuple[Settings, list[str]]:
        if not self.path.exists():
            return Settings(), [
                f"No settings file at {self.path}, so defaults apply. "
                "Create one with `jobpilot settings init`."
            ]
        try:
            data = tomllib.loads(self.path.read_text())
        except tomllib.TOMLDecodeError as exc:
            raise SettingsError([f"{self.path.name} isn't valid TOML: {exc}"]) from exc
        return parse_settings(data)


def _value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    raise TypeError(f"can't write {type(value).__name__} to settings")


def _key(key: str) -> str:
    return key if _BARE_KEY.fullmatch(key) else json.dumps(key, ensure_ascii=False)


def _list(name: str, items: Iterable[str]) -> list[str]:
    items = list(items)
    if not items:
        return [f"{name} = []"]
    return [f"{name} = [", *(f"    {_value(item)}," for item in items), "]"]


def _table(header: str, mapping: Mapping[str, object]) -> list[str]:
    return [
        f"[{header}]",
        *(f"{_key(key)} = {_value(value)}" for key, value in mapping.items()),
    ]


def render_settings(settings: Settings) -> str:
    """A complete, commented settings file for the given settings."""
    profile, targets, rules = settings.profile, settings.targets, settings.rules
    lines = [
        "# JobPilot settings.",
        "# The engine reads this file and never rewrites it, so your edits and",
        "# comments are safe. Check it any time with: jobpilot settings check",
        "",
        "[profile]",
        f"name = {_value(profile.name)}",
        f"email = {_value(profile.email)}",
        f"phone = {_value(profile.phone)}",
        f"city = {_value(profile.city)}",
        f"work_authorized = {_value(profile.work_authorized)}",
        f"needs_sponsorship = {_value(profile.needs_sponsorship)}",
        "# Languages you can work in. Roles that require another language are skipped.",
        *_list("languages", profile.languages),
        "",
        *_table("profile.links", profile.links),
        "",
        "[targets]",
        "# Role titles you're aiming for.",
        *_list("titles", targets.titles),
        *_list("skills", targets.skills),
        "",
        "# Years of experience by domain.",
        *_table("targets.domain_experience", targets.domain_experience),
        "",
        "[rules.location]",
        "# Places you'd work on-site or hybrid.",
        *_list("home", rules.location.home),
        '# Remote roles you\'ll take: "us", "anywhere", or "none".',
        f"remote = {_value(rules.location.remote.value)}",
        f"relocate = {_value(rules.location.relocate)}",
        "",
        "[rules.level]",
        '# Titles containing any of these words are skipped, e.g. "senior", "staff".',
        *_list("exclude_titles", rules.level.exclude_titles),
        "# Skip postings that require more years than this. 0 means no limit.",
        f"max_years_required = {_value(rules.level.max_years_required)}",
        "",
        "[rules.companies]",
        "# Companies you won't work for.",
        *_list("exclude", rules.companies.exclude),
        "# Rank large, established companies lower.",
        f"avoid_large = {_value(rules.companies.avoid_large)}",
        "",
        "[rules.pay]",
        "# Minimum annual pay in USD. 0 means no floor.",
        f"floor = {_value(rules.pay_floor)}",
        "",
        "[rules.travel]",
        "# The most travel you'll accept, as a percent of working time.",
        f"max_percent = {_value(rules.max_travel_percent)}",
        "",
    ]
    return "\n".join(lines)
