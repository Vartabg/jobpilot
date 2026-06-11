from jobpilot.gigs.core.models import Gig
from jobpilot.gigs.core.scorer import filter_and_rank, score_gig
from jobpilot.gigs.core.scoring_rules import TITLE_NEGATIVE_CAP


# ----- core invariants ---------------------------------------------------


def test_short_ai_keyword_does_not_match_maintain() -> None:
    """A bare DevOps job whose body mentions 'maintain' must not pick up
    a phantom 'ai' match from inside the word."""
    gig = Gig(
        id="devops",
        source="wwr",
        title="Senior DevOps Engineer",
        url="https://example.com",
        description="Maintain infrastructure and improve reliability for Linux systems.",
    )

    scored = score_gig(gig)

    assert not any(r.startswith("+4 ai") for r in scored.fit_reasons)
    assert any("generic job-board role" in r for r in scored.fit_reasons)


def test_saved_upwork_ai_lead_gets_revenue_bonus() -> None:
    gig = Gig(
        id="upwork-ai",
        source="upwork-export",
        title="AI Workflow Engineer",
        url="https://example.com",
        description="Need LLM workflow automation with n8n and CRM integration.",
        pay_hourly_est=80,
    )

    scored = score_gig(gig)

    assert scored.fit_score == 100
    assert "+25 saved Upwork lead" in scored.fit_reasons


def test_upwork_leads_sort_ahead_of_equal_score_job_board_roles() -> None:
    upwork = Gig(
        id="upwork-ai",
        source="upwork-export",
        title="AI Workflow Engineer",
        url="https://example.com/upwork",
        description="Need LLM workflow automation with n8n and CRM integration.",
        pay_hourly_est=80,
    )
    job_board = Gig(
        id="hn-ai",
        source="hn",
        title="AI Platform Engineer",
        url="https://example.com/hn",
        description="LLM agent workflow role with high salary.",
        salary_min=250000,
        salary_max=350000,
    )

    ranked = filter_and_rank([job_board, upwork], min_score=60, top_n=2)

    assert ranked[0].source == "upwork-export"


def test_intern_and_product_manager_roles_are_suppressed() -> None:
    intern = Gig(
        id="intern",
        source="wwr",
        title="DevOps Intern",
        url="https://example.com/intern",
        description="AI infrastructure intern role for students.",
    )
    pm = Gig(
        id="pm",
        source="wwr",
        title="Senior Product Manager, Agentic Commerce",
        url="https://example.com/pm",
        description="Lead product roadmap for AI shopping workflows.",
    )

    assert score_gig(intern).fit_score < 55
    assert score_gig(pm).fit_score < 55


def test_strong_fit_phrase_boosts_applied_ai_role() -> None:
    gig = Gig(
        id="applied-ai",
        source="hn",
        title="Applied AI Engineer",
        url="https://example.com/ai",
        description="Build RAG workflows and internal tools using Python.",
    )

    scored = score_gig(gig)

    assert scored.fit_score == 100
    assert "+15 strong-fit phrase in title" in scored.fit_reasons


# ----- regression: 2026-05-08 mis-ranking bug ----------------------------
#
# Before the fix, the GigPilot scan top-15 was dominated by Senior DevOps,
# QA Automation, and Product Marketing roles all scoring 100/100 because
# their descriptions mentioned "automation" and "ai". The fixes asserted
# below all came from `data/latest_leads.json` on 2026-05-08.


def test_devops_role_is_capped_even_when_body_mentions_ai_and_automation() -> None:
    """Real example from latest_leads.json (Woliba Senior DevOps Engineer)
    that scored 100/100 under the old scheme."""
    gig = Gig(
        id="wwr-woliba",
        source="wwr",
        title="Senior DevOps Engineer",
        company="Woliba",
        url="https://example.com",
        description=(
            "We're looking for a Senior DevOps Engineer to help us scale our cloud "
            "infrastructure globally. Architect, build, and maintain Woliba's AWS "
            "infrastructure to ensure security, scalability, and reliability. Lead "
            "automation initiatives for CI/CD, configuration management, and "
            "observability. AI-powered platform simplifies HR processes."
        ),
    )

    scored = score_gig(gig)

    assert scored.fit_score < 55, (
        f"DevOps role scored {scored.fit_score} — title-cap is not firing"
    )


def test_product_marketing_role_is_capped_even_when_body_mentions_ai() -> None:
    """Real example from latest_leads.json (Jimdo Senior Product Marketing
    Manager) that scored 98/100 under the old scheme."""
    gig = Gig(
        id="wwr-jimdo",
        source="wwr",
        title="Senior Product Marketing Manager",
        company="Jimdo",
        url="https://example.com",
        description=(
            "We use data, automation, and AI pragmatically and responsibly to build "
            "products. We build intuitive, AI-powered products that help customers."
        ),
    )

    scored = score_gig(gig)

    assert scored.fit_score < 55, (
        f"Product Marketing role scored {scored.fit_score} — title-cap is not firing"
    )


def test_qa_automation_role_does_not_ride_strong_fit_bonus_to_top() -> None:
    """Real example from latest_leads.json (Bjak Senior QA Automation
    Engineer) that scored 100/100 under the old scheme — 'automation
    engineer' was incorrectly in STRONG_FIT_TERMS."""
    gig = Gig(
        id="wwr-bjak",
        source="wwr",
        title="Senior QA Automation Engineer",
        company="Bjak",
        url="https://example.com",
        description=(
            "Design, develop, and enhance robust automation frameworks using modern "
            "tools (e.g., Playwright, Cypress, Selenium). Integrate automated tests "
            "into Git-based CI/CD pipelines."
        ),
    )

    scored = score_gig(gig)

    assert scored.fit_score < 55, (
        f"QA Automation role scored {scored.fit_score} — should not get the "
        f"strong-fit-phrase rescue"
    )


def test_real_ai_automation_role_still_scores_high() -> None:
    """The above suppression must not also kill genuine AI Automation Engineer
    roles, which are the user's primary Upwork-style target."""
    gig = Gig(
        id="upwork-ai-auto",
        source="upwork-export",
        title="AI Automation Engineer Needed (LLM + Workflow Integration)",
        url="https://example.com",
        description=(
            "Build LLM-orchestrated automation workflows with n8n, Claude, and OpenAI."
        ),
        pay_hourly_est=80,
    )

    scored = score_gig(gig)

    assert scored.fit_score >= 90


def test_forward_deployed_applied_ai_scores_at_top() -> None:
    """Mirrors jobpilot's 2026-05-08 #1: Anthropic Forward Deployed Applied
    AI Engineer. Should be at or near 100 under the gigpilot scheme too."""
    gig = Gig(
        id="ats-anthropic-fwd",
        source="greenhouse",  # not in JOB_BOARD_SOURCES
        title="Forward Deployed Engineer, Applied AI",
        company="Anthropic",
        url="https://example.com",
        description=(
            "Embed directly with enterprise customers, build prototypes with Claude, "
            "ship MCP-based integrations and agent workflows."
        ),
    )

    scored = score_gig(gig)

    assert scored.fit_score == 100


def test_three_js_role_is_recognized() -> None:
    """preferences.json calls out three.js / webgpu — these should land
    on-track too, not get suppressed."""
    gig = Gig(
        id="ats-3d",
        source="greenhouse",
        title="Senior Three.js Engineer",
        company="Studio X",
        url="https://example.com",
        description="Build WebGPU-powered 3D experiences in Next.js.",
    )

    scored = score_gig(gig)

    assert scored.fit_score >= 70


def test_pay_below_preferences_floor_is_dropped_from_rank() -> None:
    low = Gig(
        id="low-pay",
        source="wwr",
        title="AI Automation Engineer",
        url="https://example.com",
        description="Build LLM workflows and agent tooling.",
        pay_hourly_est=40,
    )
    ok = Gig(
        id="ok-pay",
        source="wwr",
        title="AI Automation Engineer",
        url="https://example.com/2",
        description="Build LLM workflows and agent tooling.",
        pay_hourly_est=90,
    )

    ranked = filter_and_rank([low, ok], min_score=55, top_n=5)

    assert [g.id for g in ranked] == ["ok-pay"]


def test_overlapping_title_patterns_score_only_the_longest() -> None:
    """Saturation regression (2026-06): 'AI Automation Engineer' matched 3
    nested patterns ('ai automation engineer' + 'ai automation' +
    'automation engineer') for +64 from one title — nearly everything
    surfaced at 97-100. Only the longest pattern may score."""
    gig = Gig(
        id="hn-overlap",
        source="hn",
        title="AI Automation Engineer",
        url="https://example.com",
        description="",
    )

    scored = score_gig(gig)

    eng_reasons = [r for r in scored.fit_reasons if r.startswith("+") and " title:" in r]
    assert eng_reasons == ["+30 title:ai automation engineer"]


def test_score_separation_between_target_and_generic_automation_role() -> None:
    """The de-stacked title layer must leave room between a true target
    shape and a generic automation role that previously rode the bare
    'automation engineer' rescue."""
    target = Gig(
        id="ats-target",
        source="greenhouse",
        title="Forward Deployed Engineer, Applied AI",
        company="Anthropic",
        url="https://example.com/1",
        description="Build prototypes with Claude, ship MCP-based agent workflows.",
    )
    generic = Gig(
        id="wwr-generic",
        source="wwr",
        title="Automation Engineer",
        url="https://example.com/2",
        description="Automate internal workflows with Zapier and Make.com.",
    )

    assert score_gig(target).fit_score == 100
    assert score_gig(generic).fit_score < 55


def test_qa_automation_title_is_not_rescued_by_bare_automation_engineer() -> None:
    """'automation engineer' is out of TITLE_ENGINEERING_PATTERNS — a Senior
    QA Automation Engineer must hit the QA title cap no matter how many AI
    buzzwords the body lists; rescue requires an AI-flavored pattern."""
    qa = Gig(
        id="ats-qa",
        source="greenhouse",  # no job-board penalty — isolates the cap
        title="Senior QA Automation Engineer",
        url="https://example.com",
        description=(
            "LLM-adjacent test automation with Playwright, Claude-based "
            "agents, and RAG regression checks."
        ),
    )

    scored = score_gig(qa)

    assert scored.fit_score <= TITLE_NEGATIVE_CAP
    assert not any("title:automation engineer" in r for r in scored.fit_reasons)


def test_below_floor_pay_only_drops_when_parse_is_confident() -> None:
    """A confident parse (explicit range / hourly) below the floor is
    dropped; a single-ended salary is too weak to hard-drop on — the -25
    score penalty still applies, but the gig survives the rank filter."""
    confident = Gig(
        id="confident-low",
        source="hn",
        title="Applied AI Engineer",
        url="https://example.com/1",
        description="Build RAG workflows and internal tools using Python.",
        salary_min=70000,
        salary_max=80000,  # $40/hr equivalent — explicit range, below floor
    )
    unconfident = Gig(
        id="unconfident-low",
        source="hn",
        title="Applied AI Engineer",
        url="https://example.com/2",
        description="Build RAG workflows and internal tools using Python.",
        salary_max=80000,  # single-ended — not confident enough to drop
    )

    ranked = filter_and_rank([confident, unconfident], min_score=55, top_n=5)

    assert [g.id for g in ranked] == ["unconfident-low"]


def test_skill_bonus_is_capped_so_ad_keyword_spam_cannot_saturate() -> None:
    """A non-engineering ad can't reach 100 just by listing every AI buzzword
    in the description."""
    gig = Gig(
        id="wwr-spam",
        source="wwr",
        title="Senior Marketing Manager",
        company="Acme",
        url="https://example.com",
        description=(
            "We use AI, agents, automation, workflows, RAG, claude, anthropic, "
            "openai, llm, mcp, vector embeddings, and orchestration."
        ),
    )

    scored = score_gig(gig)

    # Title is non-engineering → must hit the title cap regardless of body
    assert scored.fit_score <= 45
