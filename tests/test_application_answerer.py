"""Tests for account-grounded application answer drafting."""

import json
from pathlib import Path

import pytest

from jobpilot.core import application_answerer
from jobpilot.core.application_answerer import (
    PROFILE_PLACEHOLDER,
    TRUE_ACCOUNTS_PATH,
    ApplicationAnswerer,
)
from jobpilot.core.profile_store import UserProfile

# Some tests exercise the fallback drafter against the user's real account bank
# (data/true_accounts.json), which is gitignored personal data. Skip those cleanly
# when it is absent (e.g. a fresh clone) so the suite stays green without it.
requires_personal_data = pytest.mark.skipif(
    not TRUE_ACCOUNTS_PATH.exists(),
    reason="requires local data/true_accounts.json (gitignored personal data)",
)


class DummyProfileStore:
    def load(self) -> UserProfile:
        return UserProfile(
            first_name="Alex",
            last_name="Candidate",
            city="Springfield",
            state="IL",
            authorized_to_work=True,
            requires_sponsorship=False,
            years_of_experience=15,
            current_title="Independent Contractor",
        )


class EmptyProfileStore:
    def load(self) -> UserProfile:
        return UserProfile()


def _answerer() -> ApplicationAnswerer:
    return ApplicationAnswerer(
        profile_store=DummyProfileStore(),
        accounts_path=TRUE_ACCOUNTS_PATH,
        use_bro=False,
    )


def _write_accounts(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "true_accounts.json"
    path.write_text(json.dumps(payload))
    return path


@requires_personal_data
def test_drafts_why_answer_from_true_accounts():
    jd = "Build agentic workflows, partner with clients, travel, demo progress, and turn feedback into product improvements."
    draft = _answerer().draft(
        "Why are you interested in this role?",
        jd_text=jd,
        company="titan-ai",
        title="Forward Deployed Engineer - Applied AI Focus",
    )

    assert "Titan AI" in draft.answer
    assert "JobPilot" in draft.answer
    assert "View field service work" in draft.answer
    assert "direct customer contact" not in draft.answer
    assert draft.source == "account_grounded"


@requires_personal_data
def test_browser_automation_question_uses_jobpilot_account():
    draft = _answerer().draft(
        "Describe your experience with browser automation.",
        jd_text="This role uses browser automation, integrations, and workflow tooling.",
        company="example-ai",
        title="Automation Engineer",
    )

    assert "JobPilot" in draft.answer
    assert "Chrome DevTools Protocol" in draft.answer or "Playwright" in draft.answer
    assert "paid users" not in draft.answer.lower()


@requires_personal_data
def test_account_selection_prioritizes_field_work_for_forward_deployed_roles():
    accounts = _answerer().select_accounts(
        "Tell us about your customer-facing experience.",
        jd_text="Client travel, deployment, customer feedback, and production debugging.",
        company="titan-ai",
        title="Forward Deployed Engineer",
    )

    ids = [account.id for account in accounts]
    assert "view-field-service" in ids


def test_background_answer_uses_placeholder_when_no_profile_data(tmp_path):
    accounts_path = _write_accounts(
        tmp_path,
        {"accounts": [{"id": "first-job", "title": "Something I did once"}]},
    )
    answerer = ApplicationAnswerer(
        profile_store=EmptyProfileStore(),
        accounts_path=accounts_path,
        use_bro=False,
    )

    draft = answerer.draft("Tell me about yourself.")

    assert draft.answer == PROFILE_PLACEHOLDER
    assert "jobpilot profile --edit" in draft.answer


def test_background_answer_built_from_profile_and_accounts(tmp_path):
    accounts_path = _write_accounts(
        tmp_path,
        {
            "accounts": [
                {
                    "id": "clinic-scheduler",
                    "title": "Improved scheduling at a community clinic",
                    "summary": "Built a scheduling tracker that cut patient wait times at a community clinic.",
                    "skills": ["scheduling", "operations"],
                }
            ]
        },
    )

    class SyntheticProfileStore:
        def load(self) -> UserProfile:
            return UserProfile(
                first_name="Riley",
                last_name="Nguyen",
                current_title="Operations Coordinator",
                years_of_experience=7,
            )

    answerer = ApplicationAnswerer(
        profile_store=SyntheticProfileStore(),
        accounts_path=accounts_path,
        use_bro=False,
    )

    draft = answerer.draft("Tell me about yourself.")

    assert "an Operations Coordinator" in draft.answer
    assert "7+ years" in draft.answer
    assert "I built a scheduling tracker" in draft.answer


def test_background_answer_prefers_stored_background_summary(tmp_path):
    accounts_path = _write_accounts(
        tmp_path,
        {
            "narrative": {"background_summary": "I am a synthetic test person who fixes things."},
            "accounts": [{"id": "first-job", "title": "Something I did once"}],
        },
    )
    answerer = ApplicationAnswerer(
        profile_store=EmptyProfileStore(),
        accounts_path=accounts_path,
        use_bro=False,
    )

    draft = answerer.draft("Tell me about yourself.")

    assert draft.answer == "I am a synthetic test person who fixes things."


def test_account_tags_drive_selection_and_field_narrative(tmp_path):
    accounts_path = _write_accounts(
        tmp_path,
        {
            "accounts": [
                {
                    "id": "desk-job",
                    "title": "Ran reporting at a desk job",
                    "summary": "Built weekly reports for a regional office.",
                    "tags": ["headline"],
                },
                {
                    "id": "site-visits",
                    "title": "Handled on-site equipment installs",
                    "summary": "Worked as an installer visiting customer sites.",
                    "label": "my on-site install work",
                    "tags": ["field-primary"],
                },
            ]
        },
    )
    answerer = ApplicationAnswerer(
        profile_store=EmptyProfileStore(),
        accounts_path=accounts_path,
        use_bro=False,
    )

    selected = answerer.select_accounts(
        "Why are you interested?",
        jd_text="Frequent customer travel and on-site deployment.",
        title="Field Technician",
    )
    assert selected[0].id == "site-visits"

    draft = answerer.draft(
        "Why are you interested in this role?",
        jd_text="Frequent customer travel and on-site deployment.",
        title="Field Technician",
    )
    assert "my on-site install work" in draft.answer


def test_account_tag_selects_but_never_invents_field_claims(tmp_path):
    accounts_path = _write_accounts(
        tmp_path,
        {"accounts": [
            {
                "id": "plain",
                "title": "Documented project",
                "summary": "Built a small scheduling tracker.",
                "tags": ["headline"],
            },
            {
                "id": "tag-only",
                "title": "Second documented project",
                "summary": "Recorded equipment inventory.",
                "tags": ["field-primary"],
            },
        ]},
    )

    draft = ApplicationAnswerer(
        profile_store=EmptyProfileStore(),
        accounts_path=accounts_path,
        use_bro=False,
    ).draft(
        "Why are you interested?",
        jd_text="Customer travel and on-site deployment.",
        title="Field Technician",
    )

    assert "customer-site execution" not in draft.answer
    assert "ownership under pressure" not in draft.answer
    assert "Recorded equipment inventory" in draft.answer


def test_generic_answer_uses_no_unstored_strength_claims(tmp_path):
    accounts_path = _write_accounts(
        tmp_path,
        {"accounts": [{
            "id": "one",
            "title": "Documented project",
            "summary": "Built a small scheduling tracker.",
        }]},
    )

    draft = ApplicationAnswerer(
        profile_store=EmptyProfileStore(),
        accounts_path=accounts_path,
        use_bro=False,
    ).draft("Anything else?")

    for invented in ("practical ownership", "fast learning", "clear delivery"):
        assert invented not in draft.answer.lower()


def test_title_only_account_does_not_become_hands_on_strength_claim(tmp_path):
    accounts_path = _write_accounts(
        tmp_path,
        {"accounts": [{"id": "title-only", "title": "Named account"}]},
    )

    draft = ApplicationAnswerer(
        profile_store=EmptyProfileStore(),
        accounts_path=accounts_path,
        use_bro=False,
    ).draft("What strengths do you bring?")

    assert draft.answer == ""
    assert "hands-on" not in draft.answer.lower()


def test_jd_overlap_cannot_make_unrelated_account_evidence_for_skill_question(tmp_path):
    accounts_path = _write_accounts(
        tmp_path,
        {"accounts": [{
            "id": "clinic-scheduler",
            "title": "Clinic scheduling work",
            "summary": "Built a customer scheduling tracker for a community clinic.",
            "skills": ["customer support", "scheduling", "clinic operations"],
        }]},
    )
    answerer = ApplicationAnswerer(
        profile_store=EmptyProfileStore(),
        accounts_path=accounts_path,
        use_bro=False,
    )

    draft = answerer.draft(
        "Describe your Kubernetes production experience.",
        jd_text=(
            "Operate Kubernetes clusters while partnering with clinic customers "
            "on scheduling and support workflows."
        ),
        title="Platform Engineer",
    )

    assert draft.answer == ""
    assert draft.account_ids == []
    assert draft.source == "missing_evidence"
    assert draft.confidence == 0.0
    assert any("Missing evidence" in warning for warning in draft.warnings)
    assert answerer.select_accounts(
        "Describe your Kubernetes production experience.",
        jd_text=(
            "Operate Kubernetes clusters while partnering with clinic customers "
            "on scheduling and support workflows."
        ),
        title="Platform Engineer",
    ) == []


def test_module_contains_no_author_identity():
    source = Path(application_answerer.__file__).read_text()
    for fragment in (
        "Navy",
        "Vartabedian",
        "View, Inc",
        "view-field-service",
        "christie",
        "atxbro",
        "columbia",
        "field engineering",
    ):
        assert fragment.lower() not in source.lower(), f"author literal {fragment!r} still in module"


def test_ai_hallucination_cannot_enter_external_answer(tmp_path, monkeypatch):
    accounts_path = _write_accounts(
        tmp_path,
        {"accounts": [{
            "id": "clinic",
            "title": "Clinic scheduling work",
            "summary": "Built a scheduling tracker for a community clinic.",
        }]},
    )
    monkeypatch.setattr(
        "jobpilot.core.llm_client.complete",
        lambda *_args, **_kwargs: (
            "I led fifty NASA engineers, earned a Stanford doctorate, and "
            "improved revenue by 900 percent."
        ),
    )

    draft = ApplicationAnswerer(
        profile_store=EmptyProfileStore(),
        accounts_path=accounts_path,
        use_bro=True,
    ).draft("Tell me about yourself.")

    assert draft.source == "account_grounded"
    assert "NASA" not in draft.answer
    assert "Stanford" not in draft.answer
    assert "900" not in draft.answer
