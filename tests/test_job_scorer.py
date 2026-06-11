"""Tests for core/job_scorer.py — pre-apply fit scoring."""

from jobpilot.core.profile_store import UserProfile
from jobpilot.core.job_scorer import JobScorer


class DummyProfileStore:
    def __init__(self, profile: UserProfile):
        self._profile = profile

    def load(self) -> UserProfile:
        return self._profile


def test_score_text_returns_high_score_for_good_match():
    profile = UserProfile(
        current_title="Senior Frontend Engineer",
        years_of_experience=8,
        authorized_to_work=True,
        requires_sponsorship=False,
        custom_answers={"skills": "React TypeScript Python GitHub Actions"},
    )
    scorer = JobScorer(profile_store=DummyProfileStore(profile), use_bro=False)

    result = scorer.score_text(
        """
        Senior Frontend Engineer
        Acme AI
        Requirements
        - 5+ years of experience building web apps
        - React and TypeScript
        - GitHub Actions or CI/CD
        - Python for internal tooling
        This is a fully remote role.
        """,
        title="Senior Frontend Engineer",
        company="Acme AI",
    )

    assert result.score >= 70
    assert "React" in result.matched_skills
    assert "TypeScript" in result.matched_skills
    assert result.recommendation in {"Strong fit — prioritize", "Good fit — worth a closer look"}


def test_score_text_penalizes_clear_gaps():
    profile = UserProfile(
        current_title="Junior Designer",
        years_of_experience=2,
        authorized_to_work=False,
        requires_sponsorship=True,
        custom_answers={"skills": "Figma Sketch"},
    )
    scorer = JobScorer(profile_store=DummyProfileStore(profile), use_bro=False)

    result = scorer.score_text(
        """
        Staff Platform Engineer
        Requirements
        - 10+ years of backend or platform engineering experience
        - Go, Kubernetes, and Terraform
        - Must be authorized to work in the United States without sponsorship
        This is an onsite role in Austin.
        """,
        title="Staff Platform Engineer",
        company="Infra Corp",
    )

    assert result.score < 50
    assert result.missing_skills
    assert any("spons" in risk.lower() or "authoriz" in risk.lower() for risk in result.risks)


# ── Policy alignment tests ────────────────────────────────────────
# Refusal/deprioritization behavior is config-driven (core/policy_config.py);
# fixture-driven tests for refused companies, refused title keywords, and
# deprioritized companies live in tests/test_policy_config.py.


def _aligned_profile() -> UserProfile:
    """Profile that would score well on the non-alignment components."""
    return UserProfile(
        current_title="Forward Deployed Engineer",
        years_of_experience=10,
        authorized_to_work=True,
        requires_sponsorship=False,
        custom_answers={"skills": "Python TypeScript React Postgres"},
    )


def test_aligned_commercial_company_no_alignment_penalty():
    """Commercial company outside refused/deprioritized lists scores normally."""
    scorer = JobScorer(profile_store=DummyProfileStore(_aligned_profile()), use_bro=False)
    result = scorer.score_text(
        "Software Engineer, Forward Deployed Agent Builder — Python, agents, real-time systems.",
        title="Software Engineer, Forward Deployed Agent Builder",
        company="Brex",
    )
    assert result.score > 0
    assert "REFUSED" not in result.recommendation
    assert "deprioritized" not in result.recommendation.lower()
    # Alignment component should be 0 (not penalized)
    assert result.components.get("Alignment", 0) == 0
