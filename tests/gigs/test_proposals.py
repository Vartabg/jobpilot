from jobpilot.gigs.core import preferences
from jobpilot.gigs.core.models import Gig
from jobpilot.gigs.core.proposals import (
    _listing_skill_mentions,
    _personalization_signal,
    build_revenue_brief,
    email_body,
    email_subject,
    pick_offer,
)


def test_pick_offer_prefers_rag_for_knowledge_chatbot() -> None:
    gig = Gig(
        id="test-rag",
        source="upwork",
        title="Build a healthcare knowledge chatbot",
        url="https://example.com",
        description="RAG over 500 pages with vector search and citations.",
    )

    assert pick_offer(gig) == "RAG / internal knowledge assistant"


def test_pick_offer_detects_3d_rescue() -> None:
    gig = Gig(
        id="test-3d",
        source="hn",
        title="React Three Fiber configurator performance work",
        url="https://example.com",
        description="WebGL scene has mobile lag and camera issues.",
    )

    assert pick_offer(gig) == "Interactive 3D performance rescue"


def test_revenue_brief_keeps_final_send_human_approved() -> None:
    gig = Gig(
        id="test-auto",
        source="upwork",
        title="AI workflow automation engineer",
        url="https://example.com",
        description="Need n8n, LLM APIs, CRM integration, and Slack automation.",
    )

    brief = build_revenue_brief(gig)

    assert brief.offer == "AI workflow audit + one automation"
    assert "paste the draft into Upwork manually" in brief.action
    assert "Do not auto-submit" in brief.draft
    # Service page comes from preferences (neutral default or the user's
    # gitignored data/preferences.json) — never hardcoded.
    assert preferences.links()["service_page"] in brief.draft


def test_revenue_brief_adds_grounded_personalization_hook() -> None:
    gig = Gig(
        id="test-personalized",
        source="hn",
        title="AI workflow automation engineer",
        url="https://example.com",
        description="Need LLM workflow automation in Python with Slack integration.",
        tags=["python", "agent"],
    )

    brief = build_revenue_brief(gig)

    assert "I noticed the workflow orchestration piece" in brief.draft
    assert "Python" in brief.draft
    assert "the posting mentions" in brief.draft
    assert "fits me" not in brief.draft


def test_fresh_preferences_never_emit_a_shipped_personal_biography(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        preferences,
        "background_bullets",
        lambda prefs=None: dict(preferences.DEFAULTS["background_bullets"]),
    )
    brief = build_revenue_brief(
        Gig(
            id="fresh",
            source="hn",
            title="Forward Deployed Engineer",
            url="https://example.test/role",
            description="Work with customers on deployments.",
        )
    )

    for unsupported in (
        "navy",
        "field service deployments",
        "solo ai",
        "company swe",
        "austin",
    ):
        assert unsupported not in brief.draft.lower()
    assert "add a verified background bullet" in brief.draft.lower()


def test_email_body_keeps_personalization_without_review_footer() -> None:
    gig = Gig(
        id="test-phone-body",
        source="hn",
        title="RAG engineer",
        url="https://example.com",
        description="Build RAG over internal docs with vector search and citations.",
    )

    body = email_body(gig)

    assert "I noticed the need to turn scattered documents" in body
    assert "Review before sending" not in body


def test_listing_keywords_never_become_candidate_claims(monkeypatch) -> None:
    monkeypatch.setattr(
        preferences,
        "background_bullets",
        lambda prefs=None: dict(preferences.DEFAULTS["background_bullets"]),
    )
    gig = Gig(
        id="listing-only",
        source="hn",
        title="RAG Engineer",
        company="Acme",
        url="https://example.test/role",
        description="Seeking a Python and RAG specialist for internal documents.",
    )

    brief = build_revenue_brief(gig)

    assert "the posting mentions" in brief.draft
    assert "Python" in brief.draft
    assert "RAG" in brief.draft
    assert "fits me" not in brief.draft
    assert "hands-on" not in brief.draft
    assert "AI / retrieval engineer" not in email_subject(gig)
    assert email_subject(gig) == "Interest in Acme — RAG Engineer"


def test_listing_keyword_matching_uses_lexical_boundaries() -> None:
    false_positive = Gig(
        id="boundary",
        source="hn",
        title="Storage systems engineer",
        url="https://example.test/role",
        description="Manage agentless database reliability and stakeholders.",
    )
    genuine = Gig(
        id="genuine",
        source="hn",
        title="Knowledge engineer",
        url="https://example.test/role-2",
        description="Build standalone RAG and agent workflows with Three.js.",
    )

    assert _listing_skill_mentions(false_positive) == []
    assert _personalization_signal(false_positive) != (
        "the need to turn scattered documents into cited, usable answers"
    )
    assert pick_offer(false_positive) != "RAG / internal knowledge assistant"
    assert _listing_skill_mentions(genuine)[:2] == ["Three.js", "RAG"]
    assert pick_offer(genuine) == "RAG / internal knowledge assistant"
