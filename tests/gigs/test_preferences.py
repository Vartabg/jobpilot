"""Identity resolution in gigs preferences — one identity, two lanes.

Per-key resolution order:

1. explicit identity values in data/gigs/preferences.json,
2. jobpilot's UserProfile (data/profile.json via core.profile_store),
3. the neutral shipped DEFAULTS.

Every test mocks the profile store (autouse fixture, empty profile by
default) so the developer's real data/profile.json can never leak into
test behavior or assertions.
"""

from __future__ import annotations

import builtins
import json
from pathlib import Path

import pytest

from jobpilot.core import profile_store as profile_store_module
from jobpilot.core.profile_store import UserProfile
from jobpilot.gigs.core import preferences


class _FakeStore:
    def __init__(self, profile: UserProfile) -> None:
        self._profile = profile

    def load(self) -> UserProfile:
        return self._profile


def _mock_profile(monkeypatch: pytest.MonkeyPatch, **fields) -> UserProfile:
    profile = UserProfile(**fields)
    monkeypatch.setattr(
        profile_store_module, "get_profile_store", lambda: _FakeStore(profile)
    )
    return profile


@pytest.fixture(autouse=True)
def _no_real_profile(monkeypatch: pytest.MonkeyPatch):
    """Default every test to an empty jobpilot profile (no real data)."""
    _mock_profile(monkeypatch)


def _missing(tmp_path: Path) -> Path:
    return tmp_path / "preferences.json"


# ---------------------------------------------------------------------------
# Layer 2: fall through to the jobpilot UserProfile
# ---------------------------------------------------------------------------


def test_identity_falls_through_to_profile_when_prefs_lack_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_profile(
        monkeypatch,
        first_name="Ada",
        last_name="Lovelace",
        email="ada@example.org",
        phone="555-0100",
        city="Queens",
        state="NY",
        linkedin_url="https://www.linkedin.com/in/ada",
        portfolio_url="https://ada.example.org",
        github_url="https://github.com/ada",
    )
    ident = preferences.identity(preferences.load(_missing(tmp_path)))
    assert ident["first_name"] == "Ada"
    assert ident["last_name"] == "Lovelace"
    assert ident["email"] == "ada@example.org"
    assert ident["phone"] == "555-0100"
    assert ident["city"] == "Queens, NY"
    assert ident["linkedin"] == "https://www.linkedin.com/in/ada"
    assert ident["github"] == "https://github.com/ada"
    assert ident["portfolio"] == "https://ada.example.org"
    # Keys the profile doesn't carry keep their neutral defaults.
    assert ident["phone_note"] == preferences.DEFAULTS["identity"]["phone_note"]
    assert ident["tagline"] == preferences.DEFAULTS["identity"]["tagline"]


def test_blank_profile_fields_do_not_shadow_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Only non-empty profile values are used; the rest stay neutral.
    _mock_profile(monkeypatch, first_name="Ada", email="   ")
    ident = preferences.identity(preferences.load(_missing(tmp_path)))
    assert ident["first_name"] == "Ada"
    assert ident["email"] == preferences.DEFAULTS["identity"]["email"]
    assert ident["city"] == preferences.DEFAULTS["identity"]["city"]


def test_city_state_composition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Both parts -> "City, ST"; a lone part -> no stray comma.
    _mock_profile(monkeypatch, city="Queens", state="NY")
    assert preferences.identity(preferences.load(_missing(tmp_path)))["city"] == "Queens, NY"

    _mock_profile(monkeypatch, city="Queens", state="")
    assert preferences.identity(preferences.load(_missing(tmp_path)))["city"] == "Queens"

    _mock_profile(monkeypatch, city="", state="NY")
    assert preferences.identity(preferences.load(_missing(tmp_path)))["city"] == "NY"


# ---------------------------------------------------------------------------
# Layer 1: explicit preferences win over the profile
# ---------------------------------------------------------------------------


def test_explicit_preferences_win_over_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_profile(
        monkeypatch,
        first_name="Ada",
        last_name="Lovelace",
        email="ada@example.org",
        city="Queens",
        state="NY",
    )
    path = _missing(tmp_path)
    path.write_text(
        json.dumps({"identity": {"email": "consulting@example.net", "city": "Remote"}})
    )
    ident = preferences.identity(preferences.load(path))
    # Explicit keys win ...
    assert ident["email"] == "consulting@example.net"
    assert ident["city"] == "Remote"
    # ... while unset keys still fall through to the profile.
    assert ident["first_name"] == "Ada"
    assert ident["last_name"] == "Lovelace"


def test_seeded_placeholder_file_does_not_shadow_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `gigs/cli.py` seeds preferences.json with the neutral placeholders via
    # write_default_if_missing. Placeholder-equal values carry no information
    # and must not block the profile fallback.
    path = _missing(tmp_path)
    assert preferences.write_default_if_missing(path) is True
    _mock_profile(monkeypatch, first_name="Ada", email="ada@example.org")
    ident = preferences.identity(preferences.load(path))
    assert ident["first_name"] == "Ada"
    assert ident["email"] == "ada@example.org"


# ---------------------------------------------------------------------------
# Layer 3: neutral defaults when both are absent
# ---------------------------------------------------------------------------


def test_neutral_defaults_when_prefs_and_profile_absent(tmp_path: Path) -> None:
    # Autouse fixture already mocks an empty UserProfile.
    prefs = preferences.load(_missing(tmp_path))
    assert prefs["identity"] == preferences.DEFAULTS["identity"]
    # Non-identity sections are untouched by the profile layering.
    assert prefs["pay"] == preferences.DEFAULTS["pay"]
    assert prefs["background_bullets"] == preferences.DEFAULTS["background_bullets"]


def test_defaults_object_is_not_mutated_by_load(tmp_path: Path, monkeypatch) -> None:
    _mock_profile(monkeypatch, first_name="Ada")
    before = json.dumps(preferences.DEFAULTS, sort_keys=True)
    preferences.load(_missing(tmp_path))
    assert json.dumps(preferences.DEFAULTS, sort_keys=True) == before


# ---------------------------------------------------------------------------
# Standalone safety: gigs core works without the jobpilot package
# ---------------------------------------------------------------------------


def test_identity_survives_jobpilot_core_import_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_import = builtins.__import__

    def _no_jobpilot_core(name, *args, **kwargs):
        if name == "jobpilot.core" or name.startswith("jobpilot.core.profile_store"):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_jobpilot_core)
    prefs = preferences.load(_missing(tmp_path))
    assert prefs["identity"] == preferences.DEFAULTS["identity"]


def test_identity_survives_profile_store_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom():
        raise RuntimeError("profile store unavailable")

    monkeypatch.setattr(profile_store_module, "get_profile_store", _boom)
    prefs = preferences.load(_missing(tmp_path))
    assert prefs["identity"] == preferences.DEFAULTS["identity"]


# ---------------------------------------------------------------------------
# Caller-facing surface stays intact
# ---------------------------------------------------------------------------


def test_signoff_block_uses_resolved_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_profile(
        monkeypatch,
        first_name="Ada",
        last_name="Lovelace",
        phone="555-0100",
        linkedin_url="https://www.linkedin.com/in/ada",
        portfolio_url="https://ada.example.org",
    )
    block = preferences.signoff_block(preferences.load(_missing(tmp_path)))
    assert "Ada Lovelace" in block
    assert "555-0100" in block
    assert "https://www.linkedin.com/in/ada | https://ada.example.org" in block
