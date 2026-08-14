"""Evidence-linked role decisions replace title/personality keyword scores."""

import json
from pathlib import Path

import pytest

from jobpilot.core.job_description import normalize_job_description
from jobpilot.core.profile_store import UserProfile
from jobpilot.core.requirement_matcher import EvidenceStatus, RequirementMatcher
from jobpilot.core.role_decision import RoleDecisionEngine

FULL_JD = """
Implementation Engineer
Responsibilities
- Lead customer discovery workshops and product demonstrations.
- Troubleshoot hardware and software integrations at customer sites.
Required qualifications
- Systems troubleshooting experience.
- Clear technical communication with customers.
Preferred qualifications
- Python automation experience.
Work environment
- Work independently through ambiguous technical problems.
Logistics
- Must be authorized to work in the United States without sponsorship.
"""


@pytest.fixture()
def account_path(tmp_path: Path) -> Path:
    path = tmp_path / "accounts.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "accounts": [
                    {
                        "id": "customer-field",
                        "title": "Customer-site technical work",
                        "summary": "Diagnosed hardware and software issues at customer sites.",
                        "details": [
                            "Explained technical repairs and trained customers."
                        ],
                        "skills": [
                            "systems troubleshooting",
                            "technical communication",
                        ],
                        "truth_boundaries": [
                            "Do not claim PLC programming experience."
                        ],
                    }
                ],
            }
        )
    )
    return path


def test_splits_full_jd_into_evidence_categories():
    parsed = RequirementMatcher.parse(FULL_JD)

    assert any("Systems troubleshooting" in item for item in parsed.mandatory)
    assert any("Python automation" in item for item in parsed.preferred)
    assert any("customer discovery" in item for item in parsed.responsibilities)
    assert any("ambiguous" in item for item in parsed.work_context)
    assert any("authorized" in item for item in parsed.logistics)
    assert parsed.is_sufficient


def test_inline_header_preserves_the_requirement_after_the_colon():
    parsed = RequirementMatcher.parse(
        "Requirements: Active Top Secret security clearance required."
    )

    assert parsed.mandatory == ("Active Top Secret security clearance required.",)


def test_location_before_common_primary_sections_does_not_poison_them():
    parsed = RequirementMatcher.parse(
        "Location: Remote\n"
        "About the role\n"
        "- Lead technical discovery.\n"
        "- Own deployment planning.\n"
        "What we\N{RIGHT SINGLE QUOTATION MARK}re looking for\n"
        "- Python automation experience.\n"
        "- 3+ years Kubernetes experience.\n"
        "Benefits\n"
        "- Medical coverage."
    )

    assert parsed.logistics == ("Remote",)
    assert parsed.responsibilities == (
        "Lead technical discovery.",
        "Own deployment planning.",
    )
    assert parsed.mandatory == (
        "Python automation experience.",
        "3+ years Kubernetes experience.",
    )
    assert not any(
        "Medical" in item
        for item in (
            *parsed.mandatory,
            *parsed.preferred,
            *parsed.responsibilities,
            *parsed.logistics,
        )
    )


def test_standalone_location_header_applies_to_one_value_only():
    parsed = RequirementMatcher.parse(
        "Location\n"
        "Remote\n"
        "About the role\n"
        "Build reliable systems.\n"
        "Role focus\n"
        "Support complex deployments.\n"
        "Basic requirements for this role ==\n"
        "Systems troubleshooting experience."
    )

    assert parsed.logistics == ("Remote",)
    assert parsed.responsibilities == (
        "Build reliable systems.",
        "Support complex deployments.",
    )
    assert parsed.mandatory == ("Systems troubleshooting experience.",)


def test_bare_candidate_heading_alias_is_mandatory():
    parsed = RequirementMatcher.parse(
        "We're looking for ==\nCustomer-facing troubleshooting experience."
    )

    assert parsed.mandatory == ("Customer-facing troubleshooting experience.",)


def test_modern_candidate_headings_and_reset_sections_are_classified():
    parsed = RequirementMatcher.parse(
        "About the role\n"
        "- Lead customer implementations.\n"
        "What You\N{RIGHT SINGLE QUOTATION MARK}ll Bring to the Role\n"
        "- 4+ years of sales engineering experience.\n"
        "Your background looks something like this\n"
        "- Technical discovery experience.\n"
        "Even better if you have\n"
        "- Python automation experience.\n"
        "Qualifications we value\n"
        "- Enterprise SaaS experience.\n"
        "Conclusion:\n"
        "- Compensation depends on experience and location.\n"
        "- Recruiting impersonation and fraud notice.\n"
        "Benefits and perks\n"
        "- Remote wellness coaching and family travel assistance.\n"
        "- Customer discounts."
    )

    assert parsed.mandatory == (
        "4+ years of sales engineering experience.",
        "Technical discovery experience.",
    )
    assert parsed.preferred == (
        "Python automation experience.",
        "Enterprise SaaS experience.",
    )
    assert not any(
        any(
            term in item
            for term in ("Compensation", "impersonation", "wellness", "discounts")
        )
        for item in (
            *parsed.responsibilities,
            *parsed.work_context,
            *parsed.logistics,
        )
    )


def test_partial_skill_overlap_does_not_prove_a_composite_responsibility():
    parsed = RequirementMatcher.parse(
        "About the role\n"
        "- Build customer integrations.\n"
        "- Troubleshoot production systems.\n"
        "Responsibilities\n"
        "- Hands-on experience in Go, Python, and JavaScript.\n"
        "Required qualifications\n"
        "- Python automation experience."
    )
    matches = RequirementMatcher.from_profile(
        UserProfile(skills=["Python", "JavaScript"])
    ).match(parsed)

    composite = next(match for match in matches if "Hands-on" in match.text)

    assert composite.status is EvidenceStatus.UNKNOWN


def test_classifies_direct_adjacent_unknown_and_contradicted(account_path: Path):
    profile = UserProfile(skills=["Python"], authorized_to_work=True)
    matcher = RequirementMatcher.from_profile(profile, account_path)
    parsed = RequirementMatcher.parse(
        FULL_JD + "\n- PLC programming.\n- Kubernetes administration."
    )
    matches = matcher.match(parsed)
    by_text = {match.text: match.status for match in matches}

    assert by_text["Systems troubleshooting experience."] is EvidenceStatus.DIRECT
    assert (
        by_text["Lead customer discovery workshops and product demonstrations."]
        is EvidenceStatus.ADJACENT
    )
    assert by_text["PLC programming."] is EvidenceStatus.CONTRADICTED
    assert by_text["Kubernetes administration."] is EvidenceStatus.UNKNOWN


def test_truth_boundary_only_contradicts_its_negative_claim(tmp_path: Path):
    accounts = tmp_path / "accounts.json"
    accounts.write_text(
        json.dumps(
            {
                "version": 1,
                "accounts": [
                    {
                        "id": "field-account",
                        "title": "Field Service Engineer",
                        "summary": "Resolved customer-site hardware and network problems.",
                        "details": [
                            "Coordinated technicians and customer stakeholders."
                        ],
                        "skills": [],
                        "truth_boundaries": [
                            "Not a manager of other field engineers or lead over a team."
                        ],
                    }
                ],
            }
        )
    )
    matcher = RequirementMatcher.from_profile(UserProfile(), accounts)
    parsed = RequirementMatcher.parse(
        "Responsibilities\n"
        "- Lead customer discovery with field stakeholders.\n"
        "- Coordinate customer engineers during implementation.\n"
        "Required qualifications\n"
        "- Customer-facing technical experience."
    )

    matches = matcher.match(parsed)

    assert all(match.status is not EvidenceStatus.CONTRADICTED for match in matches)


def test_sales_account_supports_salesperson_work_context_without_proving_tenure(
    tmp_path: Path,
):
    accounts = tmp_path / "accounts.json"
    accounts.write_text(
        json.dumps(
            {
                "version": 1,
                "accounts": [
                    {
                        "id": "sales-business-development",
                        "title": "Sales and business development",
                        "summary": "Built relationships through outbound sales and prospecting.",
                        "details": ["Presented to executives and closed direct sales."],
                        "skills": ["sales", "business development"],
                        "truth_boundaries": [
                            "Do not claim formal sales engineering or pre-sales tenure."
                        ],
                    }
                ],
            }
        )
    )
    matcher = RequirementMatcher.from_profile(UserProfile(), accounts)
    parsed = RequirementMatcher.parse(
        "Responsibilities\n"
        "- Build customer relationships.\n"
        "Qualifications we value\n"
        "- Operates with the enthusiasm of a salesperson.\n"
        "Required qualifications\n"
        "- 4+ years of sales engineering experience."
    )

    matches = matcher.match(parsed)
    salesperson = next(
        match
        for match in matches
        if "salesperson" in match.text and match.category == "work_context"
    )
    tenure = next(match for match in matches if "4+ years" in match.text)

    assert salesperson.category == "work_context"
    assert salesperson.status is EvidenceStatus.ADJACENT
    assert salesperson.account_ids == ("sales-business-development",)
    assert tenure.status is EvidenceStatus.UNKNOWN


def test_biggest_gap_skips_a_work_context_claim_supported_on_its_context_axis(
    tmp_path: Path,
):
    accounts = tmp_path / "accounts.json"
    accounts.write_text(
        json.dumps(
            {
                "version": 1,
                "accounts": [
                    {
                        "id": "sales-account",
                        "title": "Sales and business development",
                        "summary": "Built relationships through outbound sales.",
                        "details": ["Presented to executives."],
                        "skills": ["sales"],
                    }
                ],
            }
        )
    )
    result = RoleDecisionEngine().assess(
        title="Customer Engineer",
        jd_text=(
            "Responsibilities\n"
            "- Build software integrations.\n"
            "- Troubleshoot production systems.\n"
            "- Maintain stable APIs.\n"
            "Qualifications we value\n"
            "- Operates with the enthusiasm of a salesperson.\n"
            "- Experience with AWS and Git."
        ),
        profile=UserProfile(skills=["Git"]),
        accounts_path=accounts,
    )

    assert result.biggest_gap == "Experience with AWS and Git."


@pytest.mark.parametrize(
    "title",
    [
        "Forward Deployed Creative Designer",
        "Field Marketing Specialist",
        "Marketing Data Scientist",
        "Senior Product Manager",
    ],
)
def test_false_positive_title_families_are_skipped(title: str, account_path: Path):
    result = RoleDecisionEngine().assess(
        title=title,
        jd_text=FULL_JD,
        profile=UserProfile(skills=["Python"]),
        accounts_path=account_path,
    )

    assert result.role_family == "unsupported"
    assert result.decision == "skip"


def test_exact_explicit_target_title_is_assessed_not_silently_discarded() -> None:
    result = RoleDecisionEngine().assess(
        title="Technical Program Liaison",
        jd_text=FULL_JD,
        profile=UserProfile(target_titles=["Technical Program Liaison"]),
    )

    assert result.role_family == "candidate_target"
    assert result.decision == "investigate"


def test_missing_jd_is_unscorable_not_neutral():
    result = RoleDecisionEngine().assess(
        title="Implementation Engineer",
        jd_text="Implementation Engineer",
        profile=UserProfile(skills=["Python"]),
    )

    assert result.status == "unscorable"
    assert result.decision == "investigate"
    assert result.qualification_lower_bound is None
    assert result.work_context_match is None


def test_html_requirements_never_escape_into_biggest_gap():
    jd = """
        <h2><strong>Responsibilities</strong></h2>
        <ul>
          <li>Lead customer discovery workshops and product demonstrations.</li>
          <li>Troubleshoot customer hardware and software integrations.</li>
        </ul>
        <h2><strong>Required qualifications</strong></h2>
        <ul>
          <li>Kubernetes administration experience.</li>
          <li>Systems troubleshooting experience.</li>
        </ul>
        <h2><strong>Preferred qualifications</strong></h2>
        <ul><li>Python automation experience.</li></ul>
    """

    result = RoleDecisionEngine().assess(
        title="Implementation Engineer",
        jd_text=jd,
        profile=UserProfile(skills=["Python", "systems troubleshooting"]),
    )

    assert result.decision == "investigate"
    assert result.biggest_gap == "Kubernetes administration experience."
    assert "<" not in result.biggest_gap
    assert all("<" not in match.text for match in result.matches)


def test_orphaned_legacy_html_tokens_never_escape_into_biggest_gap():
    jd = """
        Responsibilities
        li>Lead customer discovery workshops./li>
        li>Troubleshoot customer integrations./li>
        Required qualifications
        li class="legacy">Kubernetes administration experience./li>
        li>Systems troubleshooting experience./li>
        Preferred qualifications
        li>Python automation experience./li>
    """

    result = RoleDecisionEngine().assess(
        title="Implementation Engineer",
        jd_text=jd,
        profile=UserProfile(skills=["systems troubleshooting", "Python"]),
    )

    assert result.decision == "investigate"
    assert result.biggest_gap == "Kubernetes administration experience."
    assert all(
        "li>" not in match.text and "/li>" not in match.text for match in result.matches
    )


def test_orphaned_adjacent_tag_tokens_normalize_to_plain_text():
    assert (
        normalize_job_description('li class="legacy">Kubernetes experience./li>')
        == "Kubernetes experience."
    )


def test_short_html_description_remains_fail_closed():
    result = RoleDecisionEngine().assess(
        title="Implementation Engineer",
        jd_text=(
            "<p><strong>Required qualifications</strong></p>"
            "<ul><li>Kubernetes administration experience.</li></ul>"
        ),
        profile=UserProfile(skills=["Kubernetes"]),
    )

    assert result.status == "unscorable"
    assert result.decision == "investigate"
    assert result.qualification_lower_bound is None
    assert result.biggest_gap == "A full job description is required."


def test_unknown_logistics_is_surfaced_before_soft_preference_gaps():
    jd = FULL_JD.replace(
        "Must be authorized to work in the United States without sponsorship.",
        "Must hold a valid driver's license and travel overnight.",
    )
    result = RoleDecisionEngine().assess(
        title="Implementation Engineer",
        jd_text=jd,
        profile=UserProfile(
            skills=["Python", "systems troubleshooting", "technical communication"]
        ),
    )

    assert "driver's license" in result.biggest_gap
    assert result.decision == "investigate"


def test_unknown_mandatory_requirement_cannot_reach_apply_now(
    account_path: Path,
):
    jd = FULL_JD.replace(
        "Systems troubleshooting experience.",
        "Systems troubleshooting experience.\n- Kubernetes administration.",
    )
    result = RoleDecisionEngine().assess(
        title="Implementation Engineer",
        jd_text=jd,
        profile=UserProfile(
            skills=["Python"],
            authorized_to_work=True,
            requires_sponsorship=False,
        ),
        accounts_path=account_path,
    )

    assert result.decision == "investigate"
    assert "Kubernetes" in result.biggest_gap


def test_unknown_work_authorization_is_not_direct_evidence(account_path: Path):
    matches = RequirementMatcher.from_profile(
        UserProfile(skills=["Python"]), account_path
    ).match(RequirementMatcher.parse(FULL_JD))

    work_auth = next(match for match in matches if "authorized" in match.text)
    assert work_auth.status is EvidenceStatus.UNKNOWN


def test_us_authorization_never_satisfies_canadian_requirement(
    account_path: Path,
):
    jd = FULL_JD.replace(
        "authorized to work in the United States",
        "authorized to work in Canada",
    )
    result = RoleDecisionEngine().assess(
        title="Implementation Engineer",
        jd_text=jd,
        profile=UserProfile(
            country="United States",
            skills=["Python", "systems troubleshooting"],
            authorized_to_work=True,
            requires_sponsorship=False,
        ),
        accounts_path=account_path,
    )

    canadian_auth = next(match for match in result.matches if "Canada" in match.text)
    assert canadian_auth.status is EvidenceStatus.UNKNOWN
    assert result.decision == "investigate"


def test_us_authorization_never_satisfies_south_america_requirement(
    account_path: Path,
):
    jd = FULL_JD.replace(
        "authorized to work in the United States",
        "authorized to work in South America",
    )
    result = RoleDecisionEngine().assess(
        title="Implementation Engineer",
        jd_text=jd,
        profile=UserProfile(
            country="United States",
            skills=["Python", "systems troubleshooting"],
            authorized_to_work=True,
            requires_sponsorship=False,
        ),
        accounts_path=account_path,
    )

    auth = next(match for match in result.matches if "South America" in match.text)
    assert auth.status is EvidenceStatus.UNKNOWN
    assert result.decision == "investigate"


def test_partial_work_authorization_evidence_cannot_reach_apply_now(
    account_path: Path,
):
    result = RoleDecisionEngine().assess(
        title="Implementation Engineer",
        jd_text=FULL_JD,
        profile=UserProfile(
            skills=["Python"],
            authorized_to_work=True,
            requires_sponsorship=None,
        ),
        accounts_path=account_path,
    )

    assert result.decision == "investigate"
    work_auth = next(match for match in result.matches if "authorized" in match.text)
    assert work_auth.status is EvidenceStatus.UNKNOWN
    assert result.decision == "investigate"


def test_supported_role_uses_observed_work_context_and_accounts(account_path: Path):
    result = RoleDecisionEngine().assess(
        title="Implementation Engineer",
        jd_text=FULL_JD,
        profile=UserProfile(skills=["Python"], authorized_to_work=True),
        accounts_path=account_path,
    )

    assert result.role_family == "applied_implementation"
    assert result.qualification_lower_bound is not None
    assert result.work_context_match is not None
    assert result.matched_accounts == ("customer-field",)
    assert "personality" not in result.rationale.lower()
    assert "intellect" not in result.rationale.lower()


@pytest.mark.parametrize(
    "title",
    [
        "Customer Engineer",
        "Field Engineer",
        "Data Center Technician",
        "Critical Facilities Technician",
        "Technical Account Manager",
        "Customer Success Engineer",
        "Customer Success Manager",
        "Deployment Strategist",
        "Implementation Consultant",
        "Technical Implementation Specialist",
    ],
)
def test_adjacent_customer_and_field_families_can_be_assessed(title: str):
    result = RoleDecisionEngine().assess(
        title=title,
        jd_text=FULL_JD,
        profile=UserProfile(skills=["Python"]),
    )

    assert result.role_family != "unsupported"


@pytest.mark.parametrize(
    ("title", "family"),
    [
        ("Customer Engineer", "applied_implementation"),
        ("Customer Service Engineer", "field_service"),
    ],
)
def test_customer_engineer_family_distinguishes_software_from_service(
    title: str,
    family: str,
):
    result = RoleDecisionEngine().assess(
        title=title,
        jd_text=FULL_JD,
        profile=UserProfile(skills=["Python"]),
    )

    assert result.role_family == family


def test_domain_experience_proves_only_its_named_years_requirement():
    jd = FULL_JD.replace(
        "Systems troubleshooting experience.",
        "3+ years of field service experience.",
    )
    profile = UserProfile(
        years_of_experience=10,
        domain_experience={"field service": 4},
    )
    matches = RequirementMatcher.from_profile(profile).match(
        RequirementMatcher.parse(jd)
    )

    field_years = next(match for match in matches if "3+ years" in match.text)
    assert field_years.status is EvidenceStatus.DIRECT
    assert field_years.support == ("profile domain experience: field service",)


def test_overall_years_do_not_prove_domain_specific_tenure():
    jd = FULL_JD.replace(
        "Systems troubleshooting experience.",
        "5+ years of experience with PLC programming.",
    )
    matches = RequirementMatcher.from_profile(
        UserProfile(years_of_experience=10)
    ).match(RequirementMatcher.parse(jd))

    plc_years = next(match for match in matches if "5+ years" in match.text)
    assert plc_years.status is EvidenceStatus.UNKNOWN


def test_overall_years_do_not_prove_years_of_named_domain_experience():
    jd = FULL_JD.replace(
        "Systems troubleshooting experience.",
        "5+ years of field service experience.",
    )
    matches = RequirementMatcher.from_profile(
        UserProfile(years_of_experience=10)
    ).match(RequirementMatcher.parse(jd))

    field_years = next(match for match in matches if "5+ years" in match.text)
    assert field_years.status is EvidenceStatus.UNKNOWN


def test_bare_years_before_a_named_technology_are_domain_specific():
    jd = FULL_JD.replace(
        "Systems troubleshooting experience.",
        "5 years Kubernetes experience.",
    )
    matches = RequirementMatcher.from_profile(
        UserProfile(years_of_experience=10)
    ).match(RequirementMatcher.parse(jd))

    kubernetes = next(match for match in matches if "Kubernetes" in match.text)
    assert kubernetes.status is EvidenceStatus.UNKNOWN


def test_supported_skill_cannot_mask_an_unsupported_hard_conjunct():
    requirements = RequirementMatcher.parse(
        "Requirements: Python and an active Top Secret security clearance."
    )
    match = RequirementMatcher.from_profile(UserProfile(skills=["Python"])).match(
        requirements
    )[0]

    assert match.status is EvidenceStatus.UNKNOWN
    assert not match.support


def test_generic_clearance_does_not_prove_top_secret_clearance():
    requirements = RequirementMatcher.parse(
        "Requirements: Python and an active Top Secret security clearance."
    )
    match = RequirementMatcher.from_profile(
        UserProfile(skills=["Python", "security clearance"])
    ).match(requirements)[0]

    assert match.status is EvidenceStatus.UNKNOWN


def test_inline_hard_conjunct_keeps_role_out_of_apply_now(account_path: Path):
    jd = FULL_JD.replace(
        "Required qualifications\n- Systems troubleshooting experience.",
        "Requirements: Python and an active Top Secret security clearance.\n"
        "- Systems troubleshooting experience.",
    )
    result = RoleDecisionEngine().assess(
        title="Implementation Engineer",
        jd_text=jd,
        profile=UserProfile(
            skills=["Python"],
            authorized_to_work=True,
            requires_sponsorship=False,
        ),
        accounts_path=account_path,
    )

    assert result.decision == "investigate"
    assert "Top Secret" in result.biggest_gap


def test_each_named_skill_in_a_mandatory_conjunction_needs_evidence():
    requirements = RequirementMatcher.parse(
        "Requirements: Python and Kubernetes experience."
    )
    match = RequirementMatcher.from_profile(UserProfile(skills=["Python"])).match(
        requirements
    )[0]

    assert match.status is EvidenceStatus.UNKNOWN


def test_combined_skill_label_cannot_mask_a_third_unsupported_conjunct():
    requirements = RequirementMatcher.parse(
        "Requirements: Python, Kubernetes, and Terraform experience."
    )
    match = RequirementMatcher.from_profile(
        UserProfile(skills=["Python and Kubernetes"])
    ).match(requirements)[0]

    assert match.status is EvidenceStatus.UNKNOWN


def test_explicit_evidence_for_every_conjunct_can_remain_direct():
    requirements = RequirementMatcher.parse(
        "Requirements: Python and Kubernetes experience."
    )
    match = RequirementMatcher.from_profile(
        UserProfile(skills=["Python", "Kubernetes"])
    ).match(requirements)[0]

    assert match.status is EvidenceStatus.DIRECT


def test_generic_tech_skill_does_not_prove_context_or_logistics():
    requirements = RequirementMatcher.parse(
        "Work environment\n"
        "- Python automation experience.\n"
        "Logistics\n"
        "- Python automation experience."
    )
    matches = RequirementMatcher.from_profile(UserProfile(skills=["Python"])).match(
        requirements
    )

    by_category = {match.category: match.status for match in matches}
    assert by_category["work_context"] is EvidenceStatus.UNKNOWN
    assert by_category["logistics"] is EvidenceStatus.UNKNOWN


def test_stated_overall_years_can_support_an_unqualified_tenure_requirement():
    jd = FULL_JD.replace(
        "Systems troubleshooting experience.",
        "5+ years experience.",
    )
    matches = RequirementMatcher.from_profile(
        UserProfile(years_of_experience=10)
    ).match(RequirementMatcher.parse(jd))

    overall_years = next(match for match in matches if "5+ years" in match.text)
    assert overall_years.status is EvidenceStatus.DIRECT
