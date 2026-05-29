"""
Queue Builder — scan ATS boards, score jobs, save ranked queue.

Writes data/queue.json which the dashboard reads.

Scoring formula (Lane-C, 100-point total):
    Vertical moat (25) · Tier-fit (20) · Function coherence (18)
    · Skill overlap (12) · Logistics (10) · Psycho-fit (15)

Psycho-fit scores the user's work-style alignment from a configurable profile
(`data/psyche_profile.json`) — fork it to your own preferences. The aim is
that JobPilot surfaces roles that fit how you actually work, not just what
keywords match.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from jobpilot.core.application_tracker import get_application_tracker
from jobpilot.core.config import DATA_DIR
from jobpilot.core.logger import get_logger
from jobpilot.core.portal_scanner import PortalJob, PortalScanner, ScanTarget

PSYCHE_PROFILE_PATH = DATA_DIR / "psyche_profile.json"
PORTALS_PATH = DATA_DIR / "portals.json"

log = get_logger(__name__)

QUEUE_PATH = DATA_DIR / "queue.json"

TECH_KEYWORDS = [
    "engineer", "software", "developer", "python", "automation",
    "technical", "platform", "backend", "fullstack", "full-stack",
    "infrastructure", "devops", "sre", "reliability", "systems",
    "data", "ml", "ai", "forward deployed", "solutions",
]

FIELD_OPS_KEYWORDS = [
    "facilities", "field", "technician", "operations", "maintenance",
    "data center", "datacenter", "critical", "electro", "mechanical",
    "building", "bms", "scada", "hvac", "ups", "power", "network ops",
    "it operations", "site", "infrastructure", "service engineer",
]

ALL_KEYWORDS = list(dict.fromkeys(TECH_KEYWORDS + FIELD_OPS_KEYWORDS))


@dataclass
class QueueJob:
    """A scored, queued job ready to apply to."""
    id: str
    company: str
    title: str
    url: str
    location: str
    portal: str
    track: str           # "tech" | "field_ops" | "both"
    fit_score: int       # 0-100 (sum of all dimensions)
    keywords: list[str]
    status: str = "queued"   # queued | applied | skipped
    queued_at: str = ""
    psyche_score: int = 0    # 0-15 sub-score (work-style fit, exposed for transparency)

    def __post_init__(self):
        if not self.queued_at:
            self.queued_at = datetime.now().isoformat()


# Loaded once per queue build — profiles, _notes — keep at module level.
_psyche_profile_cache: Optional[dict[str, Any]] = None
_portal_notes_cache: Optional[dict[str, str]] = None


def _load_psyche_profile() -> dict[str, Any]:
    """Read user's work-style profile. Falls back to an empty-but-safe shape."""
    global _psyche_profile_cache
    if _psyche_profile_cache is not None:
        return _psyche_profile_cache
    try:
        _psyche_profile_cache = json.loads(PSYCHE_PROFILE_PATH.read_text())
    except Exception as exc:
        log.warning("Could not load psyche_profile.json (%s) — psycho-fit will be neutral.", exc)
        _psyche_profile_cache = {"loved_signals": {}, "hated_signals": {}}
    return _psyche_profile_cache


def _load_portal_notes() -> dict[str, str]:
    """Pull each portal target's `_note` text keyed by lowercased label.

    The `_note` field captures human-readable context (stage, size, flags) per
    company. Used by psycho-fit to score against company_or_note signals when
    we don't have JD text at scan time.
    """
    global _portal_notes_cache
    if _portal_notes_cache is not None:
        return _portal_notes_cache
    notes: dict[str, str] = {}
    try:
        raw = json.loads(PORTALS_PATH.read_text())
        for item in raw:
            label = str(item.get("label", "")).strip().lower()
            note = str(item.get("_note", "")).strip().lower()
            if label and note:
                notes[label] = note
    except Exception as exc:
        log.warning("Could not load portals.json _notes (%s)", exc)
    _portal_notes_cache = notes
    return notes


def reset_caches() -> None:
    """Drop module-level caches so subsequent builds re-read profile + notes."""
    global _psyche_profile_cache, _portal_notes_cache
    _psyche_profile_cache = None
    _portal_notes_cache = None


# --- Lane-C scoring formula (memory pins: user_work_values_anticorporate,
#     user_location, feedback_anthropic_conversion_rate). Replaces the old
#     keyword-density scorer that floored everything at 30 — that floor was
#     masking real mis-fit.
#
#     Weights (sum = 100):
#       Vertical moat (30) · Tier-fit / inverse prestige (25)
#       Function coherence (20) · Skill overlap (15) · Logistics fit (10)
#
#     Hard gates zero the score: senior-bureaucrat ladder titles,
#     out-of-list locations, mega-cap / public prestige companies.

LANE_A_FUNCTION_HITS = (
    "forward deployed", "fde", "solutions engineer", "sales engineer",
    "customer engineer", "field engineer", "field service",
    "implementation engineer", "deployment engineer",
)
FOUNDING_HITS = ("founding",)
SENIOR_BUREAUCRAT_KILLS = (
    "senior manager", "principal", "staff engineer",
    "director", "vp ", "head of", "vice president",
    "manager,", "manager -",
)
# Lived/moat industries.
# Tag at scan time since we don't fetch JD text. Add entries when curating
# portals.json. Industries in HIGH_MOAT below get full vertical-moat points.
MOAT_COMPANY_TAGS = {
    "haast":            "regtech",
    "starbridge":       "govtech",
    "promise":          "govtech_payments",
    "clarion health":   "healthcare_ops",
    "avallon ai":       "insurance_ops",
    "bretton ai":       "fintech_compliance",   # tagged but excluded below
    "decagon":          "ai_cx",
    "pylon":            "ai_support",
    "credal":           "ai_dev_tools",
    "ravenna":          "ai_helpdesk",
    "soff":             "manufacturing",         # very high moat
    "happyrobot":       "logistics_ai",
    "dataland":         "data_ai",
    "airweave":         "ai_dev_tools",
    "extend":           "ai_workflow",
    "fern":             "ai_dev_tools",
    "giga":             "ai",
    "collectwise":      "ai_collections",
    "corvera ai":       "ai",
    "zymbly":           "aviation_maintenance",  # very high moat
    "crustdata":        "ai_data",
    "paratus health":   "healthcare_voice",
    "arist":            "enablement",
    "signal messenger": "infra_messaging",
    "conduktor":        "infra_kafka",
    "growthbook":       "feature_flags",
}
HIGH_MOAT_INDUSTRIES = {
    "aviation_maintenance", "manufacturing", "healthcare_ops",
    "healthcare_voice", "govtech", "govtech_payments", "regtech",
    "logistics_ai",
}
# Exclusions per PROFILE.md / feedback_anthropic_conversion_rate
EXCLUDED_INDUSTRIES = {"fintech_compliance"}

# user_location pin: NYC base; remote always; in-person only in these cities.
WIN_LOCATION_HITS = (
    "new york", "nyc", "remote", "us remote", "remote, us",
    "austin", "portland", "seattle", "denver",
)
KILL_LOCATION_HITS = (
    "san francisco", "bay area", "menlo park", "oakland", "berkeley",
    "berlin", "london", "atlanta", "boston", "washington", "dc,",
    "remote, eu", "remote, europe",
)

# Company HQ fallback — when the ATS API returns no location on a role,
# substitute the company's HQ so the logistics gate still bites. Without
# this, SF-based companies (e.g. HappyRobot) inflated scores because
# "Not specified" was treated as neutral. Per [user_location] pin.
COMPANY_HQ = {
    "happyrobot":        "San Francisco",
    "soff":              "San Francisco",
    "bretton ai":        "San Francisco",
    "paratus health":    "Menlo Park",
    "promise":           "Oakland",        # also DC; both fail
    "decagon":           "San Francisco",
    "zymbly":            "London",
    "n8n":               "Berlin",
    "credal":            "New York",
    "starbridge":        "New York",
    "haast":             "Remote",
    "clarion health":    "New York",
    "avallon ai":        "New York",
    "pylon":             "New York",
    "ravenna":           "Remote",
    "signal messenger":  "Remote",
}


def _score_psyche_fit(
    title_l: str, company_l: str, industry: Optional[str], note_l: str,
    profile: dict[str, Any],
) -> int:
    """Work-style fit (0-15) — title + company-note + industry vs psyche_profile.json.

    Title hits are weighted heavier than company/note hits since title is the
    most direct signal of role shape. Industry hits are flat ±2.
    """
    loved = profile.get("loved_signals", {}) or {}
    hated = profile.get("hated_signals", {}) or {}

    score = 7  # neutral baseline so unknown companies aren't penalized

    # Title-level signals (most direct)
    title_loved = sum(1 for tok in (loved.get("title") or []) if tok and tok in title_l)
    title_hated = sum(1 for tok in (hated.get("title") or []) if tok and tok in title_l)
    score += min(title_loved * 2, 6)
    score -= min(title_hated * 2, 8)

    # Company name + portals.json _note text (stage / size / flags)
    company_haystack = f"{company_l} {note_l}"
    company_loved = sum(1 for tok in (loved.get("company_or_note") or []) if tok and tok in company_haystack)
    company_hated = sum(1 for tok in (hated.get("company_or_note") or []) if tok and tok in company_haystack)
    score += min(company_loved, 3)
    score -= min(company_hated * 2, 4)

    # Industry classification (flat bonus / penalty)
    if industry:
        if industry in (loved.get("industry") or []):
            score += 2
        if industry in (hated.get("industry") or []):
            score -= 2

    return max(0, min(15, score))


def _score_job(job: PortalJob) -> tuple[int, str, int]:
    """Lane-C weighted score (0-100). Returns (score, track, psyche_score).

    Weights: Moat 25 · Tier 20 · Function 18 · Skill 12 · Logistics 10 · Psyche 15.
    """
    title_l = job.title.lower()
    company_l = job.company.strip().lower()
    location_l = job.location.lower() if job.location else ""

    # Hard gate: senior-bureaucrat ladder titles (Sr Manager / Director / VP / etc.)
    # Garo's value: "answers to few people" — these roles fail that by design.
    if any(k in title_l for k in SENIOR_BUREAUCRAT_KILLS):
        return 5, "tech", 0

    industry = MOAT_COMPANY_TAGS.get(company_l)
    note_l = _load_portal_notes().get(company_l, "")
    profile = _load_psyche_profile()

    # --- Function coherence (0-18)
    if any(k in title_l for k in LANE_A_FUNCTION_HITS):
        func = 18 if any(k in title_l for k in FOUNDING_HITS) else 16
    elif "founding" in title_l and ("engineer" in title_l or "developer" in title_l):
        func = 10
    elif "engineer" in title_l or "developer" in title_l:
        func = 5
    else:
        func = 0

    # --- Vertical moat (0-25)
    if industry in HIGH_MOAT_INDUSTRIES:
        moat = 25
    elif industry:
        moat = 15
    else:
        moat = 6

    # --- Tier fit (0-20). portals.json is curated early-stage so default high;
    # penalize enterprise/Fortune-500 motion hints in titles.
    tier = 18
    if any(k in title_l for k in ("enterprise", "majors", "fortune", "sr staff", "senior staff")):
        tier = 6

    # --- Skill overlap (0-12) — title-only proxy (no JD fetch at scan time).
    skill = 0
    if "ai" in title_l or "agent" in title_l: skill += 5
    if "full" in title_l or "stack" in title_l: skill += 3
    if "deploy" in title_l or "customer" in title_l or "field" in title_l: skill += 4
    skill = min(skill, 12)

    # --- Logistics fit (0-10) — role location first, then company HQ fallback
    effective_loc = location_l
    if not effective_loc or effective_loc == "not specified":
        hq = COMPANY_HQ.get(company_l, "").lower()
        if hq:
            effective_loc = hq
    if any(k in effective_loc for k in WIN_LOCATION_HITS):
        loc = 10
    elif any(k in effective_loc for k in KILL_LOCATION_HITS):
        loc = 0
    elif not effective_loc:
        loc = 5
    else:
        loc = 4
    if industry in EXCLUDED_INDUSTRIES:
        loc = min(loc, 2)

    # --- Psycho-fit (0-15) — work-style alignment via psyche_profile.json
    psyche = _score_psyche_fit(title_l, company_l, industry, note_l, profile)

    score = moat + tier + func + skill + loc + psyche

    if industry in HIGH_MOAT_INDUSTRIES or any(k in title_l for k in
            ("field", "deployed", "customer engineer", "service engineer")):
        track = "both"
    else:
        track = "tech"

    return max(0, min(100, score)), track, psyche


def build_queue(
    extra_targets: Optional[list[ScanTarget]] = None,
    limit: int = 50,
) -> list[QueueJob]:
    """Scan all portals, score jobs, return sorted queue."""
    targets = PortalScanner.load_targets()
    if extra_targets:
        targets.extend(extra_targets)

    if not targets:
        log.warning("No portal targets found — check data/portals.json")
        return []

    log.info("Scanning %d portal targets...", len(targets))
    scanner = PortalScanner(keywords=ALL_KEYWORDS)
    raw_jobs = scanner.scan_targets(targets)
    log.info("Found %d raw matches across all portals", len(raw_jobs))

    # Load prior queue to preserve applied/skipped status
    prior = {j.id: j for j in load_queue()}
    # Authoritative dedup source: applications.db (URL + company-level).
    # Catches backfilled history that prior queue.json won't have.
    tracker = get_application_tracker()

    queue: list[QueueJob] = []
    for job in raw_jobs:
        if not job.url or not job.title:
            continue
        score, track, psyche_score = _score_job(job)
        # Stable 8-char ID derived from the job URL so IDs survive --refresh
        job_id = hashlib.md5(job.url.encode()).hexdigest()[:8]
        prior_job = prior.get(job_id)
        # Tracker dedup — URL first, then company-level (catches Gmail-backfill
        # rows with synthetic URLs). Rejected wins over applied so dead ground
        # is visible in the queue.
        tracker_company_status = tracker.company_status(job.company)
        if tracker.has_applied(job.url):
            tracker_status = tracker.get_status(job.url) or "applied"
        elif tracker_company_status:
            tracker_status = tracker_company_status
        else:
            tracker_status = None
        # Status precedence: tracker > prior queue.json > default queued
        status = tracker_status or (prior_job.status if prior_job else "queued")
        queue.append(QueueJob(
            id=job_id,
            company=job.company,
            title=job.title,
            url=job.url,
            location=job.location or "Not specified",
            portal=job.portal,
            track=track,
            fit_score=score,
            keywords=job.matched_keywords[:5],
            status=status,
            queued_at=prior_job.queued_at if prior_job else "",
            psyche_score=psyche_score,
        ))

    # Also carry over applied/skipped jobs whose URL fell out of the scan
    # (e.g. listing got removed) so the user's history isn't lost.
    seen_ids = {j.id for j in queue}
    for pj in prior.values():
        if pj.id not in seen_ids and pj.status in ("applied", "skipped"):
            queue.append(pj)

    queue.sort(key=lambda j: j.fit_score, reverse=True)
    return queue[:limit]


def save_queue(jobs: list[QueueJob]) -> Path:
    """Write queue to disk (JSON + JS so file:// dashboards can load it)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    data = [asdict(j) for j in jobs]
    QUEUE_PATH.write_text(json.dumps(data, indent=2))
    js_path = DATA_DIR / "queue.js"
    js_path.write_text(f"window.JOBS = {json.dumps(data)};")
    log.info("Saved %d jobs to %s", len(jobs), QUEUE_PATH)
    return QUEUE_PATH


def load_queue() -> list[QueueJob]:
    """Load queue from disk."""
    if not QUEUE_PATH.exists():
        return []
    try:
        data = json.loads(QUEUE_PATH.read_text())
        return [QueueJob(**item) for item in data]
    except Exception as e:
        log.warning("Could not load queue: %s", e)
        return []


def update_job_status(job_id: str, status: str) -> bool:
    """Update a single job's status in the queue."""
    queue = load_queue()
    for job in queue:
        if job.id == job_id:
            job.status = status
            save_queue(queue)
            return True
    return False


def get_job(job_id: str) -> Optional[QueueJob]:
    """Get a single job by ID."""
    for job in load_queue():
        if job.id == job_id:
            return job
    return None
