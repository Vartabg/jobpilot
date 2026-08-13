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


def _listing_skill_mentions(gig: Gig, limit: int = 2) -> list[str]:
    """Pick discovery keywords explicitly present in the listing.

    These keywords help notice relevant posting language; they are not
    candidate evidence and must never be phrased as the sender's skills.
    """
    text = _text(gig)
    matches: list[str] = []
    seen: set[str] = set()
    for keyword in preferences.skill_keywords():
        keyword_l = str(keyword).strip().lower()
        if not keyword_l or not _contains_term(text, keyword_l):
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

    The hook reports only what appears in the listing. Discovery keywords may
    select a phrase, but they never become a claim about the candidate.
    """
    skills = _listing_skill_mentions(gig)
    if not skills:
        return ""
    signal = _personalization_signal(gig)
    skill_text = skills[0] if len(skills) == 1 else f"{skills[0]} and {skills[1]}"
    return f" I noticed {signal}; the posting mentions {skill_text}."


@dataclass(frozen=True)
class RevenueBrief:
    """A human-reviewed action draft for a single opportunity."""

    offer: str
    action: str
    draft: str


def email_subject(gig: Gig) -> str:
    """Compose a neutral subject without inferring the sender's discipline."""
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
    return f"Interest in {head[:88]}"[:110]


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


def _contains_term(text: str, term: str) -> bool:
    """Match a posting term lexically, including punctuated skill labels."""
    normalized = str(term or "").strip().lower()
    if not normalized:
        return False
    return re.search(
        rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])",
        text,
        re.IGNORECASE,
    ) is not None


def _has_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(_contains_term(text, keyword) for keyword in keywords)


def _pick_stack_offer(text: str) -> str | None:
    """Stack-specific offers that apply in both FTE and contract modes."""
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
    return None


def _pick_fte_track(gig: Gig) -> str:
    """FTE track label — job-search shaped, not freelance product names.

    Drives resume selection + swipe "Angle" + subject tag. Default aligns to
    the Forward Deployed / Solutions thesis (mid-level, field + builder).
    """
    title = (gig.title or "").lower()
    text = _text(gig)

    fde_title = (
        "forward deployed", "solutions engineer", "implementation engineer",
        "customer engineer", "customer success engineer", "technical support",
        "support engineer", "field engineer", "field service", "deployed engineer",
    )
    if any(p in title for p in fde_title):
        return "Forward Deployed / Solutions"

    ai_title = (
        "applied ai", "ai engineer", "llm engineer", "agent engineer",
        "agent builder", "ml engineer", "ai automation", "ai architect",
    )
    if any(p in title for p in ai_title):
        return "Applied AI / builder"

    fs_title = (
        "full stack", "fullstack", "full-stack", "founding engineer",
        "software engineer", "product engineer",
    )
    if any(p in title for p in fs_title):
        return "Full-stack / product engineer"

    # Body-only signals when the title is vague.
    if _has_any(text, ("forward deployed", "solutions engineer", "implementation")):
        return "Forward Deployed / Solutions"
    if _has_any(text, ("llm", "agentic", "mcp", "claude", "rag")):
        return "Applied AI / builder"

    return "Forward Deployed / Solutions"


def _pick_contract_offer(text: str) -> str:
    """Freelance / Upwork product-shaped offer names."""
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


def pick_offer(gig: Gig) -> str:
    """Map an opportunity to the clearest offer / track label.

    Contract/Upwork → service product names (audit, automation).
    Full-time boards → FTE tracks (FDE/Solutions, Applied AI, full-stack)
    so the phone never leads with a freelance pitch on a company job.
    """
    text = _text(gig)
    stack = _pick_stack_offer(text)
    if stack:
        return stack
    if _is_contract_lead(gig):
        return _pick_contract_offer(text)
    return _pick_fte_track(gig)


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


# Offer → which background bullet to use as the concrete proof line.
_OFFER_PROOF_KEY = {
    "RAG / internal knowledge assistant": "ai_agent_systems",
    "Interactive 3D performance rescue": "full_stack_web",
    "AI workflow audit + one automation": "browser_automation",
    "AI workflow audit": "developer_tooling",
    "Forward Deployed / Solutions": "field_engineering",
    "Applied AI / builder": "ai_agent_systems",
    "Full-stack / product engineer": "full_stack_web",
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


def draft_mode(gig: Gig) -> str:
    """'contract' or 'fte' — exposed on the phone card so the mode is visible."""
    return "contract" if _is_contract_lead(gig) else "fte"


def build_revenue_brief(gig: Gig) -> RevenueBrief:
    """A concise, role-aware outreach draft for manual approval.

    Structure: a grounded fit line → one concrete proof → a low-friction CTA.
    Full-time roles get a builder/field framing; genuine contract/Upwork
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
        if proof:
            parts.append(f"A bit of relevant work: {proof}")
        configured_pages = [
            value
            for value in (pages.get("service_page", ""), pages.get("work_page", ""))
            if value and not contains_placeholder(value)
        ]
        if configured_pages:
            parts.append("Service outline / relevant work: " + " · ".join(configured_pages))
        parts.append("Open to a short call to scope it? Happy to share more first.")
    else:
        if proof:
            parts.append(proof)
        else:
            parts.append(
                "Please add a verified background bullet before sending this draft."
            )
        work_page = pages.get("work_page", "")
        if work_page and not contains_placeholder(work_page):
            parts.append(f"Relevant work: {work_page}")
        parts.append("Is this still open? I'd welcome a short call.")

    review_note = (
        "\n\nReview before sending: confirm scope, rate, buyer quality, and any platform "
        "rules. Do not auto-submit."
    )

    signoff = preferences.signoff_block()
    if signoff:
        parts.append(signoff)
    draft = "\n\n".join(parts) + review_note
    leak = contains_placeholder(draft)
    if leak:
        log.error(
            "Draft for %s contains placeholder %r — identity/links did not "
            "resolve. Fix data/gigs/preferences.json or the jobpilot profile "
            "before sending.",
            gig.id, leak,
        )
    return RevenueBrief(offer=offer, action=action, draft=draft)
