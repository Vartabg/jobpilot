"""The user's settings: who they are, what they're aiming for, and their hard rules.

``parse_settings`` turns a plain mapping (the parsed settings file) into typed
settings. It reports every problem at once with its key path, and keeps keys it
doesn't recognize as ``extras``, with a warning, instead of dropping them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, TypeVar


class Remote(StrEnum):
    """Which remote roles the user will take."""

    US = "us"
    ANYWHERE = "anywhere"
    NONE = "none"


class FitEngine(StrEnum):
    """How fit is judged: fixed rules only, or a local model through Ollama."""

    RULES = "rules"
    OLLAMA = "ollama"


@dataclass(frozen=True)
class Profile:
    name: str = ""
    email: str = ""
    phone: str = ""
    city: str = ""
    links: Mapping[str, str] = field(default_factory=dict)
    work_authorized: bool = True
    needs_sponsorship: bool = False
    languages: tuple[str, ...] = ()
    education: tuple[str, ...] = ()


@dataclass(frozen=True)
class Targets:
    titles: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    domain_experience: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class LocationRules:
    home: tuple[str, ...] = ()
    remote: Remote = Remote.US
    relocate: bool = False


@dataclass(frozen=True)
class LevelRules:
    exclude_titles: tuple[str, ...] = ()
    max_years_required: int = 0  # 0 means no limit


@dataclass(frozen=True)
class CompanyRules:
    exclude: tuple[str, ...] = ()
    avoid_large: bool = False


@dataclass(frozen=True)
class Rules:
    location: LocationRules = field(default_factory=LocationRules)
    level: LevelRules = field(default_factory=LevelRules)
    companies: CompanyRules = field(default_factory=CompanyRules)
    pay_floor: int = 0  # annual USD; 0 means no floor
    max_travel_percent: int = 100


@dataclass(frozen=True)
class FitSettings:
    engine: FitEngine = FitEngine.RULES
    model: str = ""


@dataclass(frozen=True)
class Settings:
    profile: Profile = field(default_factory=Profile)
    targets: Targets = field(default_factory=Targets)
    rules: Rules = field(default_factory=Rules)
    fit: FitSettings = field(default_factory=FitSettings)
    # Keys this version doesn't understand, by dotted path, kept verbatim.
    extras: Mapping[str, Any] = field(default_factory=dict)


class SettingsError(ValueError):
    """The settings can't be used. ``problems`` lists every issue found."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


_Choice = TypeVar("_Choice", bound=StrEnum)


class _Table:
    """Reads one table of the settings, recording problems and unknown keys."""

    def __init__(
        self, data: Any, path: str, problems: list[str], extras: dict[str, Any]
    ) -> None:
        self.path, self.problems, self.extras = path, problems, extras
        self.used: set[str] = set()
        if data is None:
            data = {}
        if not isinstance(data, Mapping):
            problems.append(f"{path} must be a table")
            data = {}
        self.data: Mapping[str, Any] = data

    def _where(self, key: str) -> str:
        return f"{self.path}.{key}" if self.path else key

    def _get(self, key: str) -> Any:
        self.used.add(key)
        return self.data.get(key)

    def text(self, key: str, default: str = "") -> str:
        value = self._get(key)
        if value is None:
            return default
        if not isinstance(value, str):
            self.problems.append(f"{self._where(key)} must be text")
            return default
        return value.strip()

    def flag(self, key: str, default: bool) -> bool:
        value = self._get(key)
        if value is None:
            return default
        if not isinstance(value, bool):
            self.problems.append(f"{self._where(key)} must be true or false")
            return default
        return value

    def whole(self, key: str, default: int, low: int, high: int) -> int:
        value = self._get(key)
        if value is None:
            return default
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not low <= value <= high
        ):
            self.problems.append(
                f"{self._where(key)} must be a whole number from {low} to {high}"
            )
            return default
        return value

    def texts(self, key: str) -> tuple[str, ...]:
        value = self._get(key)
        if value is None:
            return ()
        if not isinstance(value, list) or not all(
            isinstance(item, str) for item in value
        ):
            self.problems.append(f"{self._where(key)} must be a list of text")
            return ()
        return tuple(item.strip() for item in value if item.strip())

    def labels(self, key: str) -> Mapping[str, str]:
        table = self.table(key)
        found: dict[str, str] = {}
        for name, value in table.data.items():
            table.used.add(name)
            if isinstance(value, str):
                found[name] = value.strip()
            else:
                self.problems.append(f"{table._where(name)} must be text")
        return MappingProxyType(found)

    def counts(self, key: str) -> Mapping[str, int]:
        table = self.table(key)
        found: dict[str, int] = {}
        for name, value in table.data.items():
            table.used.add(name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                self.problems.append(
                    f"{table._where(name)} must be a whole number of years"
                )
            else:
                found[name] = value
        return MappingProxyType(found)

    def choice(self, key: str, options: type[_Choice], default: _Choice) -> _Choice:
        value = self.text(key, default.value).lower()
        try:
            return options(value)
        except ValueError:
            allowed = ", ".join(f'"{option.value}"' for option in options)
            self.problems.append(f"{self._where(key)} must be one of {allowed}")
            return default

    def table(self, key: str) -> _Table:
        return _Table(self._get(key), self._where(key), self.problems, self.extras)

    def finish(self) -> None:
        for key, value in self.data.items():
            if key not in self.used:
                self.extras[self._where(key)] = value


def parse_settings(data: Mapping[str, Any]) -> tuple[Settings, list[str]]:
    """Typed settings plus warnings about unknown keys. Raises SettingsError on problems."""
    problems: list[str] = []
    extras: dict[str, Any] = {}
    root = _Table(data, "", problems, extras)
    finish: list[_Table] = [root]

    def table(parent: _Table, key: str) -> _Table:
        child = parent.table(key)
        finish.append(child)
        return child

    profile_table = table(root, "profile")
    email = profile_table.text("email")
    if email and "@" not in email:
        problems.append("profile.email doesn't look like an email address")
    profile = Profile(
        name=profile_table.text("name"),
        email=email,
        phone=profile_table.text("phone"),
        city=profile_table.text("city"),
        links=profile_table.labels("links"),
        work_authorized=profile_table.flag("work_authorized", True),
        needs_sponsorship=profile_table.flag("needs_sponsorship", False),
        languages=profile_table.texts("languages"),
        education=profile_table.texts("education"),
    )

    targets_table = table(root, "targets")
    targets = Targets(
        titles=targets_table.texts("titles"),
        skills=targets_table.texts("skills"),
        domain_experience=targets_table.counts("domain_experience"),
    )

    rules_table = table(root, "rules")
    location = table(rules_table, "location")
    level = table(rules_table, "level")
    companies = table(rules_table, "companies")
    pay = table(rules_table, "pay")
    travel = table(rules_table, "travel")
    rules = Rules(
        location=LocationRules(
            home=location.texts("home"),
            remote=location.choice("remote", Remote, Remote.US),
            relocate=location.flag("relocate", False),
        ),
        level=LevelRules(
            exclude_titles=level.texts("exclude_titles"),
            max_years_required=level.whole("max_years_required", 0, 0, 40),
        ),
        companies=CompanyRules(
            exclude=companies.texts("exclude"),
            avoid_large=companies.flag("avoid_large", False),
        ),
        pay_floor=pay.whole("floor", 0, 0, 10_000_000),
        max_travel_percent=travel.whole("max_percent", 100, 0, 100),
    )

    fit_table = table(root, "fit")
    fit = FitSettings(
        engine=fit_table.choice("engine", FitEngine, FitEngine.RULES),
        model=fit_table.text("model"),
    )
    if fit.engine is FitEngine.OLLAMA and not fit.model:
        problems.append('fit.model is required when fit.engine is "ollama"')

    for reader in finish:
        reader.finish()
    if problems:
        raise SettingsError(problems)
    warnings = [
        f"unknown setting {path} (kept, but not used)" for path in sorted(extras)
    ]
    settings = Settings(
        profile=profile,
        targets=targets,
        rules=rules,
        fit=fit,
        extras=MappingProxyType(dict(extras)),
    )
    return settings, warnings
