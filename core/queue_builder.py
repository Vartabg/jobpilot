"""Build the verified, evidence-linked company slate read by JobPilot UIs.

Legacy scoring helpers remain temporarily importable for compatibility, but
queue recommendations and sorting use direct-source legitimacy plus separate
full-description decision axes.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import sqlite3
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from jobpilot.core.application_evidence import (
    ApplicationEvidenceIndex,
    configured_external_evidence_errors,
)
from jobpilot.core.application_tracker import get_application_tracker
from jobpilot.core.config import DATA_DIR
from jobpilot.core.legitimacy import assess_legitimacy
from jobpilot.core.logger import get_logger
from jobpilot.core.opportunity_ledger import OpportunityLedger
from jobpilot.core.opportunity_models import DIRECT_ATS_PROVIDERS, SourceObservation
from jobpilot.core.policy_config import get_policy, reset_policy_cache
from jobpilot.core.portal_scanner import PortalJob, PortalScanner, ScanTarget
from jobpilot.core.profile_store import get_profile_store
from jobpilot.core.role_decision import RoleDecisionEngine
from jobpilot.core.role_identity import identify_role, is_safe_public_role_url
from jobpilot.core.source_health import summarize_source_runs
from jobpilot.core.work_style import title_seniority_penalty

log = get_logger(__name__)

QUEUE_PATH = DATA_DIR / "queue.json"
TRUE_ACCOUNTS_PATH = DATA_DIR / "true_accounts.json"

# Queue mutations can arrive concurrently from the HTTP server, terminal UI,
# and CLI. The in-process lock orders threads while flock orders independent
# JobPilot processes that share the same queue file. Nested queue helpers use
# the same transaction without trying to acquire a second process lock.
_QUEUE_THREAD_LOCK = threading.RLock()
_QUEUE_TRANSACTION_STATE = threading.local()


@contextmanager
def _queue_transaction() -> Iterator[None]:
    """Serialize a queue transaction across threads and local processes."""
    with _QUEUE_THREAD_LOCK:
        depth = getattr(_QUEUE_TRANSACTION_STATE, "depth", 0)
        if depth == 0:
            lock_path = QUEUE_PATH.with_suffix(f"{QUEUE_PATH.suffix}.lock")
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
            except BaseException:
                os.close(lock_fd)
                raise
            _QUEUE_TRANSACTION_STATE.lock_fd = lock_fd
        _QUEUE_TRANSACTION_STATE.depth = depth + 1
        try:
            yield
        finally:
            remaining = _QUEUE_TRANSACTION_STATE.depth - 1
            _QUEUE_TRANSACTION_STATE.depth = remaining
            if remaining == 0:
                lock_fd = _QUEUE_TRANSACTION_STATE.lock_fd
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                finally:
                    os.close(lock_fd)
                    del _QUEUE_TRANSACTION_STATE.lock_fd


TECH_KEYWORDS = [
    "engineer", "software", "developer", "python", "automation",
    "technical", "platform", "backend", "fullstack", "full-stack",
    "infrastructure", "devops", "sre", "reliability", "systems",
    "data", "ml", "ai", "forward deployed", "solutions",
    "deployment", "implementation", "integration", "customer success",
    "solutions consultant", "visualization", "webgl", "three.js", "3d web",
]

FIELD_OPS_KEYWORDS = [
    "facilities", "field", "technician", "operations", "maintenance",
    "data center", "datacenter", "critical", "electro", "mechanical",
    "building", "bms", "scada", "hvac", "ups", "power", "network ops",
    "it operations", "site", "infrastructure", "service engineer",
]

ALL_KEYWORDS = list(dict.fromkeys(TECH_KEYWORDS + FIELD_OPS_KEYWORDS))


def _discovery_keywords(profile) -> list[str]:
    """Include the candidate's explicit target titles in source discovery."""
    targets = [
        " ".join(str(title).lower().split())
        for title in getattr(profile, "target_titles", [])
        if str(title).strip()
    ]
    return list(dict.fromkeys([*ALL_KEYWORDS, *targets]))


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
    fit_score: int       # 0-100 compatibility average of known evidence axes
    keywords: list[str]
    status: str = "queued"   # queued | applied | skipped
    queued_at: str = ""
    # Kept only so existing queue.json files load. New decisions never sort or
    # recommend from this legacy title-keyword proxy.
    psyche_score: int = 0
    suppression_reason: str = ""
    decision: str = "investigate"
    assessment_status: str = "unscorable"
    role_family: str = "unknown"
    qualification_lower_bound: int | None = None
    work_context_match: int | None = None
    opportunity_score: int | None = None
    logistics_score: int | None = None
    evidence_coverage: int = 0
    biggest_gap: str = ""
    matched_accounts: list[str] = field(default_factory=list)
    decision_rationale: str = ""
    legitimacy_state: str = "hold"
    evidence_grade: str = "F"
    legitimacy_reasons: list[str] = field(default_factory=list)
    verified_at: str = ""
    posting_state: str = "unknown"
    posting_age_days: int | None = None
    provider_job_id: str = ""

    def __post_init__(self):
        if not self.queued_at:
            self.queued_at = datetime.now(UTC).isoformat()


VERIFICATION_TTL = timedelta(hours=24)


def _utc_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _verification_is_current(job: QueueJob, *, now: datetime | None = None) -> bool:
    verified = _utc_datetime(job.verified_at)
    if verified is None:
        return False
    current = (now or datetime.now(UTC)).astimezone(UTC)
    age = current - verified
    return timedelta(0) <= age <= VERIFICATION_TTL


def is_apply_ready(job: QueueJob, *, now: datetime | None = None) -> bool:
    """Return true only for a current, assessed, direct-source recommendation."""
    return (
        job.status in {"queued", "viewing"}
        and job.assessment_status == "assessed"
        and job.decision == "apply_now"
        and job.legitimacy_state == "recommend"
        and is_safe_public_role_url(job.url)
        and _verification_is_current(job, now=now)
    )


def _evidence_identity(
    url: str,
    provider: str = "",
    tenant: str = "",
    provider_job_id: str = "",
):
    """Resolve identity only when the URL and declared fields agree."""
    inferred = identify_role(url)
    declared_provider = str(provider or "").strip().lower()
    declared_tenant = str(tenant or "").strip().lower()
    declared_job_id = str(provider_job_id or "").strip()
    if inferred.provider and declared_provider and inferred.provider != declared_provider:
        return None
    if inferred.tenant and declared_tenant and inferred.tenant != declared_tenant:
        return None
    if (
        inferred.provider_job_id
        and declared_job_id
        and inferred.provider_job_id != declared_job_id
    ):
        return None
    return identify_role(
        url,
        declared_provider or inferred.provider,
        declared_tenant or inferred.tenant,
        declared_job_id or inferred.provider_job_id,
    )


def _same_evidence_role(expected, item: dict[str, Any]) -> bool:
    observed = _evidence_identity(
        str(item.get("canonical_url") or ""),
        str(item.get("provider") or ""),
        str(item.get("tenant") or ""),
        str(item.get("provider_job_id") or ""),
    )
    if observed is None:
        return False
    if expected.canonical_url and observed.canonical_url == expected.canonical_url:
        return True
    return bool(
        expected.provider_job_id
        and observed.provider_job_id
        and observed.key == expected.key
    )


def _timestamp_is_current(
    value: str,
    *,
    now: datetime,
) -> tuple[bool, datetime | None]:
    stamp = _utc_datetime(value)
    if stamp is None:
        return False, None
    age = now - stamp
    return timedelta(0) <= age <= VERIFICATION_TTL, stamp


def has_current_action_provenance(
    job: QueueJob,
    *,
    now: datetime | None = None,
    data_dir: Path | None = None,
    ledger_path: Path | None = None,
) -> bool:
    """Corroborate an apply action against the existing evidence ledger.

    This deliberately does not create, repair, upsert, or reassess anything.
    Missing, malformed, ambiguous, stale, or contradictory evidence therefore
    keeps action-time behavior closed even when queue.json says ``apply_now``.
    """
    instant = (now or datetime.now(UTC)).astimezone(UTC)
    if configured_application_history_errors(now=instant):
        log.warning(
            "Action provenance check failed closed because configured "
            "application history is incomplete."
        )
        return False
    expected = _evidence_identity(job.url, job.portal, provider_job_id=job.provider_job_id)
    if expected is None or not expected.canonical_url:
        return False

    ledger: OpportunityLedger | None = None
    try:
        ledger = OpportunityLedger.open_readonly(
            data_dir=data_dir or DATA_DIR,
            db_path=ledger_path,
        )
        matches = [
            row
            for row in ledger.iter_opportunities()
            if _same_evidence_role(expected, row)
            or (
                expected.provider_job_id
                and str(row.get("identity_key") or "") == expected.key
            )
        ]
        if len(matches) != 1:
            return False
        opportunity_id = str(matches[0]["id"])

        direct_observations: list[SourceObservation] = []
        for payload in ledger.iter_observations(opportunity_id):
            observation = SourceObservation(**payload)
            if (
                observation.provider not in DIRECT_ATS_PROVIDERS
                or not observation.is_direct
            ):
                continue
            # A direct-source row attached to this opportunity but identifying
            # another role means the ledger itself is inconsistent.
            if not _same_evidence_role(expected, payload):
                return False
            direct_observations.append(observation)
        if not direct_observations:
            return False

        latest_observation = direct_observations[-1]
        observation_current, observed_at = _timestamp_is_current(
            latest_observation.fetched_at,
            now=instant,
        )
        if (
            not observation_current
            or observed_at is None
            or latest_observation.listing_state
            not in {"active", "listed", "open", "published"}
        ):
            return False

        legitimacy = ledger.latest_assessment(opportunity_id, "legitimacy")
        role_decision = ledger.latest_assessment(opportunity_id, "role_decision")
        if legitimacy is None or role_decision is None:
            return False
        legitimacy_current, legitimacy_at = _timestamp_is_current(
            str(legitimacy["assessed_at"]),
            now=instant,
        )
        decision_current, decision_at = _timestamp_is_current(
            str(role_decision["assessed_at"]),
            now=instant,
        )
        if (
            not legitimacy_current
            or not decision_current
            or legitimacy_at is None
            or decision_at is None
            or legitimacy_at < observed_at
            or decision_at < observed_at
        ):
            return False

        legitimacy_result = legitimacy["result"]
        decision_result = role_decision["result"]
        verified_current, verified_at = _timestamp_is_current(
            str(legitimacy_result.get("verified_at") or ""),
            now=instant,
        )
        return bool(
            verified_current
            and verified_at is not None
            and verified_at >= observed_at
            and str(legitimacy_result.get("state") or "").lower() == "recommend"
            and str(decision_result.get("status") or "").lower() == "assessed"
            and str(decision_result.get("decision") or "").lower() == "apply_now"
        )
    except (
        FileNotFoundError,
        KeyError,
        OSError,
        TypeError,
        ValueError,
        sqlite3.Error,
    ) as exc:
        log.warning("Action provenance check failed closed: %s", exc)
        return False
    finally:
        if ledger is not None:
            ledger.close()


def expire_stale_recommendations(
    jobs: list[QueueJob],
    *,
    now: datetime | None = None,
) -> int:
    """Downgrade cached recommendations when direct-source proof is stale."""
    changed = 0
    for job in jobs:
        if (
            job.decision == "apply_now"
            and job.legitimacy_state == "recommend"
            and not _verification_is_current(job, now=now)
        ):
            job.decision = "investigate"
            job.legitimacy_state = "hold"
            job.suppression_reason = (
                "Verification is older than 24 hours. Refresh the source before applying."
            )
            changed += 1
    return changed


def reset_caches() -> None:
    """Drop the policy cache used by queue gates and compatibility scoring."""
    reset_policy_cache()


def __getattr__(name: str):
    # Backwards-compat: MOAT_COMPANY_TAGS used to be a module-level constant;
    # it now lives in the user's policy (data/policy.json, empty by default).
    if name == "MOAT_COMPANY_TAGS":
        return get_policy().queue.moat_company_tags
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# --- Legacy Lane-C scoring formula. Queue construction no longer calls this;
#     it remains importable while older callers migrate to evidence decisions.
#
#     Evidence-independent weights (sum = 85):
#       Vertical moat (25) · Tier-fit (20) · Function coherence (18)
#       · Skill overlap (12) · Logistics (10)
#
#     Hard gates zero the score: title kill-keywords and out-of-list
#     locations, both configured in data/policy.json (see
#     docs/policy.example.json). With the shipped defaults nothing is
#     gated: no kill-keywords, location gate off.

LANE_A_FUNCTION_HITS = (
    "forward deployed", "fde", "solutions engineer", "sales engineer",
    "customer engineer", "field engineer", "field service",
    "implementation engineer", "deployment engineer",
)
FOUNDING_HITS = ("founding",)

# Company HQ fallback — factual company → headquarters mapping. When the ATS
# API returns no location on a role, substitute the company's HQ so the
# location gate (if enabled in policy.json) still has something to bite on.
# Extend or override per-user via `queue.company_hq` in data/policy.json.
COMPANY_HQ = {
    "happyrobot":        "San Francisco",
    "soff":              "San Francisco",
    "bretton ai":        "San Francisco",
    "paratus health":    "Menlo Park",
    "promise":           "Oakland",
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
    "titan ai":          "Remote",
    "scaled cognition":  "New York",
    "p-1 ai":            "Remote",
}


def _normalize_location_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _has_location_hit(normalized_haystack: str, terms: tuple[str, ...]) -> bool:
    for term in terms:
        normalized_term = _normalize_location_text(term)
        if not normalized_term:
            continue
        pieces = [re.escape(piece) for piece in normalized_term.split()]
        pattern = r"\b" + r"\s+".join(pieces) + r"\b"
        if re.search(pattern, normalized_haystack):
            return True
    return False


def _company_hq(company: str) -> str:
    key = company.strip().lower()
    policy_hq = get_policy().queue.company_hq
    if key in policy_hq:
        return policy_hq[key]
    return COMPANY_HQ.get(key, "")


def _effective_location(company: str, location: str | None) -> str:
    loc = (location or "").strip()
    if loc and loc.lower() != "not specified":
        return loc
    return _company_hq(company)


def _is_allowed_location(title: str, company: str, location: str | None) -> bool:
    """Gate a job on its location, per `queue.location_gate` in policy.json.

    The gate is off by default — every location passes. When enabled, only
    the location fields (role location, falling back to company HQ) can
    QUALIFY a job. The title is never trusted as a positive location signal
    — a title like "Help us build…" must not pass a US gate via the word
    "us". Blocked terms found in the title (e.g. "— German Speaking",
    "| Europe/LATAM") still DISQUALIFY, since that is a conservative signal.
    """
    gate = get_policy().queue.location_gate
    if not gate.enabled:
        return True

    title_haystack = _normalize_location_text(title or "")
    if title_haystack and _has_location_hit(title_haystack, gate.blocked_locations):
        return False

    loc_haystack = _normalize_location_text(_effective_location(company, location))

    # Field-service titles: surface anywhere in the US regardless of city
    # (Garo judges travel-vs-relocation per role, 2026-07-17 pivot). Home-metro
    # narrowing is bypassed for these; a clearly-international pin with no US
    # country signal is still rejected so foreign roles don't leak in.
    title_lower = (title or "").lower()
    if gate.bypass_title_keywords and any(
        kw in title_lower for kw in gate.bypass_title_keywords
    ):
        if not loc_haystack:
            return True
        has_country = _has_location_hit(loc_haystack, gate.country_terms)
        return has_country or not _has_location_hit(
            loc_haystack, gate.blocked_locations
        )

    if not loc_haystack:
        return False

    if _has_location_hit(loc_haystack, gate.blocked_locations):
        return False
    has_country = _has_location_hit(loc_haystack, gate.country_terms)
    if not has_country and _has_location_hit(loc_haystack, gate.blocked_without_country):
        return False

    return (
        has_country
        or _has_location_hit(loc_haystack, gate.remote_terms)
        or _has_location_hit(loc_haystack, gate.allowed_locations)
    )


def _evidence_path(value: str) -> Path | None:
    if not value:
        return None
    path = Path(value).expanduser()
    repo_root = Path(__file__).resolve().parent.parent
    return path if path.is_absolute() else repo_root / path


def get_application_evidence_index(tracker=None) -> ApplicationEvidenceIndex:
    """Merge every configured source that can prove a role was already touched."""
    policy = get_policy().application_evidence
    return ApplicationEvidenceIndex.build(
        tracker=tracker or get_application_tracker(),
        employment_dir=_evidence_path(policy.employment_dir),
        gmail_cache_path=_evidence_path(policy.gmail_cache_path),
        gmail_cache_max_age_hours=policy.gmail_cache_max_age_hours,
    )


def configured_application_history_errors(
    *,
    now: datetime | None = None,
) -> tuple[str, ...]:
    """Check external history freshness without touching tracker or ledger state."""
    policy = get_policy().application_evidence
    if not policy.fail_closed:
        return ()
    return configured_external_evidence_errors(
        employment_dir=_evidence_path(policy.employment_dir),
        gmail_cache_path=_evidence_path(policy.gmail_cache_path),
        gmail_cache_max_age_hours=policy.gmail_cache_max_age_hours,
        now=now,
    )


def restrict_queue_for_incomplete_history(
    jobs: list[QueueJob],
    *,
    now: datetime | None = None,
) -> int:
    """Downgrade positive cached decisions for a read-only presentation."""
    if not configured_application_history_errors(now=now):
        return 0
    changed = 0
    for job in jobs:
        if (
            job.status in {"queued", "viewing"}
            and job.decision in {"apply_now", "stretch"}
        ):
            job.decision = "investigate"
            job.suppression_reason = _INCOMPLETE_HISTORY_REASON
            changed += 1
    return changed


def restrict_queue_for_action_provenance(
    jobs: list[QueueJob],
    *,
    now: datetime | None = None,
) -> int:
    """Hide cached recommendations that the live action gate would reject."""
    changed = 0
    for job in jobs:
        if is_apply_ready(job, now=now) and not has_current_action_provenance(
            job,
            now=now,
        ):
            job.decision = "investigate"
            job.suppression_reason = _MISSING_ACTION_PROVENANCE_REASON
            changed += 1
    return changed


def policy_block_reason(company: str, title: str) -> str | None:
    """Return the personal-policy reason a role must not enter the active queue."""
    policy = get_policy()
    company_l = (company or "").strip().lower()
    title_l = (title or "").strip().lower()

    if company_l in policy.scoring.refused_companies:
        return policy.scoring.refused_companies[company_l] or "company refused by policy"
    for keyword, reason in policy.scoring.refused_title_keywords.items():
        if keyword in title_l:
            return reason or f"title contains refused term: {keyword}"
    for keyword in policy.queue.title_kill_keywords:
        if keyword in title_l:
            return f"title blocked by policy: {keyword}"
    allowed_titles = policy.queue.title_allow_keywords
    if allowed_titles and not any(keyword in title_l for keyword in allowed_titles):
        return "outside configured role lane"

    transport = policy.queue.transport_gate
    if transport.enabled and transport.mode == "transit":
        if any(term in title_l for term in transport.fixed_site_title_keywords):
            return None
        for keyword in transport.mobile_title_keywords:
            if keyword in title_l:
                return f"routine driving likely required: {keyword}"
    return None


def _score_job(job: PortalJob) -> tuple[int, str, int]:
    """Legacy Lane-C score with a neutral compatibility value in slot three.

    This helper never reads personal preference files. New queue rows use the
    full-description decision engine instead, but older imports can continue
    unpacking the original three-tuple while the final value remains zero.
    """
    policy = get_policy().queue
    title_l = job.title.lower()
    company_l = job.company.strip().lower()

    # Hard gate: configured title kill-keywords (e.g. management-ladder
    # titles the user never wants queued). Empty by default.
    if any(k in title_l for k in policy.title_kill_keywords):
        return 5, "tech", 0

    # Hard gate: configured location gate (no-op unless enabled in policy).
    if not _is_allowed_location(job.title, job.company, job.location):
        return 0, "tech", 0

    industry = policy.moat_company_tags.get(company_l)
    # --- Function coherence (0-18)
    if any(k in title_l for k in LANE_A_FUNCTION_HITS):
        func = 18 if any(k in title_l for k in FOUNDING_HITS) else 16
    elif "founding" in title_l and ("engineer" in title_l or "developer" in title_l):
        func = 10
    elif "engineer" in title_l or "developer" in title_l:
        func = 5
    else:
        func = 0

    # --- Vertical moat (0-25) — industries where the user has lived
    # experience, tagged per company in policy.json (no JD fetch at scan time).
    if industry in policy.high_moat_industries:
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
    if "ai" in title_l or "agent" in title_l:
        skill += 5
    if "full" in title_l or "stack" in title_l:
        skill += 3
    if "deploy" in title_l or "customer" in title_l or "field" in title_l:
        skill += 4
    skill = min(skill, 12)

    # --- Logistics fit (0-10) — already hard-gated above.
    loc = 10
    if industry in policy.excluded_industries:
        loc = min(loc, 2)

    score = moat + tier + func + skill + loc

    deprioritize = policy.title_deprioritize_keywords or (
        "senior engineer", "senior software", "senior ai", "senior full",
    )
    senior_pen, _ = title_seniority_penalty(job.title, deprioritize)
    score += senior_pen

    if industry in policy.high_moat_industries or any(k in title_l for k in
            ("field", "deployed", "customer engineer", "service engineer")):
        track = "both"
    else:
        track = "tech"

    return max(0, min(100, score)), track, 0


def _axis_or_zero(value: int | None) -> int:
    return int(value) if value is not None else 0


def _decision_fit_score(decision) -> int:
    """Compatibility score derived only from separately reported axes."""
    axes = [
        decision.qualification_lower_bound,
        decision.work_context_match,
        decision.opportunity,
        decision.logistics,
    ]
    known = [int(value) for value in axes if value is not None]
    return round(sum(known) / len(known)) if known else 0


def _assessment_payload(decision) -> dict[str, Any]:
    return {
        "status": decision.status,
        "decision": decision.decision,
        "role_family": decision.role_family,
        "qualification_lower_bound": decision.qualification_lower_bound,
        "work_context_match": decision.work_context_match,
        "opportunity": decision.opportunity,
        "logistics": decision.logistics,
        "evidence_coverage": decision.evidence_coverage,
        "biggest_gap": decision.biggest_gap,
        "matched_accounts": list(decision.matched_accounts),
        "rationale": decision.rationale,
    }


_INCOMPLETE_HISTORY_REASON = (
    "Application history is incomplete. Refresh the configured history source "
    "before applying."
)
_MISSING_ACTION_PROVENANCE_REASON = (
    "Current ledger corroboration is unavailable. Refresh the queue before "
    "treating this role as ready to apply."
)


def _gate_decision_for_incomplete_history(decision, *, incomplete: bool):
    """Restrict positive recommendations when deduplication evidence is incomplete."""
    if not incomplete or decision.decision not in {"apply_now", "stretch"}:
        return decision
    return replace(
        decision,
        decision="investigate",
        rationale=f"{decision.rationale} {_INCOMPLETE_HISTORY_REASON}",
    )


def _queue_job_from_evidence(
    job: PortalJob,
    *,
    prior_job: QueueJob | None,
    status: str,
    decision,
    legitimacy,
    application_history_incomplete: bool = False,
) -> QueueJob:
    title_l = job.title.lower()
    policy = get_policy().queue
    industry = policy.moat_company_tags.get(job.company.strip().lower())
    track = (
        "both"
        if industry in policy.high_moat_industries
        or any(
            token in title_l
            for token in (
                "field",
                "deployed",
                "customer engineer",
                "service engineer",
            )
        )
        else "tech"
    )
    evidence_score = _decision_fit_score(decision)
    queue_decision = decision.decision
    suppression_reason = (
        prior_job.suppression_reason
        if prior_job is not None and status == "skipped"
        else ""
    )
    effective_status = status
    if legitimacy.state == "block" and status in {"queued", "viewing"}:
        queue_decision = "skip"
        effective_status = "skipped"
        suppression_reason = "legitimacy gate blocked this posting"
    elif legitimacy.state != "recommend" and queue_decision == "apply_now":
        queue_decision = "investigate"
        suppression_reason = "source evidence requires investigation"
    elif queue_decision == "skip" and status in {"queued", "viewing"}:
        effective_status = "skipped"
        suppression_reason = decision.biggest_gap or "role evidence did not pass"
    elif application_history_incomplete and status in {"queued", "viewing"}:
        suppression_reason = _INCOMPLETE_HISTORY_REASON
    return QueueJob(
        id=hashlib.md5(job.identity_key.encode()).hexdigest()[:8],
        company=job.company,
        title=job.title,
        url=job.canonical_url or job.url,
        location=job.location or "Not specified",
        portal=job.portal,
        track=track,
        fit_score=evidence_score,
        keywords=job.matched_keywords[:5],
        status=effective_status,
        queued_at=prior_job.queued_at if prior_job else "",
        psyche_score=0,
        suppression_reason=suppression_reason,
        decision=queue_decision,
        assessment_status=decision.status,
        role_family=decision.role_family,
        qualification_lower_bound=decision.qualification_lower_bound,
        work_context_match=decision.work_context_match,
        opportunity_score=decision.opportunity,
        logistics_score=decision.logistics,
        evidence_coverage=decision.evidence_coverage,
        biggest_gap=decision.biggest_gap,
        matched_accounts=list(decision.matched_accounts),
        decision_rationale=decision.rationale,
        legitimacy_state=legitimacy.state,
        evidence_grade=legitimacy.grade,
        legitimacy_reasons=list(legitimacy.reasons),
        verified_at=legitimacy.verified_at,
        posting_state=job.listing_state,
        provider_job_id=job.provider_job_id,
    )


def build_queue(
    extra_targets: list[ScanTarget] | None = None,
    limit: int = 50,
) -> list[QueueJob]:
    """Scan all portals, score jobs, return sorted queue."""
    targets = PortalScanner.load_targets()
    if extra_targets:
        targets.extend(extra_targets)

    if not targets:
        log.warning("No portal targets found — check data/portals.json")
        return []

    profile = get_profile_store().load()
    log.info("Scanning %d portal targets...", len(targets))
    scanner = PortalScanner(keywords=_discovery_keywords(profile))
    raw_jobs = scanner.scan_targets(targets)
    log.info("Found %d raw matches across all portals", len(raw_jobs))
    source_health = summarize_source_runs(scanner.last_run_results)
    log.info(source_health.summary_line)
    for notice in source_health.notices:
        log.warning("Source attention: %s", notice)

    # Load prior queue to preserve applied/skipped status
    prior = {j.id: j for j in load_queue()}
    prior_by_url = {j.url.strip(): j for j in prior.values() if j.url.strip()}
    # Authoritative dedup source: applications.db (URL + company-level).
    # Catches backfilled history that prior queue.json won't have.
    tracker = get_application_tracker()
    evidence = get_application_evidence_index(tracker)
    decision_engine = RoleDecisionEngine()
    accounts_path = TRUE_ACCOUNTS_PATH if TRUE_ACCOUNTS_PATH.is_file() else None
    application_history_incomplete = bool(
        get_policy().application_evidence.fail_closed
        and getattr(evidence, "errors", ())
    )
    if application_history_incomplete:
        log.warning(
            "Application history is incomplete. Search will continue, but every "
            "otherwise-ready role is restricted to Investigate. Refresh the "
            "configured history source before applying."
        )
    ledger = OpportunityLedger()

    # Fold the existing tracker/Gmail/application-packet history into the
    # canonical ledger before adding this scan. The import is idempotent, so a
    # refresh cannot duplicate funnel events.
    evidence.import_into(ledger)

    run_ids: dict[tuple[str, str], str] = {}
    try:
        for run in scanner.last_run_results:
            run_ids[(run.provider, run.target)] = ledger.record_source_run(
                source=run.provider,
                target=run.target,
                status=run.status,
                result_count=run.result_count,
                fetched_at=run.fetched_at,
                error=run.error,
                payload={"label": run.label, "error_type": run.error_type},
            )
    except Exception:
        ledger.close()
        raise

    queue: list[QueueJob] = []
    try:
        for job in raw_jobs:
            if not is_safe_public_role_url(job.canonical_url or job.url) or not job.title:
                continue
            block_reason = policy_block_reason(job.company, job.title)
            if block_reason or not _is_allowed_location(
                job.title, job.company, job.location
            ):
                continue

            observation = job.to_source_observation()
            decision = decision_engine.assess(
                title=job.title,
                jd_text=job.description,
                profile=profile,
                accounts_path=accounts_path,
            )
            decision = _gate_decision_for_incomplete_history(
                decision,
                incomplete=application_history_incomplete,
            )
            job_id = hashlib.md5(job.identity_key.encode()).hexdigest()[:8]
            prior_job = prior.get(job_id) or prior_by_url.get(
                (job.canonical_url or job.url).strip()
            )
            tracker_status = tracker_status_for_job(
                tracker, job.canonical_url or job.url, job.company
            )
            evidence_match = evidence.match(
                job.company, job.title, job.canonical_url or job.url
            )
            if (
                prior_job
                and prior_job.status == "skipped"
                and not tracker.has_applied(job.canonical_url or job.url)
                and not prior_job.suppression_reason.startswith("alternate role at company:")
            ):
                status = "skipped"
            else:
                status = (
                    tracker_status
                    or (evidence_match.status if evidence_match else None)
                    or (
                        prior_job.status
                        if prior_job and prior_job.status != "skipped"
                        else "queued"
                    )
                )

            opportunity_id = ledger.upsert_opportunity(
                job.company,
                job.title,
                canonical_url=job.canonical_url or job.url,
                provider=job.portal,
                provider_job_id=job.provider_job_id,
                identity_key=job.identity_key,
            )
            target_key = (job.portal, job.provider_tenant)
            ledger.record_observation(
                opportunity_id,
                observation,
                run_ids.get(target_key, ""),
            )
            observed_history = [
                SourceObservation(**item)
                for item in ledger.iter_observations(opportunity_id)
            ]
            legitimacy = assess_legitimacy(observed_history)
            ledger.record_assessment(
                opportunity_id,
                "legitimacy",
                legitimacy,
                assessed_at=legitimacy.evaluated_at,
                version="1",
                source="deterministic",
            )
            ledger.record_assessment(
                opportunity_id,
                "role_decision",
                _assessment_payload(decision),
                version="1",
                source="candidate_evidence",
            )
            ledger.append_event(
                opportunity_id,
                "discovered",
                job.fetched_at,
                job.portal,
                {
                    "provider_job_id": job.provider_job_id,
                    "lane": decision.role_family,
                },
            )
            if legitimacy.state == "recommend":
                ledger.append_event(
                    opportunity_id,
                    "verified",
                    job.fetched_at,
                    job.portal,
                    {
                        "grade": legitimacy.grade,
                        "lane": decision.role_family,
                    },
                )
            queue.append(_queue_job_from_evidence(
                job,
                prior_job=prior_job,
                status=status,
                decision=decision,
                legitimacy=legitimacy,
                application_history_incomplete=application_history_incomplete,
            ))
    finally:
        ledger.close()

    # Carry history forward when a role falls out of a scan. Active rows remain
    # visible for investigation but lose apply-ready status until direct-source
    # evidence returns; a source outage must never silently erase or recommend
    # the cached opportunity.
    seen_ids = {j.id for j in queue}
    for pj in prior.values():
        if pj.id in seen_ids or not _is_allowed_location(
            pj.title, pj.company, pj.location
        ):
            continue
        if pj.status in {"queued", "viewing"}:
            pj.status = "queued"
            pj.decision = "investigate"
            pj.assessment_status = "unscorable"
            pj.legitimacy_state = "hold"
            pj.evidence_grade = "E"
            pj.verified_at = ""
            pj.posting_state = "unverified"
            pj.biggest_gap = "Current direct-source verification is unavailable."
            pj.suppression_reason = (
                "Role was not observed in the latest scan. Investigate or refresh."
            )
            pj.legitimacy_reasons = ["not_observed_in_latest_scan"]
            queue.append(pj)
        elif pj.status in {"applied", "skipped"}:
            queue.append(pj)

    expire_stale_recommendations(queue)
    apply_company_slate(queue)
    queue.sort(
        key=lambda j: (
            j.status not in {"queued", "viewing"},
            j.decision != "apply_now",
            -_axis_or_zero(j.qualification_lower_bound),
            -int(j.evidence_coverage or 0),
        )
    )
    return queue[:limit]


def _fsync_directory(path: Path) -> None:
    """Best-effort durability for the directory entry replaced below."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        directory_fd = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(directory_fd)
    except OSError:
        # Some filesystems do not support syncing a directory descriptor. The
        # queue file itself has already been flushed and atomically replaced.
        pass
    finally:
        os.close(directory_fd)


def _save_queue_unlocked(jobs: list[QueueJob]) -> Path:
    """Atomically replace queue.json; caller must hold ``_queue_transaction``."""
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = [asdict(job) for job in jobs]
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=QUEUE_PATH.parent,
            prefix=f".{QUEUE_PATH.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            json.dump(data, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, QUEUE_PATH)
        _fsync_directory(QUEUE_PATH.parent)
    except BaseException:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise
    log.info("Saved %d jobs to %s", len(jobs), QUEUE_PATH)
    return QUEUE_PATH


def save_queue(jobs: list[QueueJob]) -> Path:
    """Atomically write the canonical queue under the shared queue lock."""
    with _queue_transaction():
        return _save_queue_unlocked(jobs)


def tracker_status_for_job(tracker, url: str, company: str) -> str | None:
    """Return status only when evidence identifies this exact role URL.

    A rejection belongs to one opportunity. It must not suppress a sibling
    role merely because both roles share a company name.
    """
    if tracker.has_applied(url):
        return tracker.get_status(url) or "applied"
    return None


def _queue_row_error(item: object) -> str | None:
    """Return a concise structural error for one persisted queue row."""
    if not isinstance(item, dict):
        return f"expected an object, got {type(item).__name__}"
    required = {
        "id": str,
        "company": str,
        "title": str,
        "url": str,
        "location": str,
        "portal": str,
        "track": str,
        "fit_score": int,
        "keywords": list,
    }
    for name, expected_type in required.items():
        if name not in item:
            return f"missing required field {name!r}"
        value = item[name]
        if not isinstance(value, expected_type) or (
            expected_type is int and isinstance(value, bool)
        ):
            return f"field {name!r} has invalid type {type(value).__name__}"

    string_fields = {
        "status",
        "queued_at",
        "suppression_reason",
        "decision",
        "assessment_status",
        "role_family",
        "biggest_gap",
        "decision_rationale",
        "legitimacy_state",
        "evidence_grade",
        "verified_at",
        "posting_state",
        "provider_job_id",
    }
    integer_fields = {"psyche_score", "evidence_coverage"}
    optional_integer_fields = {
        "qualification_lower_bound",
        "work_context_match",
        "opportunity_score",
        "logistics_score",
        "posting_age_days",
    }
    list_fields = {"keywords", "matched_accounts", "legitimacy_reasons"}
    for name in string_fields:
        if name in item and not isinstance(item[name], str):
            return f"field {name!r} has invalid type {type(item[name]).__name__}"
    for name in integer_fields:
        if name in item and (
            not isinstance(item[name], int) or isinstance(item[name], bool)
        ):
            return f"field {name!r} has invalid type {type(item[name]).__name__}"
    for name in optional_integer_fields:
        value = item.get(name)
        if value is not None and (
            not isinstance(value, int) or isinstance(value, bool)
        ):
            return f"field {name!r} has invalid type {type(value).__name__}"
    for name in list_fields:
        if name in item and (
            not isinstance(item[name], list)
            or any(not isinstance(value, str) for value in item[name])
        ):
            return f"field {name!r} must be a list of strings"
    return None


def _load_queue_unlocked() -> list[QueueJob]:
    """Load and validate one atomically published queue snapshot."""
    if not QUEUE_PATH.exists():
        return []
    try:
        raw = QUEUE_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        log.warning("Could not read queue %s: %s", QUEUE_PATH, exc)
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        log.warning("Could not parse queue %s: %s", QUEUE_PATH, exc)
        return []
    if not isinstance(data, list):
        log.warning(
            "Could not load queue %s: top-level JSON must be a list, got %s",
            QUEUE_PATH,
            type(data).__name__,
        )
        return []

    known = QueueJob.__dataclass_fields__
    jobs: list[QueueJob] = []
    for index, item in enumerate(data):
        error = _queue_row_error(item)
        if error:
            log.warning("Skipping malformed queue row %d: %s", index, error)
            continue
        try:
            values = {
                key: value for key, value in item.items() if key in known
            }
            jobs.append(QueueJob(**values))
        except (TypeError, ValueError) as exc:
            log.warning("Skipping malformed queue row %d: %s", index, exc)
    return jobs


def load_queue() -> list[QueueJob]:
    """Load a complete snapshot without blocking on a long source refresh."""
    return _load_queue_unlocked()


def refresh_queue(
    extra_targets: list[ScanTarget] | None = None,
    limit: int = 50,
) -> list[QueueJob]:
    """Build and persist one refresh as a serialized queue transaction."""
    with _queue_transaction():
        jobs = build_queue(extra_targets=extra_targets, limit=limit)
        _save_queue_unlocked(jobs)
        return jobs


def load_current_slate() -> list[QueueJob]:
    """Reconcile tracker evidence and freshness before any recommendation view."""
    reconcile_queue_with_tracker()
    jobs = load_queue()
    restrict_queue_for_action_provenance(jobs)
    return jobs


def update_job_status(job_id: str, status: str) -> bool:
    """Update a single job's status in the queue."""
    with _queue_transaction():
        queue = _load_queue_unlocked()
        for job in queue:
            if job.id == job_id:
                job.status = status
                _save_queue_unlocked(queue)
                return True
        return False


def reconcile_queue_with_tracker() -> tuple[int, int]:
    """Apply live policy and cross-source evidence states to queue.json."""
    with _queue_transaction():
        queue = _load_queue_unlocked()
        if not queue:
            return 0, 0
        tracker = get_application_tracker()
        evidence = get_application_evidence_index(tracker)
        application_history_incomplete = bool(
            get_policy().application_evidence.fail_closed
            and getattr(evidence, "errors", ())
        )
        changed = expire_stale_recommendations(queue)
        for job in queue:
            if (
                application_history_incomplete
                and job.status in {"queued", "viewing"}
                and job.decision in {"apply_now", "stretch"}
            ):
                job.decision = "investigate"
                job.suppression_reason = _INCOMPLETE_HISTORY_REASON
                changed += 1
            block_reason = policy_block_reason(job.company, job.title)
            if block_reason and job.status in {"queued", "viewing"}:
                job.status = "skipped"
                job.suppression_reason = block_reason
                changed += 1
                continue
            if job.status == "skipped" and not tracker.has_applied(job.url):
                continue
            tracker_status = tracker_status_for_job(
                tracker, job.url, job.company
            )
            evidence_match = evidence.match(job.company, job.title, job.url)
            evidence_status = tracker_status or (
                evidence_match.status if evidence_match else None
            )
            if evidence_status and job.status != evidence_status:
                job.status = evidence_status
                job.suppression_reason = (
                    f"application evidence: {evidence_match.source}"
                    if evidence_match else "application evidence: tracker"
                )
                changed += 1
        slate_changed, _companies = apply_company_slate(queue)
        changed += slate_changed
        if changed:
            _save_queue_unlocked(queue)
        return changed, len(queue)


def apply_company_slate(queue: list[QueueJob]) -> tuple[int, int]:
    """Keep the strongest evidence-backed active role for each company."""
    active_statuses = {"queued", "viewing"}
    groups: dict[str, list[QueueJob]] = {}
    for job in queue:
        if job.status not in active_statuses:
            continue
        key = (job.company or "").strip().lower()
        if not key:
            continue
        groups.setdefault(key, []).append(job)

    changed = 0
    focused_companies = 0
    status_rank = {"viewing": 1, "queued": 0}
    decision_rank = {
        "apply_now": 4,
        "stretch": 3,
        "investigate": 2,
        "skip": 1,
    }
    legitimacy_rank = {"recommend": 4, "review": 3, "hold": 2, "block": 1}

    for jobs in groups.values():
        if len(jobs) <= 1:
            continue
        focused_companies += 1
        jobs.sort(
            key=lambda j: (
                decision_rank.get(j.decision, 0),
                legitimacy_rank.get(j.legitimacy_state, 0),
                int(j.qualification_lower_bound or 0),
                int(j.evidence_coverage or 0),
                int(j.work_context_match or 0),
                status_rank.get(j.status, 0),
            ),
            reverse=True,
        )
        winner = jobs[0]
        for duplicate in jobs[1:]:
            if duplicate.status != "skipped":
                duplicate.status = "skipped"
                duplicate.suppression_reason = (
                    f"alternate role at company: {winner.title}"
                )
                changed += 1

    return changed, focused_companies


def focus_queue_company_first() -> tuple[int, int, int]:
    """Keep one active role per company and skip duplicate active roles."""
    with _queue_transaction():
        queue = _load_queue_unlocked()
        if not queue:
            return 0, 0, 0

        changed, focused_companies = apply_company_slate(queue)

        if changed:
            _save_queue_unlocked(queue)
        return changed, len(queue), focused_companies


def get_job(job_id: str) -> QueueJob | None:
    """Get a single job after reconciling cached state with role evidence."""
    for job in load_current_slate():
        if job.id == job_id:
            return job
    return None
