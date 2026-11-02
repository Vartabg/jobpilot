"""Hard-rule screening: does a posting break any rule the user set?

``screen`` is pure. Each rule gives a pass, a fail, or an unknown, with a
reason in plain words. A posting passes screening when nothing fails. Unknowns,
such as a remote role that doesn't say which country, are kept and flagged for
review, because hiding a real fit costs more than showing a doubtful one.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from jobpilot.engine.domain.events import normalize_timestamp
from jobpilot.engine.domain.facts import (
    annual_pay_max,
    names_no_city,
    region,
    required_languages,
    required_years,
    travel_percent,
)
from jobpilot.engine.domain.posting import Posting, Workplace
from jobpilot.engine.domain.settings import Remote, Settings

SCREEN = "screen"
SCREEN_VERSION = "screen/1"


class Outcome(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Check:
    rule: str
    outcome: Outcome
    detail: str


@dataclass(frozen=True)
class Screening:
    checks: tuple[Check, ...]

    @property
    def passed(self) -> bool:
        return not self.failures

    @property
    def failures(self) -> tuple[Check, ...]:
        return tuple(check for check in self.checks if check.outcome is Outcome.FAIL)

    @property
    def unknowns(self) -> tuple[Check, ...]:
        return tuple(check for check in self.checks if check.outcome is Outcome.UNKNOWN)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "checks": [
                {
                    "rule": check.rule,
                    "outcome": check.outcome.value,
                    "detail": check.detail,
                }
                for check in self.checks
            ],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Screening:
        return cls(
            tuple(
                Check(item["rule"], Outcome(item["outcome"]), item["detail"])
                for item in data.get("checks", ())
            )
        )


@dataclass(frozen=True)
class Assessment:
    """One judgment about one opportunity, such as a screening.

    ``basis`` fingerprints the inputs (posting text, rules, version), so
    re-assessing unchanged inputs records nothing new.
    """

    opportunity_id: str
    kind: str
    version: str
    assessed_at: str
    result: Mapping[str, Any]
    basis: str
    id: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "assessed_at", normalize_timestamp(self.assessed_at))
        object.__setattr__(self, "result", MappingProxyType(dict(self.result)))
        if not self.id:
            key = json.dumps([self.opportunity_id, self.kind, self.version, self.basis])
            object.__setattr__(
                self, "id", "asm_" + hashlib.sha256(key.encode()).hexdigest()[:24]
            )


def rules_fingerprint(settings: Settings) -> str:
    """Changes whenever anything screening reads from settings changes."""
    material = repr((settings.rules, settings.profile.languages))
    return hashlib.sha256(material.encode()).hexdigest()[:16]


def _word(term: str, text: str) -> bool:
    return (
        re.search(rf"(?<![a-z]){re.escape(term.lower())}(?![a-z])", text.lower())
        is not None
    )


_COMPANY_SUFFIXES = re.compile(
    r"\b(inc|llc|ltd|corp|corporation|co|pbc|gmbh|plc|ai)\b\.?"
)


def _company_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", _COMPANY_SUFFIXES.sub("", name.lower()))


def _location(posting: Posting, settings: Settings) -> Check:
    rules = settings.rules.location
    places = posting.all_locations
    listed = " / ".join(places) or "no location listed"
    for home in rules.home:
        city = home.split(",")[0].strip()
        if city and any(_word(city, place) for place in places):
            return Check("location", Outcome.PASS, f"In {home}")
    remote = posting.workplace is Workplace.REMOTE or (
        posting.workplace is Workplace.UNKNOWN
        and any(_word("remote", place) for place in places)
    )
    if remote:
        if rules.remote is Remote.NONE:
            return Check(
                "location", Outcome.FAIL, "Remote role, and you don't take remote roles"
            )
        if rules.remote is Remote.ANYWHERE:
            return Check("location", Outcome.PASS, "Remote")
        regions = {region(place) for place in places}
        if "us" in regions:
            return Check("location", Outcome.PASS, "Remote in the US")
        if regions == {"non_us"}:
            return Check("location", Outcome.FAIL, f"Remote only from {listed}")
        return Check(
            "location", Outcome.UNKNOWN, f"Remote, but no country stated ({listed})"
        )
    if not places:
        return Check("location", Outcome.UNKNOWN, "No location listed")
    if all(names_no_city(place) for place in places):
        return Check("location", Outcome.UNKNOWN, f"{listed}, with no city given")
    if rules.relocate:
        return Check(
            "location", Outcome.PASS, f"In {listed}, and you're open to relocating"
        )
    kind = {Workplace.ONSITE: "On-site", Workplace.HYBRID: "Hybrid"}.get(
        posting.workplace, "Based"
    )
    home = ", ".join(rules.home) or "not set"
    return Check("location", Outcome.FAIL, f"{kind} in {listed}; your home is {home}")


def _level(posting: Posting, settings: Settings) -> Check:
    for term in settings.rules.level.exclude_titles:
        if _word(term, posting.listing.title):
            return Check("level", Outcome.FAIL, f'Title includes "{term}"')
    return Check("level", Outcome.PASS, "Title level is in range")


def _years(posting: Posting, settings: Settings) -> Check:
    limit = settings.rules.level.max_years_required
    years = required_years(posting.description)
    if years is None:
        return Check("years", Outcome.PASS, "No years requirement stated")
    if limit and years > limit:
        return Check(
            "years", Outcome.FAIL, f"Asks for {years}+ years; your limit is {limit}"
        )
    return Check("years", Outcome.PASS, f"Asks for {years}+ years")


def _language(posting: Posting, settings: Settings) -> Check:
    spoken = {language.lower() for language in settings.profile.languages} | {"english"}
    needed = required_languages(posting.listing.title, posting.description) - spoken
    if needed:
        names = ", ".join(sorted(language.title() for language in needed))
        return Check("language", Outcome.FAIL, f"Requires {names}")
    return Check("language", Outcome.PASS, "No other language required")


def _company(posting: Posting, settings: Settings) -> Check:
    key = _company_key(posting.listing.company)
    for excluded in settings.rules.companies.exclude:
        if key and key == _company_key(excluded):
            return Check("company", Outcome.FAIL, f"You excluded {excluded}")
    return Check("company", Outcome.PASS, "Company not excluded")


def _pay(posting: Posting, settings: Settings) -> Check:
    floor = settings.rules.pay_floor
    top = annual_pay_max(posting.compensation)
    if not floor:
        return Check("pay", Outcome.PASS, "No pay floor set")
    if top is None:
        return Check("pay", Outcome.UNKNOWN, "No annual USD pay listed")
    if top < floor:
        return Check(
            "pay", Outcome.FAIL, f"Tops out at ${top:,}; your floor is ${floor:,}"
        )
    return Check("pay", Outcome.PASS, f"Up to ${top:,}")


def _travel(posting: Posting, settings: Settings) -> Check:
    limit = settings.rules.max_travel_percent
    travel = travel_percent(posting.description)
    if travel is None:
        return Check("travel", Outcome.PASS, "No travel percentage stated")
    if travel > limit:
        return Check(
            "travel", Outcome.FAIL, f"Up to {travel}% travel; your limit is {limit}%"
        )
    return Check("travel", Outcome.PASS, f"Up to {travel}% travel")


def screen(posting: Posting, settings: Settings) -> Screening:
    """Apply every hard rule to one posting."""
    rules = (_location, _level, _years, _language, _company, _pay, _travel)
    return Screening(tuple(rule(posting, settings) for rule in rules))
