"""Per-user scoring overrides (data/gigs/scoring.json) deep-merge over defaults."""

import importlib
import json

import pytest


@pytest.fixture
def rules():
    """Reload scoring_rules so each test starts from pristine defaults and any
    in-place override rebinding can't leak into other tests."""
    from jobpilot.gigs.core import scoring_rules
    importlib.reload(scoring_rules)
    yield scoring_rules
    importlib.reload(scoring_rules)


def test_no_override_file_keeps_defaults(rules, tmp_path):
    missing = tmp_path / "scoring.json"
    before = dict(rules.SKILL_WEIGHTS)
    assert rules.apply_overrides(missing) is False
    assert rules.SKILL_WEIGHTS == before


def test_override_deep_merges_dict_weights(rules, tmp_path):
    cfg = tmp_path / "scoring.json"
    # Add one new skill weight, bump one existing — keep the rest.
    existing_key = next(iter(rules.SKILL_WEIGHTS))
    cfg.write_text(json.dumps({
        "SKILL_WEIGHTS": {"rustlang": 99, existing_key: 1},
    }))
    assert rules.apply_overrides(cfg) is True
    assert rules.SKILL_WEIGHTS["rustlang"] == 99       # added
    assert rules.SKILL_WEIGHTS[existing_key] == 1       # overridden
    assert len(rules.SKILL_WEIGHTS) >= 2                # others preserved


def test_override_replaces_scalar_cap(rules, tmp_path):
    cfg = tmp_path / "scoring.json"
    cfg.write_text(json.dumps({"TITLE_NEGATIVE_CAP": 10}))
    assert rules.apply_overrides(cfg) is True
    assert rules.TITLE_NEGATIVE_CAP == 10


def test_unknown_keys_are_ignored(rules, tmp_path):
    cfg = tmp_path / "scoring.json"
    cfg.write_text(json.dumps({"NOT_A_REAL_TABLE": {"x": 1}}))
    assert rules.apply_overrides(cfg) is True
    assert not hasattr(rules, "NOT_A_REAL_TABLE")


def test_override_changes_actual_score(rules, tmp_path):
    """End-to-end: a scalar override the scorer reads through `_rules` must
    change the score it produces. Zeroing the skill-weight cap removes the
    skill contribution."""
    from jobpilot.gigs.core import scorer
    importlib.reload(scorer)  # rebind scorer's `_rules` to the reloaded module
    from jobpilot.gigs.core.models import Gig

    def fresh_gig():
        return Gig(id="hn-1", source="hn", title="AI Automation Engineer",
                   company="Acme",
                   description="building agentic workflows with python and rag",
                   url="x")

    base = scorer.score_gig(fresh_gig()).fit_score

    cfg = tmp_path / "scoring.json"
    cfg.write_text(json.dumps({"SKILL_WEIGHTS_CAP": 0}))
    assert rules.apply_overrides(cfg) is True
    assert scorer._rules.SKILL_WEIGHTS_CAP == 0  # scorer reads the override
    after = scorer.score_gig(fresh_gig()).fit_score

    assert after < base  # skills no longer contribute
    importlib.reload(scorer)  # restore defaults for other tests
