import pytest

from jobpilot.engine.domain import Listing, Posting, Profile, Settings, Targets
from jobpilot.engine.domain.fit import (
    Fit,
    Match,
    Requirement,
    Tier,
    Verdict,
    assess_with_rules,
    decide,
    evidence_fingerprint,
    evidence_from,
    extract_requirements,
    match_rules,
    title_relevance,
)

TARGETS = (
    "Forward Deployed Engineer",
    "Solutions Engineer",
    "Customer Engineer",
    "Field Engineer",
    "Customer Success Engineer",
    "Technical Account Manager",
    "Installation & Commissioning Engineer",
)
ME = Settings(
    profile=Profile(languages=("English", "Spanish")),
    targets=Targets(
        titles=TARGETS,
        skills=("Python", "TypeScript", "FastAPI", "RAG", "C#"),
        domain_experience={"field service": 4},
    ),
)


def posting(title="Forward Deployed Engineer", description=""):
    return Posting(
        Listing("Acme", title, url="https://jobs.ashbyhq.com/acme/1"), description
    )


class TestEvidence:
    def test_skills_domains_and_languages_but_never_target_titles(self):
        ids = {item.id for item in evidence_from(ME)}
        assert {
            "skill:python",
            "skill:c#",
            "domain:field-service",
            "language:spanish",
        } <= ids
        assert not any(item.startswith("title:") for item in ids)

    def test_education_is_evidence(self):
        schooled = Settings(
            profile=Profile(education=("BA, Philosophy, Columbia University",))
        )
        (item,) = evidence_from(schooled)
        assert (
            item.kind == "education"
            and item.text == "BA, Philosophy, Columbia University"
        )

    def test_fingerprint_changes_with_the_evidence(self):
        more = Settings(targets=Targets(skills=("Python", "Go")))
        assert evidence_fingerprint(evidence_from(ME)) != evidence_fingerprint(
            evidence_from(more)
        )


class TestRequirements:
    def test_sections_decide_must_and_nice(self):
        text = (
            "About the role\nYou will build agents.\n"
            "Requirements:\n- Python backend experience\n- Comfortable with customers\n"
            "Nice to have:\n- LangGraph experience\n"
        )
        requirements = extract_requirements(posting(description=text))
        assert requirements == (
            Requirement("Python backend experience", True),
            Requirement("Comfortable with customers", True),
            Requirement("LangGraph experience", False),
        )

    def test_company_history_is_not_a_requirement(self):
        text = "Acme has led the industry for over 40 years.\nYou have 3+ years of field experience."
        assert [r.text for r in extract_requirements(posting(description=text))] == [
            "You have 3+ years of field experience."
        ]

    def test_postings_without_sections_fall_back_to_requirement_cues(self):
        text = "We move fast.\nYou have 3+ years building integrations.\nKnowledge of SQL is essential."
        assert [r.text for r in extract_requirements(posting(description=text))] == [
            "You have 3+ years building integrations.",
            "Knowledge of SQL is essential.",
        ]


class TestMatching:
    def test_named_skills_meet_with_evidence(self):
        match = match_rules(
            Requirement("Strong Python and FastAPI skills"), evidence_from(ME)
        )
        assert match.verdict is Verdict.MEETS
        assert set(match.evidence) == {"skill:python", "skill:fastapi"}

    def test_symbols_in_skill_names_match_exactly(self):
        assert (
            match_rules(
                Requirement("Experience with C# services"), evidence_from(ME)
            ).verdict
            is Verdict.MEETS
        )
        assert (
            match_rules(
                Requirement("Experience with C services"), evidence_from(ME)
            ).verdict
            is Verdict.UNKNOWN
        )

    def test_rules_never_claim_a_gap(self):
        assert (
            match_rules(
                Requirement("Kubernetes in production"), evidence_from(ME)
            ).verdict
            is Verdict.UNKNOWN
        )


@pytest.mark.parametrize(
    ("title", "relevant"),
    [
        ("Forward Deployed Engineer – Applied AI Focus", True),
        ("Strategic Solutions Engineer, West", True),
        ("Agent Deployment Engineer", True),  # "deployed" and "deployment" share a stem
        ("Customer Success Manager", True),
        ("Technical Support Engineer II", False),  # not a target in this test
        ("Software Engineer", False),  # a role noun alone isn't enough
        ("Field Marketing Manager", False),  # one shared word without the role noun
        ("Payroll Manager", False),
        (
            "Engineering Manager, Customer Studios",
            False,
        ),  # "engineering" is not "engineer"
        ("Accounting Manager", False),  # "accounting" is not "account"
        ("100% Abdominal Radiologist", False),
    ],
)
def test_title_relevance(title, relevant):
    assert (title_relevance(title, TARGETS) >= 0.5) is relevant


class TestDecide:
    def req(self, verdict, must=True):
        return Match(Requirement(f"req {verdict}", must), verdict)

    def test_off_target_titles_are_off_target_whatever_the_evidence(self):
        assert (
            decide((self.req(Verdict.MEETS),), 0.2, engine="rules").tier
            is Tier.OFF_TARGET
        )

    def test_strong_needs_relevance_coverage_and_no_gaps(self):
        matches = (
            self.req(Verdict.MEETS),
            self.req(Verdict.EQUIVALENT),
            self.req(Verdict.UNKNOWN),
        )
        assert decide(matches, 1.0, engine="rules").tier is Tier.STRONG
        with_gap = (*matches, self.req(Verdict.GAP))
        assert decide(with_gap, 1.0, engine="rules").tier is Tier.POSSIBLE

    def test_many_gaps_make_a_stretch(self):
        matches = (
            self.req(Verdict.MEETS),
            self.req(Verdict.GAP),
            self.req(Verdict.GAP),
        )
        fit = decide(matches, 1.0, engine="rules")
        assert fit.tier is Tier.STRETCH
        assert fit.biggest_gap == "req gap"

    def test_nice_to_haves_do_not_count_against_coverage(self):
        matches = (self.req(Verdict.MEETS), self.req(Verdict.GAP, must=False))
        assert decide(matches, 1.0, engine="rules").coverage == 1.0

    def test_relevant_role_without_readable_requirements_is_possible(self):
        fit = decide((), 1.0, engine="rules")
        assert fit.tier is Tier.POSSIBLE
        assert "no requirements found" in fit.notes[0]


def test_rules_assessment_round_trips():
    text = "Requirements:\n- Python and RAG experience\n- Kubernetes in production\n"
    fit = assess_with_rules(posting(description=text), ME)
    assert fit.engine == "rules"
    assert fit.coverage == 0.5
    assert Fit.from_dict(fit.to_dict()) == fit
