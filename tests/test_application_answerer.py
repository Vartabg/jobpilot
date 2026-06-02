"""Tests for account-grounded application answer drafting."""

import pytest

from jobpilot.core.application_answerer import ApplicationAnswerer, TRUE_ACCOUNTS_PATH
from jobpilot.core.profile_store import UserProfile

# These tests exercise the fallback drafter against the user's real account bank
# (data/true_accounts.json), which is gitignored personal data. Skip cleanly when
# it is absent (e.g. a fresh clone) so the suite stays green without it.
pytestmark = pytest.mark.skipif(
    not TRUE_ACCOUNTS_PATH.exists(),
    reason="requires local data/true_accounts.json (gitignored personal data)",
)


class DummyProfileStore:
    def load(self) -> UserProfile:
        return UserProfile(
            first_name="Garo",
            last_name="Vartabedian",
            city="New York",
            state="NY",
            authorized_to_work=True,
            requires_sponsorship=False,
            years_of_experience=15,
            current_title="Independent Contractor",
        )


def _answerer() -> ApplicationAnswerer:
    return ApplicationAnswerer(
        profile_store=DummyProfileStore(),
        accounts_path=TRUE_ACCOUNTS_PATH,
        use_bro=False,
    )


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
    assert draft.source == "fallback"


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


def test_account_selection_prioritizes_field_work_for_forward_deployed_roles():
    accounts = _answerer().select_accounts(
        "Tell us about your customer-facing experience.",
        jd_text="Client travel, deployment, customer feedback, and production debugging.",
        company="titan-ai",
        title="Forward Deployed Engineer",
    )

    ids = [account.id for account in accounts]
    assert "view-field-service" in ids
