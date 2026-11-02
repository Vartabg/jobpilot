"""Fit: how well a posting matches the candidate, judged against recorded evidence.

The rules here are pure and deterministic. They pull requirements out of a
posting, list the candidate's evidence (skills, domain experience, target
titles, languages), score title relevance, and turn per-requirement verdicts
into a tier. A model-backed checker may produce the verdicts instead, but the
tier is always decided here, so no model can talk a posting into "strong".
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from jobpilot.engine.domain.facts import is_optional, required_years, sections
from jobpilot.engine.domain.posting import Posting
from jobpilot.engine.domain.settings import Settings

FIT = "fit"
FIT_VERSION = "fit/1"
MAX_REQUIREMENTS = 25
RELEVANT = 0.5


class ModelError(Exception):
    """A language model couldn't produce a usable answer."""


class Verdict(StrEnum):
    MEETS = "meets"  # evidence covers it directly
    EQUIVALENT = "equivalent"  # evidence is a credible equivalent
    GAP = "gap"  # clearly missing
    UNKNOWN = "unknown"  # can't tell from the evidence


class Tier(StrEnum):
    STRONG = "strong"
    POSSIBLE = "possible"
    STRETCH = "stretch"
    OFF_TARGET = "off_target"


@dataclass(frozen=True)
class EvidenceItem:
    id: str
    kind: str  # skill | domain | education | language
    text: str
    term: str  # what to look for in posting text


@dataclass(frozen=True)
class Requirement:
    text: str
    must: bool = True


@dataclass(frozen=True)
class Match:
    requirement: Requirement
    verdict: Verdict
    evidence: tuple[str, ...] = ()
    note: str = ""


@dataclass(frozen=True)
class Fit:
    tier: Tier
    title_relevance: float
    coverage: float
    matches: tuple[Match, ...]
    biggest_gap: str
    engine: str
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier.value,
            "title_relevance": round(self.title_relevance, 3),
            "coverage": round(self.coverage, 3),
            "biggest_gap": self.biggest_gap,
            "engine": self.engine,
            "notes": list(self.notes),
            "matches": [
                {
                    "requirement": match.requirement.text,
                    "must": match.requirement.must,
                    "verdict": match.verdict.value,
                    "evidence": list(match.evidence),
                    "note": match.note,
                }
                for match in self.matches
            ],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Fit:
        return cls(
            tier=Tier(data["tier"]),
            title_relevance=float(data["title_relevance"]),
            coverage=float(data["coverage"]),
            matches=tuple(
                Match(
                    Requirement(item["requirement"], bool(item["must"])),
                    Verdict(item["verdict"]),
                    tuple(item.get("evidence", ())),
                    item.get("note", ""),
                )
                for item in data.get("matches", ())
            ),
            biggest_gap=data.get("biggest_gap", ""),
            engine=data.get("engine", ""),
            notes=tuple(data.get("notes", ())),
        )


# ----- evidence ------------------------------------------------------------------


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9#+.]+", "-", text.lower()).strip("-")


def evidence_from(settings: Settings) -> tuple[EvidenceItem, ...]:
    """What the candidate has actually done or knows, each with a stable ID.

    Target titles are deliberately left out: they describe roles the candidate
    wants, not experience, so they must never back a "meets" verdict.
    """
    targets, profile = settings.targets, settings.profile
    items = [
        EvidenceItem(f"skill:{_slug(skill)}", "skill", skill, skill)
        for skill in targets.skills
    ]
    items += [
        EvidenceItem(
            f"domain:{_slug(domain)}", "domain", f"{domain} ({years} years)", domain
        )
        for domain, years in targets.domain_experience.items()
    ]
    items += [
        EvidenceItem(f"education:{_slug(entry)}", "education", entry, "")
        for entry in profile.education
    ]
    items += [
        EvidenceItem(f"language:{_slug(language)}", "language", language, language)
        for language in profile.languages
    ]
    return tuple(dict.fromkeys(items))


def evidence_fingerprint(evidence: Iterable[EvidenceItem]) -> str:
    material = json.dumps(sorted((item.id, item.text) for item in evidence))
    return hashlib.sha256(material.encode()).hexdigest()[:16]


# ----- requirements --------------------------------------------------------------

_REQUIREMENT_CUES = re.compile(
    r"\b(experience (?:with|in|building|selling|leading)|proficien(?:t|cy)|knowledge of|"
    r"familiar(?:ity)? with|ability to|degree in|background in|expertise in|comfortable with|"
    r"track record)\b",
    re.IGNORECASE,
)


def _reads_like_requirement(line: str) -> bool:
    # Years only count with an experience context, so "a leader for over 40
    # years" is company history, not a requirement.
    return bool(_REQUIREMENT_CUES.search(line)) or required_years(line) is not None


def extract_requirements(posting: Posting) -> tuple[Requirement, ...]:
    """Requirement lines from a posting's requirement and preferred sections.

    Postings without such sections fall back to lines that read like a
    requirement ("3+ years", "experience with", "knowledge of").
    """
    found = [
        Requirement(line, must=kind == "required" and not is_optional(line))
        for kind, line in sections(posting.description)
        if kind in {"required", "preferred"} and len(line) >= 8
    ]
    if not found:
        found = [
            Requirement(line, must=not is_optional(line))
            for kind, line in sections(posting.description)
            if kind == "other" and _reads_like_requirement(line)
        ]
    return tuple(found[:MAX_REQUIREMENTS])


def _mentions(term: str, text: str) -> bool:
    return (
        re.search(rf"(?<![a-z0-9]){re.escape(term.lower())}(?![a-z0-9])", text.lower())
        is not None
    )


def match_rules(requirement: Requirement, evidence: Iterable[EvidenceItem]) -> Match:
    """A requirement meets when it names a skill, domain, or language on record.

    Rules never declare a gap: not finding a word proves nothing, so an
    unmatched requirement is unknown.
    """
    cited = tuple(
        item.id
        for item in evidence
        if item.kind in {"skill", "domain", "language"}
        and item.term
        and _mentions(item.term, requirement.text)
    )
    if cited:
        return Match(requirement, Verdict.MEETS, cited, "named in your evidence")
    return Match(requirement, Verdict.UNKNOWN)


# ----- title relevance -----------------------------------------------------------

_HEADS = frozenset(
    [
        "engineer",
        "manager",
        "consultant",
        "technician",
        "specialist",
        "representative",
        "executive",
        "analyst",
        "architect",
        "developer",
        "associate",
        "coordinator",
        "advisor",
        "agent",
    ]
)
_IGNORED = frozenset(
    [
        "and",
        "of",
        "the",
        "for",
        "to",
        "a",
        "an",
        "in",
        "at",
        "with",
        "i",
        "ii",
        "iii",
        "iv",
        "v",
        "sr",
        "senior",
        "jr",
        "junior",
        "staff",
        "principal",
        "lead",
        "head",
        "chief",
        "remote",
        "hybrid",
        "us",
        "usa",
    ]
)


def _stem(word: str) -> str:
    # No "-ing": it would turn "engineering" into "engineer" and "accounting"
    # into "account", matching roles that only share a word.
    for suffix in ("ments", "ment", "ed", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def _tokens(title: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", title.lower())
    return [_stem(word) for word in words if word not in _IGNORED]


def title_relevance(title: str, targets: Iterable[str]) -> float:
    """How closely a posting title matches the nearest target title, from 0 to 1.

    Distinctive words ("forward deployed", "solutions") carry 70%, and the role
    noun ("engineer") carries 30%. A target with one distinctive word needs the
    role noun too, so "Field Marketing Manager" doesn't pass for "Field Engineer".
    """
    posting = set(_tokens(title))
    best = 0.0
    for target in targets:
        words = _tokens(target)
        if not words:
            continue
        head = words[-1] if words[-1] in _HEADS else ""
        distinctive = [word for word in words if word != head]
        if not distinctive:
            continue
        share = sum(word in posting for word in distinctive) / len(distinctive)
        head_match = bool(head) and head in posting
        score = 0.7 * share + (0.3 if head_match else 0.0)
        if len(distinctive) == 1 and not head_match:
            score = min(score, 0.45)
        best = max(best, score)
    return round(best, 3)


# ----- decision ------------------------------------------------------------------


def decide(
    matches: tuple[Match, ...],
    relevance: float,
    *,
    engine: str,
    notes: tuple[str, ...] = (),
) -> Fit:
    """Turn verdicts into a tier with fixed, explainable thresholds."""
    musts = [match for match in matches if match.requirement.must] or list(matches)
    covered = sum(
        match.verdict in {Verdict.MEETS, Verdict.EQUIVALENT} for match in musts
    )
    gaps = [match for match in musts if match.verdict is Verdict.GAP]
    unknowns = [match for match in musts if match.verdict is Verdict.UNKNOWN]
    coverage = covered / len(musts) if musts else 0.0

    if relevance < RELEVANT:
        tier = Tier.OFF_TARGET
    elif not musts:
        tier = Tier.POSSIBLE  # nothing to judge against; the title alone fits
        notes = (*notes, "no requirements found in the posting")
    elif relevance >= 0.67 and coverage >= 0.5 and not gaps:
        tier = Tier.STRONG
    elif coverage >= 0.25 and len(gaps) <= 1:
        tier = Tier.POSSIBLE
    else:
        tier = Tier.STRETCH
    gap = (gaps or unknowns or [None])[0]
    return Fit(
        tier=tier,
        title_relevance=relevance,
        coverage=round(coverage, 3),
        matches=matches,
        biggest_gap=gap.requirement.text if gap else "",
        engine=engine,
        notes=notes,
    )


def assess_with_rules(posting: Posting, settings: Settings) -> Fit:
    """The deterministic fit check: no model, nothing leaves the computer."""
    evidence = evidence_from(settings)
    relevance = title_relevance(posting.listing.title, settings.targets.titles)
    matches = tuple(
        match_rules(requirement, evidence)
        for requirement in extract_requirements(posting)
    )
    return decide(matches, relevance, engine="rules")
