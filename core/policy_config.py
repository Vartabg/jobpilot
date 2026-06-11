"""User job-search policy — refusals, deprioritizations, and queue gates.

JobPilot ships policy-neutral: by default nothing is refused, no company is
deprioritized, and the queue location gate is off (every location passes).
Your personal policy lives in ``data/policy.json`` (gitignored, so it never
ships with the repo). Copy the documented template to get started:

    cp docs/policy.example.json data/policy.json

The file is deep-merged over the neutral ``DEFAULTS`` below, so you only
need to write the sections you want to change. Keys starting with ``_``
(like the ``_doc`` strings in the example file) are ignored.
"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from jobpilot.core.config import DATA_DIR
from jobpilot.core.logger import get_logger

log = get_logger(__name__)

POLICY_PATH = DATA_DIR / "policy.json"

# Neutral defaults — a fresh clone refuses nothing, deprioritizes nothing,
# and gates no locations.
DEFAULTS: dict[str, Any] = {
    "scoring": {
        "refused_companies": {},
        "refused_title_keywords": {},
        "deprioritized_companies": {},
    },
    "queue": {
        "title_kill_keywords": [],
        "moat_company_tags": {},
        "high_moat_industries": [],
        "excluded_industries": [],
        "company_hq": {},
        "location_gate": {
            "enabled": False,
            "allowed_locations": [],
            "remote_terms": ["remote"],
            "country_terms": [],
            "blocked_locations": [],
            "blocked_without_country": [],
        },
    },
}


@dataclass(frozen=True)
class ScoringPolicy:
    """Pre-apply fit-scorer policy (consumed by ``core/job_scorer.py``)."""

    refused_companies: dict[str, str] = field(default_factory=dict)
    refused_title_keywords: dict[str, str] = field(default_factory=dict)
    deprioritized_companies: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class LocationGate:
    """Hard location gate for the queue. Disabled (allow-all) by default."""

    enabled: bool = False
    allowed_locations: tuple[str, ...] = ()
    remote_terms: tuple[str, ...] = ("remote",)
    country_terms: tuple[str, ...] = ()
    blocked_locations: tuple[str, ...] = ()
    blocked_without_country: tuple[str, ...] = ()


@dataclass(frozen=True)
class QueuePolicy:
    """Queue-builder policy (consumed by ``core/queue_builder.py``)."""

    title_kill_keywords: tuple[str, ...] = ()
    moat_company_tags: dict[str, str] = field(default_factory=dict)
    high_moat_industries: frozenset = frozenset()
    excluded_industries: frozenset = frozenset()
    company_hq: dict[str, str] = field(default_factory=dict)
    location_gate: LocationGate = field(default_factory=LocationGate)


@dataclass(frozen=True)
class Policy:
    """The full user policy. Built via `policy_from_dict` / `load_policy`."""

    scoring: ScoringPolicy = field(default_factory=ScoringPolicy)
    queue: QueuePolicy = field(default_factory=QueuePolicy)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``override`` into a copy of ``base``."""
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _reason_map(value: Any) -> dict[str, str]:
    """Normalize a list of names or a ``{name: reason}`` map.

    Returns ``{lowercased name: reason}``; a plain list yields empty reasons.
    Keys starting with ``_`` (doc strings) are dropped.
    """
    if isinstance(value, dict):
        return {
            str(name).strip().lower(): str(reason or "")
            for name, reason in value.items()
            if str(name).strip() and not str(name).startswith("_")
        }
    if isinstance(value, (list, tuple)):
        return {str(name).strip().lower(): "" for name in value if str(name).strip()}
    return {}


def _term_tuple(value: Any) -> tuple[str, ...]:
    """Normalize a list of match terms to a lowercased tuple."""
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(term).strip().lower() for term in value if str(term).strip())


def _str_map(value: Any, *, lower_values: bool = False) -> dict[str, str]:
    """Normalize a ``{str: str}`` map, dropping ``_``-prefixed doc keys."""
    if not isinstance(value, dict):
        return {}
    result: dict[str, str] = {}
    for key, val in value.items():
        key_s = str(key).strip()
        if not key_s or key_s.startswith("_"):
            continue
        val_s = str(val).strip()
        result[key_s.lower()] = val_s.lower() if lower_values else val_s
    return result


def policy_from_dict(data: Optional[dict[str, Any]]) -> Policy:
    """Build a `Policy` from a raw dict, deep-merged over neutral DEFAULTS."""
    merged = _deep_merge(DEFAULTS, data or {})
    scoring_raw = merged.get("scoring") or {}
    queue_raw = merged.get("queue") or {}
    gate_raw = queue_raw.get("location_gate") or {}

    scoring = ScoringPolicy(
        refused_companies=_reason_map(scoring_raw.get("refused_companies")),
        refused_title_keywords=_reason_map(scoring_raw.get("refused_title_keywords")),
        deprioritized_companies=_reason_map(scoring_raw.get("deprioritized_companies")),
    )
    gate = LocationGate(
        enabled=bool(gate_raw.get("enabled", False)),
        allowed_locations=_term_tuple(gate_raw.get("allowed_locations")),
        remote_terms=_term_tuple(gate_raw.get("remote_terms")),
        country_terms=_term_tuple(gate_raw.get("country_terms")),
        blocked_locations=_term_tuple(gate_raw.get("blocked_locations")),
        blocked_without_country=_term_tuple(gate_raw.get("blocked_without_country")),
    )
    queue = QueuePolicy(
        title_kill_keywords=_term_tuple(queue_raw.get("title_kill_keywords")),
        moat_company_tags=_str_map(queue_raw.get("moat_company_tags"), lower_values=True),
        high_moat_industries=frozenset(_term_tuple(queue_raw.get("high_moat_industries"))),
        excluded_industries=frozenset(_term_tuple(queue_raw.get("excluded_industries"))),
        company_hq=_str_map(queue_raw.get("company_hq")),
        location_gate=gate,
    )
    return Policy(scoring=scoring, queue=queue)


def load_policy(path: Optional[Path] = None) -> Policy:
    """Load the policy file (default ``data/policy.json``).

    A missing or unparseable file yields the neutral defaults — the tool
    must work out of the box on a fresh clone with no ``data/`` contents.
    """
    policy_path = path or POLICY_PATH
    raw: dict[str, Any] = {}
    if policy_path.exists():
        try:
            loaded = json.loads(policy_path.read_text())
            if isinstance(loaded, dict):
                raw = loaded
            else:
                log.warning("Ignoring %s — top level must be a JSON object.", policy_path)
        except Exception as exc:
            log.warning("Could not parse %s (%s) — using neutral defaults.", policy_path, exc)
    return policy_from_dict(raw)


_policy_cache: Optional[Policy] = None


def get_policy() -> Policy:
    """Process-wide policy, read once from disk (see `reset_policy_cache`)."""
    global _policy_cache
    if _policy_cache is None:
        _policy_cache = load_policy()
    return _policy_cache


def set_policy(policy: Optional[Policy]) -> None:
    """Override the active policy (tests); pass ``None`` to force a re-read."""
    global _policy_cache
    _policy_cache = policy


def reset_policy_cache() -> None:
    """Drop the cached policy so the next `get_policy` re-reads policy.json."""
    set_policy(None)
