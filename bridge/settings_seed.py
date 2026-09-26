"""Build first settings from what the legacy lanes already know.

Sources, all read-only: the jobs lane's profile.json and optional policy.json,
and the gigs lane's preferences.json. Anything they don't cover keeps a neutral
default for the user to fill in.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from jobpilot.engine.domain import (
    CompanyRules,
    LevelRules,
    LocationRules,
    Profile,
    Remote,
    Rules,
    Settings,
    Targets,
)


def _merge(*groups: Iterable[Any] | None) -> tuple[str, ...]:
    """Order-preserving, case-insensitive union of text lists."""
    seen: set[str] = set()
    merged: list[str] = []
    for group in groups:
        for item in group or ():
            if (
                isinstance(item, str)
                and item.strip()
                and item.strip().lower() not in seen
            ):
                seen.add(item.strip().lower())
                merged.append(item.strip())
    return tuple(merged)


def _table(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _public_keys(value: Any) -> list[str]:
    """Keys of a legacy policy map, without its ``_doc`` annotations."""
    return [key for key in _table(value) if not key.startswith("_")]


def seed_settings(
    profile: Mapping[str, Any],
    preferences: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> Settings:
    identity = _table(preferences.get("identity"))
    location = _table(preferences.get("location"))
    search = _table(preferences.get("search"))
    queue_policy = _table(policy.get("queue"))
    scoring_policy = _table(policy.get("scoring"))
    gate = _table(queue_policy.get("location_gate"))

    def pick(profile_key: str, identity_key: str) -> str:
        value = profile.get(profile_key) or identity.get(identity_key) or ""
        return value.strip() if isinstance(value, str) else ""

    name = " ".join(
        part
        for part in (pick("first_name", "first_name"), pick("last_name", "last_name"))
        if part
    )
    city = ", ".join(
        part
        for part in (profile.get("city"), profile.get("state"))
        if isinstance(part, str) and part
    )
    city = city or pick("", "city")
    links = {
        label: value
        for label, value in (
            ("linkedin", pick("linkedin_url", "linkedin")),
            ("github", pick("github_url", "github")),
            ("portfolio", pick("portfolio_url", "portfolio")),
        )
        if value
    }
    domain_experience = {
        str(domain): years
        for domain, years in _table(profile.get("domain_experience")).items()
        if isinstance(years, int) and not isinstance(years, bool) and years >= 0
    }
    home = _merge([city] if city else location.get("home_metro_tags"))
    if gate.get("enabled"):
        home = _merge(home, gate.get("allowed_locations"))

    return Settings(
        profile=Profile(
            name=name,
            email=pick("email", "email"),
            phone=pick("phone", "phone"),
            city=city,
            links=links,
            work_authorized=bool(profile.get("authorized_to_work", True)),
            needs_sponsorship=bool(profile.get("requires_sponsorship", False)),
        ),
        targets=Targets(
            titles=_merge(profile.get("target_titles"), search.get("target_titles")),
            skills=_merge(profile.get("skills")),
            domain_experience=domain_experience,
        ),
        rules=Rules(
            location=LocationRules(
                home=home,
                remote=Remote.US if location.get("allow_remote", True) else Remote.NONE,
                relocate=bool(profile.get("open_to_relocation", False)),
            ),
            level=LevelRules(
                exclude_titles=_merge(
                    queue_policy.get("title_kill_keywords"),
                    _public_keys(scoring_policy.get("refused_title_keywords")),
                )
            ),
            companies=CompanyRules(
                exclude=_merge(_public_keys(scoring_policy.get("refused_companies")))
            ),
        ),
    )
