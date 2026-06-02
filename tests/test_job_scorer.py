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


# ── Supreme-pin alignment filter tests ────────────────────────────
# See ~/.claude/projects/-Users-vartny-AI-Workspace/memory/feedback_jesus_is_the_standard.md


def _aligned_profile() -> UserProfile:
    """Profile that would score well on the non-alignment components."""
    return UserProfile(
        current_title="Forward Deployed Engineer",
        years_of_experience=10,
        authorized_to_work=True,
        requires_sponsorship=False,
        custom_answers={"skills": "Python TypeScript React Postgres"},
    )


def test_refused_palantir_short_circuits_to_zero():
    """Palantir is refused regardless of title-fit (supreme pin / Matt 4:8-10)."""
    scorer = JobScorer(profile_store=DummyProfileStore(_aligned_profile()), use_bro=False)
    result = scorer.score_text(
        "Forward Deployed AI Engineer — Python, distributed systems, NYC",
        title="Forward Deployed AI Engineer",
        company="Palantir",
    )
    assert result.score == 0, "Refused companies must score 0 regardless of fit"
    assert "REFUSED" in result.recommendation
    assert any("palantir" in risk.lower() for risk in result.risks)


def test_refused_title_keyword_federal_civilian():
    """Federal-customer titles refused regardless of company (supreme pin)."""
    scorer = JobScorer(profile_store=DummyProfileStore(_aligned_profile()), use_bro=False)
    result = scorer.score_text(
        "Senior engineer working on civilian agency deployments",
        title="Applied AI Architect, Federal Civilian",
        company="Anthropic",  # company alignment isn't what catches this — title keyword does
    )
    assert result.score == 0, "Federal-customer title keywords must short-circuit to 0"
    assert "REFUSED" in result.recommendation
    assert any("federal civilian" in risk.lower() for risk in result.risks)


def test_deprioritized_anthropic_penalty_and_recommendation_downgrade():
    """Anthropic roles are aligned but deprioritized (0 prior conversion)."""
    scorer = JobScorer(profile_store=DummyProfileStore(_aligned_profile()), use_bro=False)
    # Use a clean commercial Anthropic title (no federal keywords)
    result = scorer.score_text(
        "Applied AI Engineer on the Beneficial Deployments team building healthcare integrations.",
        title="Applied AI Engineer, Beneficial Deployments",
        company="Anthropic",
    )
    # Score is reduced from what it would be without the -25 penalty, but not zero
    assert 0 < result.score < 100
    assert "deprioritized" in result.recommendation.lower()
    assert any("anthropic" in risk.lower() and "conversion" in risk.lower() for risk in result.risks)


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
