"""Deterministic, posting-evidenced relocation screening."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from jobpilot.core.job_description import normalize_job_description


class RelocationState(StrEnum):
    OFFERED = "offered"
    CONDITIONAL = "conditional"
    REQUIRED_UNSUPPORTED = "required_unsupported"
    NOT_OFFERED = "not_offered"
    NOT_NEEDED = "not_needed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class RelocationAssessment:
    state: RelocationState
    destination: str = ""
    move_required: bool | None = None
    evidence: tuple[str, ...] = ()
    confidence: int = 0
    rationale: str = ""

    @property
    def supported(self) -> bool:
        return self.state in {RelocationState.OFFERED, RelocationState.CONDITIONAL}


_NEGATIVE = re.compile(
    r"\b(?:no|without)\s+relocation\s+(?:assistance|support|package|benefits?|"
    r"reimbursement)|\brelocation\s+(?:assistance|support|package|benefits?)"
    r"\s+(?:is\s+)?not\s+(?:available|offered|provided|included)|"
    r"\bnot\s+eligible\s+for\s+relocation|"
    r"\b(?:will|does)\s+not\s+(?:provide|offer|pay(?:\s+for)?)\s+relocation|"
    r"\brelocation\s+assistance\s*:\s*no\b",
    re.IGNORECASE,
)
_CONDITIONAL = re.compile(
    r"\brelocation\s+(?:assistance|support|package|benefits?)\s+"
    r"(?:may|might|can|could)\s+be\s+(?:available|offered|provided|considered)|"
    r"\brelocation\s+(?:may|might|can|could)\s+be\s+(?:available|offered|provided)|"
    r"\b(?:may|might)\s+be\s+eligible\s+for\s+relocation|"
    r"\brelocation\s+(?:assistance|support)\s+(?:depends|is considered|case[- ]by[- ]case)",
    re.IGNORECASE,
)
_OFFERED = re.compile(
    r"\brelocation\s+(?:assistance|support)\s+(?:is\s+)?"
    r"(?:available|offered|provided|included)|"
    r"\b(?:offers?|provides?|includes?)\s+relocation\s+(?:assistance|support)|"
    r"\brelocation\s+(?:package|allowance|bonus|reimbursement|benefits?)\b|"
    r"\bpaid\s+relocation\b|"
    r"\b(?:moving|relocation)\s+(?:expenses?|costs?)\s+(?:are\s+)?"
    r"(?:covered|reimbursed|paid)|"
    r"\brelocation\s+assistance\s*:\s*yes\b",
    re.IGNORECASE,
)
_REQUIRED = re.compile(
    r"\b(?:must|required\s+to|expected\s+to)\s+relocate\b|"
    r"\brelocation\s+(?:is\s+)?required\b|"
    r"\bmust\s+be\s+willing\s+to\s+relocate\b|"
    r"\bwillingness\s+to\s+relocate\s+(?:is\s+)?required\b",
    re.IGNORECASE,
)
_REMOTE = re.compile(r"\bremote\b", re.IGNORECASE)


def _evidence_lines(text: str) -> tuple[str, ...]:
    found: list[str] = []
    for line in normalize_job_description(text).splitlines():
        cleaned = " ".join(line.split()).strip()
        if not cleaned or not re.search(r"\brelocat|\bmoving\s+(?:expense|cost)", cleaned, re.I):
            continue
        if cleaned not in found:
            found.append(cleaned[:320])
    return tuple(found[:3])


def _home_metro(location: str, home_city: str) -> bool:
    city = re.sub(r"[^a-z0-9]+", " ", home_city.casefold()).strip()
    haystack = re.sub(r"[^a-z0-9]+", " ", location.casefold()).strip()
    return bool(city and re.search(rf"\b{re.escape(city)}\b", haystack))


def assess_relocation(
    *,
    description: str,
    location: str = "",
    workplace_type: str = "",
    home_city: str = "",
) -> RelocationAssessment:
    """Classify relocation only from explicit posting and structured location evidence."""
    destination = " ".join(str(location or "").split())
    normalized = normalize_job_description(description)
    evidence = _evidence_lines(normalized)
    relocation_text = "\n".join(evidence)
    remote = bool(_REMOTE.search(f"{destination} {workplace_type}"))
    local = _home_metro(destination, home_city)
    move_required = False if remote or local else (True if destination else None)

    if _NEGATIVE.search(relocation_text):
        return RelocationAssessment(
            RelocationState.NOT_OFFERED,
            destination,
            move_required,
            evidence,
            100,
            "The posting explicitly says relocation support is not offered.",
        )
    if _CONDITIONAL.search(relocation_text):
        return RelocationAssessment(
            RelocationState.CONDITIONAL,
            destination,
            move_required,
            evidence,
            90,
            "The posting says relocation support may be available; confirm terms.",
        )
    if _OFFERED.search(relocation_text):
        return RelocationAssessment(
            RelocationState.OFFERED,
            destination,
            move_required,
            evidence,
            100,
            "The posting explicitly offers relocation support.",
        )
    if _REQUIRED.search(relocation_text):
        return RelocationAssessment(
            RelocationState.REQUIRED_UNSUPPORTED,
            destination,
            True,
            evidence,
            95,
            "The posting requires relocation but does not confirm company support.",
        )
    if remote or local:
        reason = "The structured location is remote." if remote else "The role is in the candidate's home metro."
        return RelocationAssessment(
            RelocationState.NOT_NEEDED,
            destination,
            False,
            (),
            95,
            reason,
        )
    return RelocationAssessment(
        RelocationState.UNKNOWN,
        destination,
        move_required,
        evidence,
        30 if destination else 0,
        "The posting does not state whether relocation support is available.",
    )


def relocation_sort_rank(state: str | RelocationState) -> int:
    try:
        normalized = RelocationState(state)
    except ValueError:
        return 0
    return {
        RelocationState.OFFERED: 6,
        RelocationState.CONDITIONAL: 5,
        RelocationState.NOT_NEEDED: 4,
        RelocationState.REQUIRED_UNSUPPORTED: 3,
        RelocationState.NOT_OFFERED: 2,
        RelocationState.UNKNOWN: 1,
    }.get(normalized, 0)
