"""Deterministic, evidence-linked role decisions with separate axes."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from jobpilot.core.profile_store import UserProfile
from jobpilot.core.requirement_matcher import (
    EvidenceStatus,
    RequirementMatch,
    RequirementMatcher,
)

_EXCLUDED = re.compile(
    r"\b(marketing|designer|product design|product manager|product management|"
    r"data scientist|data science|brand|content strategist)\b",
)
_FAMILIES = (
    (
        "field_service",
        re.compile(
            r"\b(field (?:service )?engineer|customer service engineer|"
            r"service (?:engineer|technician)|commissioning|installation technician|"
            r"data center technician|critical facilities technician|"
            r"facilities technician|access control technician)\b"
        ),
    ),
    (
        "technical_support",
        re.compile(
            r"\b(technical support|support engineer|escalation engineer|integration support)\b"
        ),
    ),
    (
        "technical_presales",
        re.compile(
            r"\b(sales engineer|pre-?sales engineer|solutions consultant|"
            r"technical consultant|demo engineer|technical account manager|"
            r"customer success (?:engineer|manager)|solutions architect)\b"
        ),
    ),
    (
        "creative_technology",
        re.compile(
            r"\b(creative developer|webgl|three\.?js|3d web|visualization (?:engineer|developer))\b"
        ),
    ),
    (
        "industrial_application",
        re.compile(
            r"\b(applications? engineer|industrial engineer|controls engineer)\b"
        ),
    ),
    (
        "applied_implementation",
        re.compile(
            r"\b(customer engineer|implementation engineer|solutions engineer|integration engineer|"
            r"deployment engineer|forward deployed engineer|deployment strategist|"
            r"implementation strategist|implementation consultant|"
            r"technical implementation specialist)\b"
        ),
    ),
)


@dataclass(frozen=True)
class RoleDecision:
    status: str
    decision: str
    role_family: str
    qualification_lower_bound: int | None
    work_context_match: int | None
    opportunity: int | None
    logistics: int | None
    evidence_coverage: int
    biggest_gap: str
    matched_accounts: tuple[str, ...]
    matches: tuple[RequirementMatch, ...]
    rationale: str


def classify_role_family(title: str) -> str:
    lowered = " ".join((title or "").lower().split())
    if not lowered or _EXCLUDED.search(lowered):
        return "unsupported"
    return next(
        (family for family, pattern in _FAMILIES if pattern.search(lowered)),
        "unsupported",
    )


def _axis(matches: list[RequirementMatch]) -> int | None:
    if not matches:
        return None
    points = {
        EvidenceStatus.DIRECT: 100,
        EvidenceStatus.ADJACENT: 50,
        EvidenceStatus.UNKNOWN: 0,
        EvidenceStatus.CONTRADICTED: 0,
    }
    return round(sum(points[item.status] for item in matches) / len(matches))


def _first_gap(matches: tuple[RequirementMatch, ...]) -> str:
    supported_text = {
        item.text
        for item in matches
        if item.status in {EvidenceStatus.DIRECT, EvidenceStatus.ADJACENT}
    }
    priorities = (
        ("mandatory", EvidenceStatus.CONTRADICTED),
        ("logistics", EvidenceStatus.CONTRADICTED),
        ("mandatory", EvidenceStatus.UNKNOWN),
        ("logistics", EvidenceStatus.UNKNOWN),
        ("preferred", EvidenceStatus.CONTRADICTED),
        ("work_context", EvidenceStatus.UNKNOWN),
        ("preferred", EvidenceStatus.UNKNOWN),
    )
    for category, status in priorities:
        found = next(
            (
                item
                for item in matches
                if item.category == category
                and item.status is status
                and item.text not in supported_text
            ),
            None,
        )
        if found:
            return found.text
    return ""


def _is_hard_unknown(item: RequirementMatch) -> bool:
    if item.status is not EvidenceStatus.UNKNOWN:
        return False
    if item.category == "mandatory":
        return True
    if item.category != "logistics":
        return False
    return bool(
        re.search(
            r"\b(must|required|license|travel|authoriz|sponsor|relocat|shift|"
            r"weekend|overnight|lift)\w*\b",
            item.text,
            re.IGNORECASE,
        )
    )


class RoleDecisionEngine:
    def assess(
        self,
        *,
        title: str,
        jd_text: str,
        profile: UserProfile,
        accounts_path: Path | None = None,
    ) -> RoleDecision:
        family = classify_role_family(title)
        explicit_targets = {
            " ".join(str(target).lower().split())
            for target in profile.target_titles
            if str(target).strip()
        }
        if (
            family == "unsupported"
            and " ".join(title.lower().split()) in explicit_targets
        ):
            family = "candidate_target"
        if family == "unsupported":
            return RoleDecision(
                "assessed",
                "skip",
                family,
                None,
                None,
                None,
                None,
                0,
                "Title is outside the supported role families.",
                (),
                (),
                "The title was rejected before evidence scoring.",
            )

        requirements = RequirementMatcher.parse(jd_text)
        if not requirements.is_sufficient:
            return RoleDecision(
                "unscorable",
                "investigate",
                family,
                None,
                None,
                None,
                None,
                0,
                "A full job description is required.",
                (),
                (),
                "Insufficient job-description evidence; no neutral points were awarded.",
            )

        matcher = RequirementMatcher.from_profile(profile, accounts_path)
        matches = matcher.match(requirements)
        mandatory = [item for item in matches if item.category == "mandatory"]
        context = [item for item in matches if item.category == "work_context"]
        opportunity_matches = [
            item
            for item in matches
            if item.category in {"preferred", "responsibilities"}
        ]
        logistics_matches = [item for item in matches if item.category == "logistics"]
        qualification = _axis(mandatory)
        work_context = _axis(context)
        opportunity = _axis(opportunity_matches)
        logistics = _axis(logistics_matches)
        known = sum(item.status is not EvidenceStatus.UNKNOWN for item in matches)
        coverage = round(100 * known / len(matches)) if matches else 0
        contradicted_gate = any(
            item.status is EvidenceStatus.CONTRADICTED
            and item.category in {"mandatory", "logistics"}
            for item in matches
        )
        unknown_hard_gate = any(_is_hard_unknown(item) for item in matches)
        if contradicted_gate:
            decision = "skip"
        elif unknown_hard_gate or qualification is None or coverage < 40:
            decision = "investigate"
        elif qualification >= 70 and coverage >= 55:
            decision = "apply_now"
        elif qualification >= 40:
            decision = "stretch"
        else:
            decision = "skip" if coverage >= 70 else "investigate"
        account_ids = tuple(
            dict.fromkeys(
                account_id
                for item in matches
                if item.status in {EvidenceStatus.DIRECT, EvidenceStatus.ADJACENT}
                for account_id in item.account_ids
            )
        )
        rationale = (
            f"Evidence-linked {family.replace('_', ' ')} assessment; "
            f"qualification lower bound {qualification if qualification is not None else 'unknown'}; "
            f"evidence coverage {coverage}%."
        )
        return RoleDecision(
            "assessed",
            decision,
            family,
            qualification,
            work_context,
            opportunity,
            logistics,
            coverage,
            _first_gap(matches),
            account_ids,
            matches,
            rationale,
        )
