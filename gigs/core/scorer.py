"""
Score each gig 0-100 against the user's profile + pay thresholds.

Scoring has two layers:

1. **Title layer** — what the role actually IS, not what the ad says.
   `_rules.TITLE_ENGINEERING_PATTERNS` matches AI-engineering job-title shapes
   (forward deployed, applied ai, ai engineer, agent builder, etc.) and
   `_rules.TITLE_NEGATIVES` matches DevOps / Marketing / Sales / PM / Intern shapes.
   When a title hits a negative without an engineering rescue, the role is
   hard-capped at `_rules.TITLE_NEGATIVE_CAP` so the description layer can't push
   it past the threshold on incidental keyword matches.

2. **Full-text layer** — `_rules.SKILL_WEIGHTS` adds capped contribution from the
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

from jobpilot.core.relocation_signals import detect_relocation_offer
from jobpilot.core.work_style import is_contract_friendly, is_w2_only, score_work_style
from jobpilot.gigs.core import preferences
from jobpilot.gigs.core import scoring_rules as _rules
from jobpilot.gigs.core.models import Gig


def _pay_floor_hourly() -> float:
    pay = preferences.pay()
    floor_hourly = float(pay.get("floor_hourly_usd", 65))
    floor_annual = float(pay.get("floor_annual_usd", 130000))
    return max(floor_hourly, floor_annual / 2000)


# Static USD conversion (no network). Rough mid-rates — enough to keep a
# sub-floor foreign salary (e.g. CAD 125K ≈ USD 91K) from passing the USD floor.
_USD_PER = {
    "USD": 1.0,
    "CAD": 0.73,
    "AUD": 0.66,
    "NZD": 0.61,
    "EUR": 1.08,
    "GBP": 1.27,
    "SGD": 0.74,
}


def _to_usd(amount: float, currency: str) -> float:
    return amount * _USD_PER.get((currency or "USD").upper(), 1.0)


def _normalize_pay(gig: Gig) -> float:
    """Reduce everything to a single USD hourly-equivalent for sorting and the
    pay floor — converting from the gig's currency first so a CAD/GBP band
    isn't compared against the USD floor at face value."""
    cur = gig.currency
    if gig.salary_max and gig.salary_max > 0:
        return _to_usd(gig.salary_max, cur) / 2000  # annual → hourly @ 2000 hrs
    if gig.salary_min and gig.salary_min > 0:
        return _to_usd(gig.salary_min, cur) / 2000
    if gig.pay_hourly_est:
        return _to_usd(gig.pay_hourly_est, cur)
    return 0.0


def _posted_age_days(posted_at: str) -> int | None:
    """Best-effort age in days from a posted_at string (ISO or RSS/email date).
    None if absent/unparseable — callers treat that as neutral, not newest."""
    if not posted_at:
        return None
    from datetime import datetime

    dt = None
    try:
        dt = datetime.fromisoformat(posted_at.replace("Z", "+00:00"))
    except ValueError:
        try:
            from email.utils import parsedate_to_datetime

            dt = parsedate_to_datetime(posted_at)
        except (TypeError, ValueError):
            dt = None
    if dt is None:
        return None
    now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
    return max(0, (now - dt).days)


def _freshness_bucket(gig: Gig) -> int:
    """2 = posted within a week, 1 = within two weeks, 0 = older/unknown.
    A coarse tiebreaker so today's roles edge out month-old HN comments at
    equal fit, without letting recency override fit."""
    age = _posted_age_days(getattr(gig, "posted_at", ""))
    if age is None:
        return 0
    if age <= 7:
        return 2
    if age <= 14:
        return 1
    return 0


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
    if (
        "boards.greenhouse.io" in apply
        or "jobs.lever.co" in apply
        or "ashbyhq.com" in apply
    ):
        return 3  # ATS form: autofill + upload + screening Qs + CAPTCHA
    if "google.com/search" in apply:
        return 5  # Google search → find result → land on careers → form
    if (
        "weworkremotely.com" in apply
        or "himalayas.app" in apply
        or "himalayas.co" in apply
    ):
        return 6  # paywalled / aggregator detail page
    if "remoteok.com" in apply or "remoteok.io" in apply:
        return 5  # aggregator → off-site apply
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


_REMOTE_TOKENS = ("remote", "anywhere", "distributed", "worldwide", "global")
# Strong "you can work from anywhere" signals — not soft "remote interviews".
_FULLY_REMOTE_RE = re.compile(
    r"\b("
    r"fully\s+remote|remote[- ]first|work\s+from\s+anywhere|"
    r"remote\s+us|remote,?\s+us|us[- ]remote|remote\s+within\s+the\s+us|"
    r"distributed\s+(?:team|company)|remote\s+worldwide"
    r")\b",
    re.IGNORECASE,
)
# A location requirement naming one of these is compatible with a US/home-metro
# applicant ("must live in the US"); anything else ("must live in Canada")
# means out of reach. Word-boundary so "us" matches the US but not Russia.
_HOME_OK_RE = re.compile(
    r"\b(u\.?s\.?a?|united states|north america|remote|anywhere|worldwide|texas|tx)\b",
    re.IGNORECASE,
)
# High-precision restriction phrasing. We only EXCLUDE on an explicit hard
# requirement, never on an incidental city mention, to avoid dropping good
# remote roles.
_RESTRICTION_RE = re.compile(
    r"must\s+(?:live|reside|be\s+located|be\s+based|work)\s+(?:in|from|near|within)?\s*([a-z .,/&'-]{2,40})",
    re.IGNORECASE,
)
_UNKNOWN_LOC = (
    "",
    "see post",
    "not specified",
    "n/a",
    "unspecified",
    "remote",
    "worldwide",
)

# Non-home US metros commonly used as location pins. Matched on title +
# location + URL only (not full description — HQ noise). An NYC/SF pin must
# not be rescued by a soft "remote" / "distributed" mention in the body.
_OTHER_US_METRO_RE = re.compile(
    r"\b("
    r"new\s*york(?:\s*city)?|\bnyc\b|brooklyn|manhattan|queens|"
    r"san\s*francisco|sf\s*bay|bay\s*area|"
    r"los\s*angeles|"
    r"seattle|chicago|boston|denver|miami|atlanta|"
    r"washington\s*d\.?c\.?|arlington,?\s*va|"
    r"philadelphia|phoenix|portland|san\s*diego|"
    r"jersey\s*city|hoboken"
    r")\b",
    re.IGNORECASE,
)

# Region locks visible in title / URL / location even when the feed hardcodes
# "Remote". Intentionally NOT full-description: body text often names HQ
# cities without restricting applicants. Catches shapes like
# "Founding AI Engineer (India)", "… – Remote (UK)", slug "...-india-2896…".
_REGION_LOCK_RES = (
    re.compile(
        r"\((?:india|uk|u\.k\.|emea|apac|eu|europe|canada|australia|"
        r"germany|singapore|latam|mexico|brazil|philippines|poland|"
        r"portugal|spain|france|netherlands)\)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:india|uk|u\.k\.|emea|apac|eu|europe|canada|australia|"
        r"germany|singapore)[- ](?:only|based)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:only|based)\s+in\s+(?:india|the\s+uk|united\s+kingdom|"
        r"canada|australia|germany|singapore|europe)\b",
        re.IGNORECASE,
    ),
    # Title/slug trailing market pin: "… Engineer - India", "…/india", "…-india-"
    re.compile(
        r"(?:^|[\s/_\-–—])(?:india|uk|emea|apac)(?:$|[\s/_\-–—])",
        re.IGNORECASE,
    ),
)


def _pin_blob(gig: Gig) -> str:
    """Title + location + URLs — where market pins usually live."""
    return " ".join(
        [
            gig.title or "",
            gig.location or "",
            gig.url or "",
            gig.apply_url or "",
        ]
    ).lower()


def _is_home_blob(s: str, home_tags: list[str]) -> bool:
    return any(t in s for t in home_tags)


def _other_us_metro_pinned(gig: Gig, *, home_tags: list[str]) -> bool:
    """True when title/location/URL pins a non-home US metro (e.g. NYC, SF)."""
    blob = _pin_blob(gig)
    if _is_home_blob(blob, home_tags):
        return False
    return bool(_OTHER_US_METRO_RE.search(blob))


def _region_locked_away(gig: Gig, *, home_tags: list[str]) -> bool:
    """True when title/URL/location pins the role to a non-home market.

    Used only for high-precision markers (parenthetical markets, *-only /
    *-based, slug/title market pins). Does not scan the full description.
    """
    blob = _pin_blob(gig)
    if _is_home_blob(blob, home_tags):
        return False
    # Explicit US / home-compatible framing in the same fields wins only when
    # there is no competing non-home metro pin (e.g. "Remote US" ok;
    # "NYC — US" still NYC-pinned).
    if _OTHER_US_METRO_RE.search(blob):
        return True
    if re.search(r"\b(u\.?s\.?a?|united states|north america|texas|tx)\b", blob):
        return False
    return any(rx.search(blob) for rx in _REGION_LOCK_RES)


def _location_is_remote(loc: str) -> bool:
    loc = (loc or "").lower().strip()
    if not loc or loc in _UNKNOWN_LOC:
        return False
    if _OTHER_US_METRO_RE.search(loc):
        return False  # "Remote - NYC" / "New York (Remote)" still metro-pinned
    return any(t in loc for t in _REMOTE_TOKENS) or bool(_FULLY_REMOTE_RE.search(loc))


def _geo_eligible(gig: Gig, *, home_tags: list[str], allow_remote: bool) -> bool:
    """True when a gig is reachable for home-metro or *fully* remote roles.

    Non-home metro pins (NYC, SF, …) in title/location/URL are excluded even
    if the description casually says "remote" or "distributed". Soft remote
    keywords no longer rescue a city-pinned role.
    """
    loc = (gig.location or "").lower().strip()
    pin = _pin_blob(gig)
    text = " ".join(
        [gig.location or "", gig.title or "", gig.description or ""]
    ).lower()

    # Home metro in title/location wins.
    if _is_home_blob(pin, home_tags):
        return True

    # Non-home US metro pin (NYC job when home is Austin) — hard no.
    if _other_us_metro_pinned(gig, home_tags=home_tags):
        return False

    if _region_locked_away(gig, home_tags=home_tags):
        return False

    # Explicit hard location requirement → judge by the region it names.
    for m in _RESTRICTION_RE.finditer(text):
        region = m.group(1)
        if _is_home_blob(region, home_tags) or _HOME_OK_RE.search(region):
            # "must live in NYC" is not home-ok for Austin even if "remote" elsewhere
            return not (
                _OTHER_US_METRO_RE.search(region)
                and not _is_home_blob(region, home_tags)
            )
        return False  # requirement names somewhere else → out of reach

    # Fully remote (location field or strong phrasing) — not soft body keywords.
    if allow_remote:
        if _location_is_remote(loc):
            return True
        if _FULLY_REMOTE_RE.search(text) and not _OTHER_US_METRO_RE.search(pin):
            return True
        # Bare location "Remote" with no metro pin
        if (
            loc in ("remote", "worldwide", "global", "anywhere")
            or loc.startswith("remote ")
        ) and not _OTHER_US_METRO_RE.search(pin):
            return True

    # A specific, non-home, non-remote location → onsite elsewhere.
    return loc in _UNKNOWN_LOC or any(
        re.search(rf"\b{re.escape(tok)}\b", loc) for tok in ("united states", "usa")
    )


def _seniority_drag(title: str) -> tuple[int, str | None]:
    """Strongest seniority penalty for this title, or (0, None).

    Word-boundary checks so 'leadership' / 'seniority' don't false-trigger;
    'sr' requires a following space/dot so it doesn't match inside words.
    """
    best_w = 0
    best_label: str | None = None
    # Ordered strongest-first so ties prefer the clearer label.
    checks: list[tuple[str, int, re.Pattern[str]]] = [
        (
            "staff",
            _rules.TITLE_SENIORITY_DRAG.get("staff", -18),
            re.compile(r"\bstaff\b"),
        ),
        (
            "principal",
            _rules.TITLE_SENIORITY_DRAG.get("principal", -16),
            re.compile(r"\bprincipal\b"),
        ),
        (
            "senior",
            _rules.TITLE_SENIORITY_DRAG.get("senior", -12),
            re.compile(r"\bsenior\b"),
        ),
        ("sr", _rules.TITLE_SENIORITY_DRAG.get("sr ", -12), re.compile(r"\bsr\.?\b")),
        (
            "lead",
            _rules.TITLE_SENIORITY_DRAG.get("lead", -10),
            re.compile(r"\b(?:tech\s+)?lead\b"),
        ),
    ]
    for label, weight, rx in checks:
        if rx.search(title) and weight < best_w:
            best_w = weight
            best_label = label
    return best_w, best_label


def score_gig(gig: Gig) -> Gig:
    """Mutate gig in place: set fit_score (0-100) and fit_reasons."""
    title = (gig.title or "").lower()
    description = (gig.description or "").lower()
    full_text = " ".join(
        [
            title,
            description,
            " ".join(gig.tags or []),
            (gig.company or "").lower(),
        ]
    )

    score = 30  # baseline for existing-at-all
    reasons: list[str] = []

    # ----- Title layer -----
    # Overlapping phrases ("ai automation engineer" ⊃ "ai automation" ⊃
    # "automation engineer") must not stack — one title scoring +64 across
    # three nested patterns is how everything saturated at 97-100. Only the
    # longest match scores (weight breaks length ties); every match still
    # counts as a rescue from _rules.TITLE_NEGATIVES and the job-board penalty.
    title_eng_hits = [
        phrase
        for phrase in _rules.TITLE_ENGINEERING_PATTERNS
        if _phrase_in(title, phrase)
    ]
    if title_eng_hits:
        best = max(
            title_eng_hits,
            key=lambda p: (len(p), _rules.TITLE_ENGINEERING_PATTERNS[p]),
        )
        w = _rules.TITLE_ENGINEERING_PATTERNS[best]
        score += w
        reasons.append(f"+{w} title:{best}")

    for phrase, w in _rules.TITLE_TECH_BONUS.items():
        if _phrase_in(title, phrase):
            score += w
            reasons.append(f"+{w} title:{phrase}")

    title_neg_hits: list[str] = []
    for phrase, w in _rules.TITLE_NEGATIVES.items():
        if _phrase_in(title, phrase):
            score += w
            title_neg_hits.append(phrase)
            reasons.append(f"{w} title:{phrase}")

    # If the title is DevOps/Marketing/Sales/PM-shaped and nothing in
    # _rules.TITLE_ENGINEERING_PATTERNS matched, the role is off-track regardless
    # of how many AI buzzwords appear in the description.
    title_capped = bool(title_neg_hits) and not title_eng_hits

    # ----- Full-text skill layer (capped) -----
    skill_total = 0
    for kw, w in _rules.SKILL_WEIGHTS.items():
        if _has_keyword(full_text, kw):
            skill_total += w
            reasons.append(f"+{w} {kw}")
    if skill_total > _rules.SKILL_WEIGHTS_CAP:
        reasons.append(
            f"cap-{_rules.SKILL_WEIGHTS_CAP} skill bonus capped (raw {skill_total})"
        )
        skill_total = _rules.SKILL_WEIGHTS_CAP
    score += skill_total

    # ----- Spam + description-level negatives -----
    for kw, w in _rules.SCAM_SIGNALS.items():
        if kw in full_text:
            score += w
            reasons.append(f"{w} {kw}")

    for kw, w in _rules.NEGATIVE_TERMS.items():
        if _has_keyword(full_text, kw):
            score += w
            reasons.append(f"{w} {kw}")

    # ----- Domain (company) bonus -----
    for kw, w in _rules.DOMAIN_BONUS.items():
        if _has_keyword(full_text, kw):
            score += w
            reasons.append(f"+{w} {kw}")

    # ----- Source bonus + revenue / strong-fit phrase -----
    if "upwork" in gig.source.lower():
        score += 25
        reasons.append("+25 saved Upwork lead")

    if any(_has_keyword(full_text, term) for term in _rules.REVENUE_TERMS):
        score += 8
        reasons.append("+8 revenue-term fit")

    # Strong-fit bonus only when the title layer did NOT already score an
    # engineering pattern — otherwise "AI Engineer" double-counts
    # (+28 title + +15 strong-fit) and everything piles at 100.
    if not title_eng_hits and any(
        _phrase_in(title, term) for term in _rules.STRONG_FIT_TERMS
    ):
        score += 15
        reasons.append("+15 strong-fit phrase in title")

    # ----- Seniority drag (always; even with engineering rescue) -----
    drag, drag_label = _seniority_drag(title)
    if drag:
        score += drag
        reasons.append(f"{drag} seniority:{drag_label}")

    # ----- Generic-job-board penalty -----
    # WWR/RemoteOK/Himalayas/HN postings without a title-level engineering
    # signal get hit hard — most of them match an incidental "automation"
    # or "ai" in the description.
    if (
        gig.source in _rules.JOB_BOARD_SOURCES
        and not title_eng_hits
        and not any(_phrase_in(title, term) for term in _rules.STRONG_FIT_TERMS)
    ):
        score -= 25
        reasons.append("-25 generic job-board role (no AI title signal)")

    # ----- Pay (bonus only — never a hard block or score penalty) -----
    # Unstated / low posted pay must not hide a great role. High stated pay
    # still gets a small ranking bump; the user judges rate after triage.
    hourly_eq = _normalize_pay(gig)
    if hourly_eq >= 125:
        score += 15
        reasons.append(f"+15 pay ${hourly_eq:.0f}/hr")
    elif hourly_eq >= 75:
        score += 10
        reasons.append(f"+10 pay ${hourly_eq:.0f}/hr")
    elif hourly_eq >= 50:
        score += 5
        reasons.append(f"+5 pay ${hourly_eq:.0f}/hr")

    # ----- Employer-offered relocation (mobility help on the table) -----
    relocation_offer = detect_relocation_offer(full_text)
    if relocation_offer:
        score += 10
        reasons.append(f"+10 relocation offered ({relocation_offer})")

    # ----- Work style (autonomy / contract / anti-9-5) -----
    ws_delta, ws_reasons = score_work_style(full_text, title=gig.title or "")
    if ws_delta:
        score += ws_delta
        reasons.extend(ws_reasons[:4])

    # ----- Title-cap -----
    if title_capped:
        if score > _rules.TITLE_NEGATIVE_CAP:
            reasons.append(
                f"cap-{_rules.TITLE_NEGATIVE_CAP} title is non-engineering (no AI rescue)"
            )
        score = min(score, _rules.TITLE_NEGATIVE_CAP)

    gig.fit_score = max(0, min(100, score))
    gig.fit_reasons = reasons[:8]
    return gig


def _pay_parse_is_confident(gig: Gig) -> bool:
    """True when the comp came from an explicit salary range or hourly figure.

    Kept for crib/salary-anchor helpers and tests. Pay is not used as a hard
    rank filter (low/unstated pay must not hide strong roles).
    """
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

    Pay is never a hard filter: low or missing stated pay still passes so
    great-fit roles are not hidden. High stated pay only helps as a soft
    sort/score bonus.

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

    scored = [score_gig(g) for g in gigs]
    kept = [g for g in scored if g.fit_score >= min_score]
    if contract_first:
        kept = [
            g
            for g in kept
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
            g
            for g in kept
            if not is_schedule_rigid(
                " ".join([g.description or "", g.title or ""]),
                title=g.title or "",
            )
        ]
    # Geo-eligibility: opt-in (preferences location.require_home_or_remote) and
    # only when home_metro_tags are set, so the shipped default stays neutral.
    loc_cfg = preferences.location_config()
    home_tags = [t.lower() for t in loc_cfg.get("home_metro_tags", [])]
    if loc_cfg.get("require_home_or_remote", False) and home_tags:
        allow_remote = loc_cfg.get("allow_remote", True)
        kept = [
            g
            for g in kept
            if _geo_eligible(g, home_tags=home_tags, allow_remote=allow_remote)
        ]
    kept.sort(
        key=lambda g: (
            -g.fit_score,
            -_freshness_bucket(g),  # fresher first when fit ties (don't outweigh fit)
            apply_friction(g),
            -_source_priority(g),
            -_normalize_pay(g),
        )
    )
    return kept[:top_n]
