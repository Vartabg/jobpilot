"""Tests for core/policy_config.py — config-driven job-search policy.

The shipped defaults are neutral: nothing refused, nothing deprioritized,
location gate off. Personal policy comes from data/policy.json (gitignored).
These tests exercise the mechanism with fixture policies so they hold on a
fresh clone with no data/ contents.
"""

import json
from pathlib import Path

from jobpilot.core.job_scorer import JobScorer
from jobpilot.core.policy_config import Policy, load_policy, policy_from_dict
from jobpilot.core.profile_store import UserProfile


# ── policy loading ────────────────────────────────────────────────


def test_defaults_are_neutral():
    policy = policy_from_dict({})
    assert policy.scoring.refused_companies == {}
    assert policy.scoring.refused_title_keywords == {}
    assert policy.scoring.deprioritized_companies == {}
    assert policy.queue.title_kill_keywords == ()
    assert policy.queue.moat_company_tags == {}
    assert not policy.queue.location_gate.enabled


def test_load_policy_missing_file_returns_defaults(tmp_path: Path):
    policy = load_policy(tmp_path / "does_not_exist.json")
    assert policy.scoring.refused_companies == {}
    assert not policy.queue.location_gate.enabled


def test_load_policy_invalid_json_falls_back_to_defaults(tmp_path: Path):
    bad = tmp_path / "policy.json"
    bad.write_text("{not json!")
    policy = load_policy(bad)
    assert policy == policy_from_dict({})


def test_load_policy_deep_merges_partial_file(tmp_path: Path):
    """A partial policy file only overrides what it specifies."""
    partial = tmp_path / "policy.json"
    partial.write_text(json.dumps({
        "scoring": {"refused_companies": {"evilcorp": "reasons"}},
        "queue": {"location_gate": {"enabled": True, "country_terms": ["us"]}},
    }))
    policy = load_policy(partial)
    assert policy.scoring.refused_companies == {"evilcorp": "reasons"}
    # untouched sections keep their defaults
    assert policy.scoring.deprioritized_companies == {}
    assert policy.queue.title_kill_keywords == ()
    gate = policy.queue.location_gate
    assert gate.enabled
    assert gate.country_terms == ("us",)
    assert gate.remote_terms == ("remote",)  # default survives the merge


def test_refused_lists_accept_list_or_dict_and_ignore_doc_keys():
    as_list = policy_from_dict({"scoring": {"refused_companies": ["EvilCorp "]}})
    assert as_list.scoring.refused_companies == {"evilcorp": ""}

    as_dict = policy_from_dict({
        "scoring": {
            "refused_companies": {
                "_doc": "this is documentation, not a company",
                "EvilCorp": "their whole deal",
            },
        },
        "queue": {
            "moat_company_tags": {"_doc": "ignored", "Acme": "HealthCare_Ops"},
        },
    })
    assert as_dict.scoring.refused_companies == {"evilcorp": "their whole deal"}
    assert as_dict.queue.moat_company_tags == {"acme": "healthcare_ops"}


# ── scorer mechanism (fixture policies) ───────────────────────────


class DummyProfileStore:
    def __init__(self, profile: UserProfile):
        self._profile = profile

    def load(self) -> UserProfile:
        return self._profile


def _scorer(policy: Policy) -> JobScorer:
    profile = UserProfile(
        current_title="Forward Deployed Engineer",
        years_of_experience=10,
        authorized_to_work=True,
        requires_sponsorship=False,
        custom_answers={"skills": "Python TypeScript React Postgres"},
    )
    return JobScorer(profile_store=DummyProfileStore(profile), use_bro=False, policy=policy)


JD_TEXT = "Forward Deployed Engineer — Python, TypeScript, distributed systems. Remote."


def test_scorer_refuses_configured_company():
    policy = policy_from_dict({
        "scoring": {"refused_companies": {"evilcorp": "their whole deal"}},
    })
    result = _scorer(policy).score_text(JD_TEXT, title="Forward Deployed Engineer", company="EvilCorp")
    assert result.score == 0, "refused companies must score 0 regardless of fit"
    assert "REFUSED" in result.recommendation
    assert any("evilcorp" in risk.lower() and "their whole deal" in risk.lower() for risk in result.risks)


def test_scorer_refuses_configured_title_keyword():
    policy = policy_from_dict({
        "scoring": {"refused_title_keywords": ["night shift"]},
    })
    result = _scorer(policy).score_text(JD_TEXT, title="Engineer, Night Shift", company="Acme")
    assert result.score == 0
    assert "REFUSED" in result.recommendation
    assert any("night shift" in risk.lower() for risk in result.risks)


def test_scorer_deprioritizes_configured_company():
    policy = policy_from_dict({
        "scoring": {"deprioritized_companies": {"slowcorp": "zero prior conversion"}},
    })
    result = _scorer(policy).score_text(JD_TEXT, title="Forward Deployed Engineer", company="SlowCorp")
    assert result.components.get("Alignment") == -25
    assert 0 < result.score < 100
    assert "deprioritized" in result.recommendation.lower()
    assert any("slowcorp" in risk.lower() and "conversion" in risk.lower() for risk in result.risks)


def test_scorer_default_policy_refuses_nothing():
    """The same JDs score normally under the shipped (neutral) defaults."""
    scorer = _scorer(policy_from_dict({}))
    for title, company in (
        ("Forward Deployed Engineer", "EvilCorp"),
        ("Engineer, Night Shift", "Acme"),
        ("Forward Deployed Engineer", "SlowCorp"),
    ):
        result = scorer.score_text(JD_TEXT, title=title, company=company)
        assert result.score > 0
        assert "REFUSED" not in result.recommendation
        assert "deprioritized" not in result.recommendation.lower()
        assert result.components.get("Alignment", 0) == 0
