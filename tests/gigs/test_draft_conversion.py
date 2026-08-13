"""Tier-5: role-aware drafts (FTE vs contract), subject, and follow-up nudge."""

from datetime import datetime

from jobpilot.gigs.core import pipeline, proposals
from jobpilot.gigs.core.models import Gig
from jobpilot.gigs.core.pipeline import Row


def _gig(**kw):
    base = dict(id="hn-1", source="hn", title="AI Engineer", company="Acme",
                description="rag, agentic workflows, python", url="https://post.test/1")
    base.update(kw)
    return Gig(**base)


# --- FTE vs contract framing ----------------------------------------------

def test_fte_draft_leads_with_engineer_framing_and_cta():
    body = proposals.email_body(_gig(title="Founding Engineer"))
    assert "Is this still open?" in body          # low-friction CTA
    assert "add a verified background bullet" in body
    assert "Service outline" not in body          # no contractor service pitch
    assert "example.com" not in body
    # humble/straight: no flourish words
    for flourish in ("end to end", "ships end to end", "the glue between"):
        assert flourish not in body


def test_fte_hn_role_is_not_labeled_workflow_audit():
    """Mobile Apply was shipping freelance 'AI workflow audit' on FTE posts."""
    g = _gig(
        title="Forward Deployed Engineer, Applied AI",
        description="Embed with customers; ship agent workflows with Claude and MCP.",
    )
    assert proposals.draft_mode(g) == "fte"
    assert proposals.pick_offer(g) == "Forward Deployed / Solutions"
    brief = proposals.build_revenue_brief(g)
    assert "workflow audit" not in brief.offer.lower()
    assert "Service outline" not in brief.draft
    assert "Is this still open?" in brief.draft


def test_fte_ai_engineer_track_maps_to_applied_ai():
    g = _gig(title="AI Engineer", description="Build agents with Python and Claude.")
    assert proposals.pick_offer(g) == "Applied AI / builder"
    assert proposals.draft_mode(g) == "fte"


def test_contract_draft_keeps_service_framing():
    body = proposals.email_body(_gig(id="up-1", source="upwork",
                                     title="Contract: automation freelancer"))
    assert "Service outline" in body
    assert "scope it" in body
    g = _gig(id="up-1", source="upwork", title="Contract: automation freelancer",
             description="n8n llm crm slack")
    assert proposals.draft_mode(g) == "contract"
    assert "audit" in proposals.pick_offer(g).lower() or "automation" in proposals.pick_offer(g).lower()


def test_is_contract_lead_detection():
    assert proposals._is_contract_lead(_gig(id="up", source="upwork")) is True
    assert proposals._is_contract_lead(_gig(title="Contract AI Engineer")) is True
    assert proposals._is_contract_lead(_gig(title="Senior AI Engineer")) is False


def test_subject_is_role_specific_without_inferred_candidate_title():
    subj = proposals.email_subject(_gig(title="Founding Engineer"))
    assert subj.startswith("Interest in ")
    assert "Founding Engineer" in subj
    assert "full-stack engineer" not in subj.lower()
    assert len(subj) <= 110


# --- follow-up nudge -------------------------------------------------------

_TODAY = datetime(2026, 6, 19)


def test_followups_due_picks_stale_sent_rows():
    rows = [
        Row(status="sent", company="A", role="R1", last_touched="6/10"),   # 9 days → due
        Row(status="sent", company="B", role="R2", last_touched="6/18"),   # 1 day → not
        Row(status="replied", company="C", role="R3", last_touched="6/1"), # replied → not
        Row(status="new", company="D", role="R4", last_touched="6/1"),     # not sent
        Row(status="sent", company="E", role="R5", last_touched=""),       # no date → skip
    ]
    due = pipeline.followups_due(rows, days=3, today=_TODAY)
    assert [r.company for r in due] == ["A"]


def test_followup_message_includes_role_and_company():
    msg = proposals.followup_message("Acme", "Senior Engineer")
    assert "Senior Engineer" in msg and "Acme" in msg
    assert "Following up" in msg
