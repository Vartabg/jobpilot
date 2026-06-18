"""
Score each gig 0-100 against the user's profile + pay thresholds.

Scoring has two layers:

1. **Title layer** — what the role actually IS, not what the ad says.
   `TITLE_ENGINEERING_PATTERNS` matches AI-engineering job-title shapes
   (forward deployed, applied ai, ai engineer, agent builder, etc.) and
   `TITLE_NEGATIVES` matches DevOps / Marketing / Sales / PM / Intern shapes.
   When a title hits a negative without an engineering rescue, the role is
   hard-capped at `TITLE_NEGATIVE_CAP` so the description layer can't push
   it past the threshold on incidental keyword matches.

2. **Full-text layer** — `SKILL_WEIGHTS` adds capped contribution from the
   user's stack (rag, claude, mcp, three.js, playwright, …). The cap
   prevents description noise (a Marketing ad that lists every AI buzzword)
   from saturating the score.

Profile signal (default calibration — see scoring_rules.py; per-user
override is a phase-3 follow-up):
- Solo builder shipping AI agents, browser automation, full-stack
- Stack: claude, mcp, rag, agentic, three.js / webgpu, playwright, next.js
- Remote-friendly
- Target roles: Forward Deployed, Applied AI, Agent Builder, AI Engineer,
  AI Architect, Solutions Engineer

Pay floors (preferences.json):
- Floor: $65/hr or $130K/yr — below is noise
- Target: $90/hr or $175K/yr
"""

from __future__ import annotations

import re

from jobpilot.gigs.core import preferences
from jobpilot.gigs.core.models import Gig
from jobpilot.core.work_style import is_contract_friendly, is_w2_only, score_work_style
from jobpilot.gigs.core.scoring_rules import (
    DOMAIN_BONUS,
    JOB_BOARD_SOURCES,
    NEGATIVE_TERMS,
    REVENUE_TERMS,
    SCAM_SIGNALS,
    SKILL_WEIGHTS,
    SKILL_WEIGHTS_CAP,
    STRONG_FIT_TERMS,
    TITLE_ENGINEERING_PATTERNS,
    TITLE_NEGATIVE_CAP,
    TITLE_NEGATIVES,
    TITLE_TECH_BONUS,
)


def _pay_floor_hourly() -> float:
    pay = preferences.pay()
    floor_hourly = float(pay.get("floor_hourly_usd", 65))
    floor_annual = float(pay.get("floor_annual_usd", 130000))
    return max(floor_hourly, floor_annual / 2000)


def _normalize_pay(gig: Gig) -> float:
    """Reduce everything to a single hourly-equivalent for sorting."""
    if gig.salary_max and gig.salary_max > 0:
        # Annual max → hourly assuming 2000 hrs
        return gig.salary_max / 2000
    if gig.salary_min and gig.salary_min > 0:
        return gig.salary_min / 2000
    if gig.pay_hourly_est:
        return gig.pay_hourly_est
    return 0.0


def _source_priority(gig: Gig) -> int:
    source = gig.source.lower()
    if "upwork" in source:
        return 3
    if source == "hn":
        return 2
    return 1


def apply_friction(gig: Gig) -> int:
    """Estimate how many taps stand between the user and a sent application.

    Lower = better. Used as a tiebreaker so 100/100 mailto gigs sort above
    100/100 paywall-gated WWR gigs.

    Heuristic-only — based on the apply_url scheme + source. We don't fetch
    the page or verify the actual flow.
    """
    apply = (gig.apply_url or gig.url or "").lower()
    if apply.startswith("mailto:"):
        return 1  # composer opens prefilled, attach resume, send
    if "boards.greenhouse.io" in apply or "jobs.lever.co" in apply or "ashbyhq.com" in apply:
        return 3  # ATS form: autofill + upload + screening Qs + CAPTCHA
    if "google.com/search" in apply:
        return 5  # Google search → find result → land on careers → form
    if "weworkremotely.com" in apply:
        return 6  # paywalled aggregator
    if "/careers" in apply or "/jobs" in apply or "/apply" in apply:
        return 3  # company-hosted careers page
    if "news.ycombinator.com/item" in apply:
        return 5  # HN thread, no extracted target
    return 4  # generic company URL → user has to find apply path


def _has_keyword(text: str, keyword: str) -> bool:
    """Avoid false positives like 'ai' inside 'maintain'."""
    if len(keyword) <= 8 or keyword in {"make.com", "ts/sci"}:
        pattern = rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])"
        return re.search(pattern, text) is not None
    return keyword in text


def _phrase_in(text: str, phrase: str) -> bool:
    """Substring match; phrases are multi-word so word-boundary noise is rare."""
    return phrase in text


def score_gig(gig: Gig) -> Gig:
    """Mutate gig in place: set fit_score (0-100) and fit_reasons."""
    title = (gig.title or "").lower()
    description = (gig.description or "").lower()
    full_text = " ".join([
        title,
        description,
        " ".join(gig.tags or []),
        (gig.company or "").lower(),
    ])

    score = 30  # baseline for existing-at-all
    reasons: list[str] = []

    # ----- Title layer -----
    # Overlapping phrases ("ai automation engineer" ⊃ "ai automation" ⊃
    # "automation engineer") must not stack — one title scoring +64 across
    # three nested patterns is how everything saturated at 97-100. Only the
    # longest match scores (weight breaks length ties); every match still
    # counts as a rescue from TITLE_NEGATIVES and the job-board penalty.
    title_eng_hits = [
        phrase for phrase in TITLE_ENGINEERING_PATTERNS if _phrase_in(title, phrase)
    ]
    if title_eng_hits:
        best = max(
            title_eng_hits,
            key=lambda p: (len(p), TITLE_ENGINEERING_PATTERNS[p]),
        )
        w = TITLE_ENGINEERING_PATTERNS[best]
        score += w
        reasons.append(f"+{w} title:{best}")

    for phrase, w in TITLE_TECH_BONUS.items():
        if _phrase_in(title, phrase):
            score += w
            reasons.append(f"+{w} title:{phrase}")

    title_neg_hits: list[str] = []
    for phrase, w in TITLE_NEGATIVES.items():
        if _phrase_in(title, phrase):
            score += w
            title_neg_hits.append(phrase)
            reasons.append(f"{w} title:{phrase}")

    # If the title is DevOps/Marketing/Sales/PM-shaped and nothing in
    # TITLE_ENGINEERING_PATTERNS matched, the role is off-track regardless
    # of how many AI buzzwords appear in the description.
    title_capped = bool(title_neg_hits) and not title_eng_hits

    # ----- Full-text skill layer (capped) -----
    skill_total = 0
    for kw, w in SKILL_WEIGHTS.items():
        if _has_keyword(full_text, kw):
            skill_total += w
            reasons.append(f"+{w} {kw}")
    if skill_total > SKILL_WEIGHTS_CAP:
        reasons.append(f"cap-{SKILL_WEIGHTS_CAP} skill bonus capped (raw {skill_total})")
        skill_total = SKILL_WEIGHTS_CAP
    score += skill_total

    # ----- Spam + description-level negatives -----
    for kw, w in SCAM_SIGNALS.items():
        if kw in full_text:
            score += w
            reasons.append(f"{w} {kw}")

    for kw, w in NEGATIVE_TERMS.items():
        if _has_keyword(full_text, kw):
            score += w
            reasons.append(f"{w} {kw}")

    # ----- Domain (company) bonus -----
    for kw, w in DOMAIN_BONUS.items():
        if _has_keyword(full_text, kw):
            score += w
            reasons.append(f"+{w} {kw}")

    # ----- Source bonus + revenue / strong-fit phrase -----
    if "upwork" in gig.source.lower():
        score += 25
        reasons.append("+25 saved Upwork lead")

    if any(_has_keyword(full_text, term) for term in REVENUE_TERMS):
        score += 8
        reasons.append("+8 revenue-term fit")

    if any(_phrase_in(title, term) for term in STRONG_FIT_TERMS):
        score += 15
        reasons.append("+15 strong-fit phrase in title")

    # ----- Generic-job-board penalty -----
    # WWR/RemoteOK/Himalayas/HN postings without a title-level engineering
    # signal get hit hard — most of them match an incidental "automation"
    # or "ai" in the description.
    if (
        gig.source in JOB_BOARD_SOURCES
        and not title_eng_hits
        and not any(_phrase_in(title, term) for term in STRONG_FIT_TERMS)
    ):
        score -= 25
        reasons.append("-25 generic job-board role (no AI title signal)")

    # ----- Pay -----
    hourly_eq = _normalize_pay(gig)
    floor_h = _pay_floor_hourly()
    if hourly_eq and hourly_eq < floor_h:
        score -= 25
        reasons.append(f"-25 pay ${hourly_eq:.0f}/hr (below floor ${floor_h:.0f}/hr)")
    elif hourly_eq >= 125:
        score += 15
        reasons.append(f"+15 pay ${hourly_eq:.0f}/hr")
    elif hourly_eq >= 75:
        score += 10
        reasons.append(f"+10 pay ${hourly_eq:.0f}/hr")
    elif hourly_eq >= 50:
        score += 5
        reasons.append(f"+5 pay ${hourly_eq:.0f}/hr")

    # ----- Work style (autonomy / contract / anti-9-5) -----
    ws_delta, ws_reasons = score_work_style(full_text, title=gig.title or "")
    if ws_delta:
        score += ws_delta
        reasons.extend(ws_reasons[:4])

    # ----- Title-cap -----
    if title_capped:
        if score > TITLE_NEGATIVE_CAP:
            reasons.append(f"cap-{TITLE_NEGATIVE_CAP} title is non-engineering (no AI rescue)")
        score = min(score, TITLE_NEGATIVE_CAP)

    gig.fit_score = max(0, min(100, score))
    gig.fit_reasons = reasons[:8]
    return gig


def _pay_parse_is_confident(gig: Gig) -> bool:
    """True when the comp came from an explicit salary range or hourly figure.

    Hourly estimates only exist when the text carried a per-hour marker, and
    a salary range needs both ends — a single-ended salary (or partial data
    from a cross-source merge) is too weak to hard-drop a gig on. The -25
    below-floor penalty in score_gig applies either way."""
    if gig.pay_hourly_est:
        return True
    return bool(gig.salary_min and gig.salary_max)


def filter_and_rank(
    gigs: list[Gig],
    min_score: int = 55,
    top_n: int = 15,
    *,
    contract_first: bool = False,
    drop_rigid_schedule: bool = False,
) -> list[Gig]:
    """Score every gig, drop the weak ones, return top N sorted.

    Below-floor pay only hard-drops a gig when the parse is confident — a
    comp mis-parse must not silently kill a good gig (the 2026-06 "$70-$90
    per hour read as $70K-$90K" bug). Unstated pay always passes.

    ``contract_first`` drops explicit W-2-only postings unless contract
    signals are also present. ``drop_rigid_schedule`` removes postings with
    strong 9-5 / core-hours language.

    Sort keys (highest priority first):
      1. fit_score (descending)
      2. apply_friction (ascending — lower friction wins ties)
      3. source priority (Upwork > HN > everything else)
      4. pay (descending)
    """
    from jobpilot.core.work_style import is_schedule_rigid

    floor_h = _pay_floor_hourly()
    scored = [score_gig(g) for g in gigs]
    kept = [
        g for g in scored
        if g.fit_score >= min_score
        and (
            _normalize_pay(g) >= floor_h
            or not _pay_parse_is_confident(g)
        )
    ]
    if contract_first:
        kept = [
            g for g in kept
            if is_contract_friendly(
                " ".join([g.description or "", g.title or ""]),
                title=g.title or "",
            )
            or not is_w2_only(
                " ".join([g.description or "", g.title or ""]),
                title=g.title or "",
            )
        ]
    if drop_rigid_schedule:
        kept = [
            g for g in kept
            if not is_schedule_rigid(
                " ".join([g.description or "", g.title or ""]),
                title=g.title or "",
            )
        ]
    kept.sort(key=lambda g: (
        -g.fit_score,
        apply_friction(g),
        -_source_priority(g),
        -_normalize_pay(g),
    ))
    return kept[:top_n]
