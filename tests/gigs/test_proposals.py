from jobpilot.gigs.core import preferences
from jobpilot.gigs.core.models import Gig
from jobpilot.gigs.core.proposals import build_revenue_brief, pick_offer


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
