"""Revenue-focused outreach drafts for ranked opportunities."""

from __future__ import annotations

import re
from dataclasses import dataclass

from jobpilot.gigs.core import preferences
from jobpilot.gigs.core.models import Gig


_SKILL_DISPLAY = {
    "three.js": "Three.js",
    "threejs": "Three.js",
    "react three fiber": "React Three Fiber",
    "r3f": "React Three Fiber",
    "webgpu": "WebGPU",
    "rag": "RAG",
    "retrieval-augmented": "retrieval-augmented generation",
    "agentic": "agentic workflows",
    "agent": "agent orchestration",
    "claude": "Claude",
    "anthropic": "Anthropic SDK",
    "mcp": "MCP",
    "model context protocol": "Model Context Protocol",
    "playwright": "Playwright",
    "browser-use": "browser-use",
    "chrome devtools": "Chrome DevTools Protocol",
    "next.js": "Next.js",
    "nextjs": "Next.js",
    "fastapi": "FastAPI",
    "python": "Python",
    "typescript": "TypeScript",
    "postgres": "Postgres",
    "sqlite": "SQLite",
    "vercel": "Vercel",
    "tailscale": "Tailscale",
}


def _display_skill(kw: str) -> str:
    return _SKILL_DISPLAY.get(kw.lower(), kw)


_PERSONALIZATION_SIGNALS: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        (
            "rag",
            "retrieval",
            "vector",
            "embedding",
            "knowledge base",
            "knowledge assistant",
            "document search",
            "internal docs",
        ),
        "the need to turn scattered documents into cited, usable answers",
    ),
    (
        (
            "workflow",
            "automation",
            "n8n",
            "zapier",
            "make.com",
            "crm",
            "slack",
            "calendar",
            "integration",
            "orchestration",
        ),
        "the workflow orchestration piece",
    ),
    (
        (
            "agent",
            "agentic",
            "tool use",
            "mcp",
            "model context protocol",
            "llm",
            "claude",
            "anthropic",
        ),
        "the practical agent/tooling angle",
    ),
    (
        (
            "three.js",
            "threejs",
            "react three fiber",
            "r3f",
            "webgl",
            "shader",
            "3d",
            "configurator",
            "virtual tour",
        ),
        "the interactive 3D and performance angle",
    ),
    (
        (
            "playwright",
            "browser-use",
            "chrome devtools",
            "browser automation",
            "scraping",
            "web automation",
        ),
        "the browser automation and integration work",
    ),
    (
        (
            "security",
            "privacy",
            "compliance",
            "sensitive",
            "governance",
            "audit",
        ),
        "the security and human-review constraints",
    ),
)


def _matched_personalization_skills(gig: Gig, limit: int = 2) -> list[str]:
    """Pick user-configured skills that are explicitly present in the gig."""
    text = _text(gig)
    matches: list[str] = []
    seen: set[str] = set()
    for keyword in preferences.skill_keywords():
        keyword_l = str(keyword).strip().lower()
        if not keyword_l or keyword_l not in text:
            continue
        display = _display_skill(keyword_l)
        normalized = display.lower()
        if normalized in seen:
            continue
        matches.append(display)
        seen.add(normalized)
        if len(matches) >= limit:
            break
    return matches


def _personalization_signal(gig: Gig) -> str:
    """Return a grounded phrase from the posting text, never invented research."""
    text = _text(gig)
    for keywords, phrase in _PERSONALIZATION_SIGNALS:
        if _has_any(text, keywords):
            return phrase

    title = _display_title(gig)
    if title:
        role = re.sub(r"\s+", " ", title).strip(" .").lower()
        return f"the {role} scope"
    return "the practical implementation scope"


def _tailored_hook(gig: Gig) -> str:
    """One grounded personalization sentence for phone-ready drafts.

    The hook uses only the listing's own text plus user-configured skill
    keywords. It avoids fake company research while removing the old manual
    step where the user had to write the first personalized line themselves.
    """
    signal = _personalization_signal(gig)
    skills = _matched_personalization_skills(gig)
    if skills:
        if len(skills) == 1:
            skill_text = skills[0]
        else:
            skill_text = f"{skills[0]} and {skills[1]}"
        return (
            f" What caught my eye is {signal}, especially the overlap with "
            f"{skill_text}; that maps closely to the practical systems I build."
        )
    return (
        f" What caught my eye is {signal}; that is the kind of practical "
        "implementation work I like."
    )


@dataclass(frozen=True)
class RevenueBrief:
    """A human-reviewed action draft for a single opportunity."""

    offer: str
    action: str
    draft: str


def email_subject(gig: Gig) -> str:
    """Compose a hireable, scannable email subject from a gig.

    Prefers `Company — Role` when both are known. Skips title entirely
    when it's marketing prose (a paragraph instead of a 'Company | Role' line).
    Caps at ~110 chars for iOS Mail preview.
    """
    company = (gig.company or "").strip()
    role = _display_title(gig)
    if company and role:
        head = f"{company} — {role}"
    elif company:
        head = company
    elif role:
        head = role
    else:
        head = "Your hiring post"
    return f"{head[:80]} — interested + brief background"[:110]


def email_body(gig: Gig) -> str:
    """Email-ready body (no internal review notes, no markdown)."""
    full = build_revenue_brief(gig).draft
    return full.split("\n\nReview before sending:", 1)[0].strip()


def _text(gig: Gig) -> str:
    return " ".join(
        [
            gig.title or "",
            gig.company or "",
            gig.description or "",
            " ".join(gig.tags or []),
        ]
    ).lower()


def _has_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def pick_offer(gig: Gig) -> str:
    """Map an opportunity to the clearest sellable offer."""
    text = _text(gig)

    if _has_any(
        text,
        (
            "rag",
            "retrieval",
            "vector",
            "embedding",
            "knowledge base",
            "knowledge assistant",
            "chatbot",
            "internal docs",
            "document search",
        ),
    ):
        return "RAG / internal knowledge assistant"

    if _has_any(
        text,
        (
            "three.js",
            "threejs",
            "react three fiber",
            "r3f",
            "webgl",
            "shader",
            "3d",
            "configurator",
            "virtual tour",
            "interactive map",
        ),
    ):
        return "Interactive 3D performance rescue"

    if _has_any(
        text,
        (
            "n8n",
            "zapier",
            "make.com",
            "workflow",
            "automation",
            "agent",
            "orchestration",
            "integrate",
            "integration",
            "crm",
            "email",
            "calendar",
            "slack",
            "mcp",
        ),
    ):
        return "AI workflow audit + one automation"

    return "AI workflow audit"


def _action_for_source(source: str) -> str:
    source = source.lower()
    if "upwork" in source:
        return "Review fit, then paste the draft into Upwork manually."
    if source in {"hn", "hackernews"}:
        return "Reply or email only if the company and scope are high-fit."
    return "Open the lead, verify buyer quality, then send the draft manually."


def _display_title(gig: Gig) -> str:
    """Short, readable role reference. Returns "" when title is unusable
    (e.g. HN posts where the first paragraph is marketing prose, not a header).
    Callers must handle the empty-string case explicitly.
    """
    raw = (gig.title or "").strip()
    if not raw:
        return ""
    head = raw.split(".")[0].split("|")[0].strip()
    if len(head) > 80 or len(head.split()) > 12:
        return ""
    return head


def _opening_line(gig: Gig) -> str:
    """First sentence of the draft — reads naturally for both header-format
    and prose-format gigs."""
    title = _display_title(gig)
    if title:
        return f"Hi - I saw the {title} post."
    if gig.company:
        return f"Hi - I saw {gig.company}'s hiring post."
    return "Hi - I saw your hiring post."


def build_revenue_brief(gig: Gig) -> RevenueBrief:
    """Create a concise proposal/DM draft for manual approval."""
    offer = pick_offer(gig)
    action = _action_for_source(gig.source)
    company = gig.company or "your team"
    opening = _opening_line(gig) + _tailored_hook(gig)
    # Outreach pages come from preferences (data/preferences.json, gitignored);
    # shipped defaults are neutral placeholders.
    pages = preferences.links()
    service_page = pages["service_page"]
    work_page = pages["work_page"]

    if offer == "RAG / internal knowledge assistant":
        body = (
            f"{opening} The scope looks like a fit for a lean RAG "
            "MVP: ingestion, chunking, citations, a simple web UI, and a clear update "
            "path for new documents.\n\n"
            "I build local-first AI and document-retrieval systems in Python/React, "
            "and I am careful about security boundaries and human review. I would start "
            "with a small architecture pass, then ship the first working assistant before "
            "expanding features.\n\n"
            f"Relevant work: {work_page}\n"
            "A good first milestone would be: corpus audit, retrieval design, one working "
            "chat path with cited answers, and deployment notes."
        )
    elif offer == "Interactive 3D performance rescue":
        body = (
            f"{opening} My strongest fit is stabilizing "
            "Three.js/R3F experiences that are slow, fragile, or struggling on mobile.\n\n"
            "I would begin with a focused performance triage: asset size, texture lifecycle, "
            "render-loop cost, camera/input ownership, and reduced-motion/mobile fallback. "
            "Then I would scope a short stabilization sprint around the highest-impact fixes.\n\n"
            f"Relevant work: {work_page}\n"
            "If useful, I can send a short teardown before we talk."
        )
    elif offer == "AI workflow audit + one automation":
        body = (
            f"{opening} This looks like a practical AI workflow problem, "
            "not a model-training problem. That is the lane I work in: LLM APIs, tools, "
            "workflow orchestration, and human-in-the-loop automation.\n\n"
            "I would start by mapping the current workflow, identifying the one automation "
            "with the fastest ROI, and keeping anything sensitive out of cloud AI unless it "
            "is explicitly safe.\n\n"
            f"Service outline: {service_page}\n"
            f"Relevant work: {work_page}\n"
            "First milestone: a bounded audit plus one working automation with docs."
        )
    else:
        body = (
            f"{opening} I think the fastest path is a bounded AI "
            "workflow audit before building. I would identify the current bottlenecks, "
            "separate no-AI fixes from AI-worthy work, and recommend the smallest useful "
            "automation.\n\n"
            f"Service outline: {service_page}\n"
            "If the audit does not surface clear savings, I would say that directly instead "
            "of trying to sell implementation."
        )

    signoff = "\n\n" + preferences.signoff_block()

    review_note = (
        "\n\nReview before sending: confirm scope, rate, buyer quality, and any platform "
        "rules. Do not auto-submit."
    )

    return RevenueBrief(offer=offer, action=action, draft=body + signoff + review_note)
