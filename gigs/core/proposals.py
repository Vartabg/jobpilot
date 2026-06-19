"""Revenue-focused outreach drafts for ranked opportunities."""

from __future__ import annotations

import re
from dataclasses import dataclass

from jobpilot.gigs.core import preferences
from jobpilot.gigs.core.logger import get_logger
from jobpilot.gigs.core.models import Gig

log = get_logger(__name__)

# Strings that must never reach an employer — the neutral-default placeholders.
# A leak here means a draft shipped before identity/links resolved (see the
# example.com portfolio bug). Cheap regression net on top of the resolver fix.
_PLACEHOLDER_MARKERS = (
    "example.com",
    "your-portfolio",
    "your-handle",
    "your city, st",
    "your professional tagline",
)


def contains_placeholder(text: str) -> str | None:
    """Return the first placeholder marker found in `text`, or None if clean."""
    low = (text or "").lower()
    for marker in _PLACEHOLDER_MARKERS:
        if marker in low:
            return marker
    return None


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
        return f" The part that fits me is {signal} — I work with {skill_text}."
    return f" The part that fits me is {signal} — that's the kind of work I do."


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
    tag = _SUBJECT_TAG.get(pick_offer(gig), "full-stack + AI engineer")
    return f"{head[:72]} — {tag}"[:110]


# Short value tag for the subject line, by offer — leads with relevant value
# instead of the old generic "interested + brief background".
_SUBJECT_TAG = {
    "RAG / internal knowledge assistant": "AI / retrieval engineer",
    "Interactive 3D performance rescue": "front-end + 3D engineer",
    "AI workflow audit + one automation": "AI automation engineer",
    "AI workflow audit": "AI / automation engineer",
}


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

    # Require a real front-end 3D stack signal. A bare "3d" matched "3D
    # geologic models" / CAD / data-viz and mis-pitched a Three.js rescue to
    # backend roles — the offer must be corroborated by an actual web-3D tool.
    if _has_any(
        text,
        (
            "three.js",
            "threejs",
            "react three fiber",
            "r3f",
            "webgl",
            "webgpu",
            "shader",
            "babylon.js",
            "babylonjs",
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


def followup_message(company: str = "", role: str = "") -> str:
    """A short, neutral 2nd-touch nudge for a sent-but-quiet application."""
    what = role.strip() or "the role"
    where = f" at {company.strip()}" if company.strip() else ""
    return (
        f"Following up on my note about {what}{where} — still very interested. "
        "Happy to share more or hop on a quick call if useful."
    )


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


_CONTRACT_TITLE_RE = re.compile(
    r"\b(contract|contractor|freelance|freelancer|1099|c2c|corp[- ]to[- ]corp)\b",
    re.IGNORECASE,
)


def _is_contract_lead(gig: Gig) -> bool:
    """Contractor framing only for genuine contract/freelance sources. Most of
    the pipeline is full-time, where leading with 'contractor' reads to an FTE
    recruiter as a flight risk."""
    if "upwork" in (gig.source or "").lower():
        return True
    return bool(_CONTRACT_TITLE_RE.search(f"{gig.title or ''} {gig.description or ''}"))


# Full-time framing: what the applicant BUILDS (an engineer who ships end to
# end), not a contractor pitching an audit. Phrasing draws on the user's stack.
_FTE_CAPABILITY = {
    "RAG / internal knowledge assistant":
        "I build AI and document-retrieval systems in Python and React, and I "
        "keep security and human review in mind.",
    "Interactive 3D performance rescue":
        "I build web and 3D front-ends in React/Next.js and Three.js, including "
        "performance work on mobile.",
    "AI workflow audit + one automation":
        "I work on practical LLM and automation workflows — tools, "
        "orchestration, and human-in-the-loop. Not model training.",
    "AI workflow audit":
        "I build practical AI and automation with LLM APIs and tools, with "
        "human review in the loop.",
}
_FTE_CAPABILITY_DEFAULT = (
    "I'm a full-stack and AI engineer. I build web apps, LLM and agent tools, "
    "and the automation around them."
)

# Contract framing: a bounded, service-shaped offer (appropriate for Upwork /
# explicit contract roles).
_CONTRACT_CAPABILITY = {
    "RAG / internal knowledge assistant":
        "The scope looks like a fit for a lean RAG MVP: ingestion, chunking, "
        "citations, a simple UI, and a clear update path. I'd start with a small "
        "architecture pass, then ship the first working assistant.",
    "Interactive 3D performance rescue":
        "My strongest fit is stabilizing Three.js/R3F experiences that are slow "
        "or fragile on mobile — a focused performance triage, then a short pass "
        "on the highest-impact fixes.",
    "AI workflow audit + one automation":
        "This looks like a practical AI workflow problem, not a model-training "
        "one. I'd map the current workflow, find the one automation with the "
        "fastest ROI, and keep anything sensitive out of cloud AI.",
    "AI workflow audit":
        "I think the fastest path is a bounded AI workflow audit before "
        "building — separating no-AI fixes from AI-worthy work and recommending "
        "the smallest useful automation.",
}
_CONTRACT_CAPABILITY_DEFAULT = _CONTRACT_CAPABILITY["AI workflow audit"]

# Offer → which background bullet to use as the concrete proof line.
_OFFER_PROOF_KEY = {
    "RAG / internal knowledge assistant": "ai_agent_systems",
    "Interactive 3D performance rescue": "full_stack_web",
    "AI workflow audit + one automation": "browser_automation",
    "AI workflow audit": "developer_tooling",
}


def _proof_bullet(offer: str) -> str:
    """One concrete proof sentence from the user's background bullets, matched
    to the offer. Skips bullets still at the neutral placeholder."""
    bullets = preferences.background_bullets()
    defaults = preferences.DEFAULTS["background_bullets"]
    for key in (_OFFER_PROOF_KEY.get(offer, ""), "ai_agent_systems", "elevator_pitch"):
        val = bullets.get(key, "")
        if val and val != defaults.get(key):
            return val
    return ""


def build_revenue_brief(gig: Gig) -> RevenueBrief:
    """A concise, role-aware outreach draft for manual approval.

    Structure: a grounded fit line → one concrete proof → a low-friction CTA.
    Full-time roles get a builder/engineer framing; genuine contract/Upwork
    sources keep the service/audit framing.
    """
    offer = pick_offer(gig)
    action = _action_for_source(gig.source)
    contract = _is_contract_lead(gig)
    opening = _opening_line(gig) + _tailored_hook(gig)
    proof = _proof_bullet(offer)
    pages = preferences.links()

    parts = [opening]
    if contract:
        parts.append(_CONTRACT_CAPABILITY.get(offer, _CONTRACT_CAPABILITY_DEFAULT))
        if proof:
            parts.append(f"A bit of relevant work: {proof}")
        parts.append(f"Service outline: {pages['service_page']} · Relevant work: {pages['work_page']}")
        parts.append("Open to a short call to scope it? Happy to share more first.")
    else:
        parts.append(_FTE_CAPABILITY.get(offer, _FTE_CAPABILITY_DEFAULT))
        if proof:
            parts.append(proof)
        parts.append(f"Relevant work: {pages['work_page']}")
        parts.append("Is this still open? I'd welcome a short call.")

    review_note = (
        "\n\nReview before sending: confirm scope, rate, buyer quality, and any platform "
        "rules. Do not auto-submit."
    )

    draft = "\n\n".join(parts) + "\n\n" + preferences.signoff_block() + review_note
    leak = contains_placeholder(draft)
    if leak:
        log.error(
            "Draft for %s contains placeholder %r — identity/links did not "
            "resolve. Fix data/gigs/preferences.json or the jobpilot profile "
            "before sending.",
            gig.id, leak,
        )
    return RevenueBrief(offer=offer, action=action, draft=draft)
