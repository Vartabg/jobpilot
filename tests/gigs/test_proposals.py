from jobpilot.gigs.core import preferences
from jobpilot.gigs.core.models import Gig
from jobpilot.gigs.core.proposals import build_revenue_brief, email_body, pick_offer


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

    assert "The part that fits me is the workflow orchestration piece" in brief.draft
    assert "Python" in brief.draft
    assert "that's the kind of work I do" in brief.draft or "I work with" in brief.draft


def test_email_body_keeps_personalization_without_review_footer() -> None:
    gig = Gig(
        id="test-phone-body",
        source="hn",
        title="RAG engineer",
        url="https://example.com",
        description="Build RAG over internal docs with vector search and citations.",
    )

    body = email_body(gig)

    assert "The part that fits me is the need to turn scattered documents" in body
    assert "Review before sending" not in body
