"""Parse full job descriptions and link each claim to candidate evidence."""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from jobpilot.core.candidate_evidence import CandidateEvidence, normalize, tokens
from jobpilot.core.job_description import normalize_job_description
from jobpilot.core.profile_store import UserProfile


class EvidenceStatus(StrEnum):
    DIRECT = "direct"
    ADJACENT = "adjacent"
    UNKNOWN = "unknown"
    CONTRADICTED = "contradicted"


@dataclass(frozen=True)
class RequirementSet:
    raw_text: str
    mandatory: tuple[str, ...] = ()
    preferred: tuple[str, ...] = ()
    responsibilities: tuple[str, ...] = ()
    work_context: tuple[str, ...] = ()
    logistics: tuple[str, ...] = ()

    @property
    def is_sufficient(self) -> bool:
        primary = self.mandatory + self.preferred + self.responsibilities
        return len(self.raw_text.strip()) >= 140 and len(set(primary)) >= 3
@dataclass(frozen=True)
class RequirementMatch:
    category: str
    text: str
    status: EvidenceStatus
    account_ids: tuple[str, ...] = ()
    support: tuple[str, ...] = ()

_HEADER_END = r"(?:\s*(?::|[-\u2013\u2014]|=+)\s*|\s*$)"
_YOU_WILL = r"you(?:['\u2019]ll| will)"
_WE_ARE = r"we(?:['\u2019]re| are)"


def _header(labels: str) -> re.Pattern[str]:
    return re.compile(rf"^(?:{labels}){_HEADER_END}", re.IGNORECASE)


_HEADERS = (
    ("preferred", _header(
        r"preferred(?: (?:skills?(?:\s*(?:&|and)\s*qualifications?)?|"
        r"qualifications?|experience|requirements?))?|nice to haves?|bonus(?: points?)?|"
        r"desirable|ideal",
    )),
    ("mandatory", _header(
        rf"required(?: (?:skills?|qualifications?|experience|requirements?))?|"
        rf"(?:minimum|basic|essential)(?: (?:skills?|qualifications?|requirements?|"
        rf"experience))?(?: for (?:this|the) role)?|qualifications?|"
        rf"requirements?(?: for (?:this|the) role)?|what (?:{_WE_ARE} looking for|"
        rf"{_YOU_WILL} need|{_YOU_WILL} bring(?: to (?:the|our) team)?|"
        rf"you bring(?: to (?:the|our) team)?|we need)|{_WE_ARE} looking for|"
        rf"who you are|about you|"
        rf"candidate profile|ideal candidate(?: profile)?|"
        rf"your (?:background|experience|skills)|"
        rf"{_YOU_WILL} have|you have|skills?\s*(?:&|and)\s*(?:experience|"
        rf"qualifications?)|experience\s*(?:&|and)\s*skills?",
    )),
    ("responsibilities", _header(
        rf"(?:(?:core|key|primary|day[-\u2013\u2014 ]to[-\u2013\u2014 ]day|role and) )?"
        rf"responsibilit(?:y|ies)|accountabilities|what {_YOU_WILL} "
        rf"(?:do(?: here)?|be doing|own)|what you own|"
        rf"about the role|(?:role|position|job) (?:summary|overview|description|focus)|"
        rf"(?:your|the) role|role (?:purpose|mission)|scope of (?:the )?role|"
        rf"in (?:this|the) role|your impact|the opportunity|"
        rf"(?:key |essential )?(?:duties|functions|activities)|(?:a )?day in the life",
    )),
    ("work_context", _header(
        r"work environment|work context|how you work|ways of working|"
        r"how we work|what success looks like",
    )),
    ("logistics", _header(
        rf"logistics|(?:job |work |office )?locations?|travel|schedule|"
        rf"work authorization|physical requirements|working conditions|workplace|"
        rf"where {_YOU_WILL} work",
    )),
)
_RESET_HEADER = _header(
    r"about (?:us|the company|our company|the team|our team)|"
    r"(?:company|team) (?:overview|description)|meet (?:the|our) team|"
    r"benefits?(?: and perks?|\s*&\s*perks?)?|perks?|what we offer|total rewards?|"
    r"compensation|salary|pay range|our values|"
    r"(?:equal employment|equal opportunity|eeo|diversity|inclusion|privacy|"
    r"accommodation|legal|disclaimer)(?: statement| notice| policy)?|"
    r"(?:application|hiring|interview) process|how to apply|"
    r"why (?:join|work with|choose) (?:us|the company)"
)
_SCALAR_LOGISTICS = re.compile(
    r"^(?:job |work |office )?locations?\b",
    re.IGNORECASE,
)
_CONTEXT = re.compile(
    r"\b(customer|client|discovery|demo(?:nstration)?s?|workshops?|training|"
    r"cross-functional|communicat|present|independent|autonom|ambigu|stakeholder|field)\w*\b"
)
_LOGISTICS = re.compile(
    r"\b(remote|hybrid|on[ -]?site|travel|relocat|sponsor|authoriz|driver'?s license|"
    r"shift|weekend|overnight|physical|lift)\w*\b"
)
_US_JURISDICTION = re.compile(
    r"\b(?:u\.?s\.?a?|united states(?: of america)?)\b",
    re.IGNORECASE,
)
_YEARS = re.compile(r"(\d+)\+?\s+years?")
_OVERALL_TENURE = re.compile(
    r"^(?:(?:candidates?|applicants?|you)\s+(?:must|should)\s+have\s+)?"
    r"(?:(?:a\s+)?minimum(?:\s+of)?\s+|at\s+least\s+)?"
    r"\d+\+?\s+years?(?:\s+of)?(?:\s+(?:overall|total|professional|relevant))?"
    r"\s+experience(?:\s+(?:is\s+)?required)?$"
)
_COMPOSITE_SPLIT = re.compile(r"\s*(?:;|,|\band\b|\bplus\b)\s*", re.IGNORECASE)
_HARD_CREDENTIALS = (
    re.compile(
        r"\b(?:active\s+|current\s+)?(?:top\s+secret\s*/\s*sci|top\s+secret|"
        r"secret|confidential|ts\s*/?\s*sci|sci|security)"
        r"(?:\s+security)?\s+clearance\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:valid\s+)?(?:driver'?s\s+|professional\s+|state\s+)?"
        r"licen(?:ce|se|sed|sure)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:bachelor'?s|master'?s|doctorate|ph\.?d\.?)\s+(?:degree|diploma)\b|"
        r"\b(?:professional\s+)?certification\b",
        re.IGNORECASE,
    ),
)
_CONCEPTS = {
    "customer": {"customer", "customers", "client", "clients", "stakeholder", "stakeholders"},
    "presentation": {"demo", "demos", "demonstration", "demonstrations", "workshop", "workshops", "train", "trained", "training", "explain", "explained", "present"},
    "diagnosis": {"troubleshoot", "troubleshooting", "diagnose", "diagnosed", "repair", "repairs", "resolve", "resolved"},
    "integration": {"integration", "integrations", "hardware", "software", "systems"},
    "autonomy": {"independent", "independently", "autonomous", "autonomy", "ambiguity", "ambiguous"},
}


def _concepts(value: str) -> set[str]:
    found = set()
    words = tokens(value)
    for concept, variants in _CONCEPTS.items():
        if words & variants:
            found.add(concept)
    return found

class RequirementMatcher:
    def __init__(self, evidence: CandidateEvidence):
        self.evidence = evidence

    @classmethod
    def from_profile(cls, profile: UserProfile, accounts_path: Path | None = None) -> RequirementMatcher:
        return cls(CandidateEvidence.load(profile, accounts_path))

    @staticmethod
    def parse(raw_text: str) -> RequirementSet:
        normalized_text = normalize_job_description(raw_text)
        buckets: dict[str, list[str]] = {name: [] for name in (
            "mandatory", "preferred", "responsibilities", "work_context", "logistics",
        )}
        current: str | None = None
        one_shot = False
        for raw_line in re.split(r"\n+", normalized_text):
            item = re.sub(r"^[^\w]+", "", raw_line).strip()
            if not item:
                continue
            if _RESET_HEADER.match(item):
                current = None
                one_shot = False
                continue
            header_match = next(
                ((name, match) for name, pattern in _HEADERS if (match := pattern.match(item))),
                None,
            )
            if header_match:
                header, match = header_match
                current = header
                one_shot = header == "logistics" and bool(_SCALAR_LOGISTICS.match(item))
                item = item[match.end():].strip()
                if not item:
                    continue
            lowered = normalize(item)
            primary = current
            if primary is None:
                if re.search(
                    r"\b(must|required|minimum|at least|\d+\+?\s+years?|"
                    r"years? of experience)\b",
                    lowered,
                ) or re.match(
                    rf"^(?:you have|{_YOU_WILL} have|you bring|{_YOU_WILL} bring)\b",
                    item,
                    re.IGNORECASE,
                ):
                    primary = "mandatory"
                elif re.search(r"\b(preferred|nice to have|bonus)\b", lowered):
                    primary = "preferred"
            if primary and len(item) >= 5:
                buckets[primary].append(item)
            if _CONTEXT.search(lowered):
                buckets["work_context"].append(item)
            if _LOGISTICS.search(lowered):
                buckets["logistics"].append(item)
            if one_shot:
                current = None
                one_shot = False
        return RequirementSet(raw_text=normalized_text, **{
            key: tuple(dict.fromkeys(value)) for key, value in buckets.items()
        })

    def match(self, requirements: RequirementSet) -> tuple[RequirementMatch, ...]:
        matches = []
        for category in ("mandatory", "preferred", "responsibilities", "work_context", "logistics"):
            for item in getattr(requirements, category):
                status, account_ids, support = self._match_one(item, category)
                matches.append(RequirementMatch(category, item, status, account_ids, support))
        return tuple(matches)

    @property
    def _explicit_evidence(self) -> tuple[str, ...]:
        return (*self.evidence.skills, *(
            skill for account in self.evidence.accounts for skill in account.skills
        ))

    def _credential_is_unproven(self, item: str) -> bool:
        for pattern in _HARD_CREDENTIALS:
            for match in pattern.finditer(item):
                required = tokens(match.group())
                if not any(required <= tokens(claim) for claim in self._explicit_evidence):
                    return True
        return False

    def _has_unsupported_conjunct(
        self,
        item: str,
        *,
        structured: re.Pattern[str] | None = None,
    ) -> bool:
        parts = [part for part in _COMPOSITE_SPLIT.split(item) if tokens(part)]
        if len(parts) < 2:
            return False
        evidence_tokens = set().union(*(tokens(claim) for claim in self._explicit_evidence))
        supported = []
        for part in parts:
            part_tokens = tokens(part)
            supported.append(
                bool(structured and structured.search(part))
                or any(tokens(claim) and tokens(claim) <= part_tokens for claim in self._explicit_evidence)
                or part_tokens <= evidence_tokens
            )
        return any(supported) and not all(supported)

    def _adjacent_accounts(
        self,
        item: str,
        *,
        include_skills: bool = True,
    ) -> tuple[str, ...]:
        req_tokens = tokens(item)
        req_concepts = _concepts(item)
        found = []
        for account in self.evidence.accounts:
            claims = (*account.claims, *account.skills) if include_skills else account.claims
            claim_text = " ".join(claims)
            overlap = req_tokens & tokens(claim_text)
            concept_overlap = req_concepts & _concepts(claim_text)
            if len(overlap) >= 2 or len(concept_overlap) >= 2:
                found.append(account.account_id)
        return tuple(found)

    def _match_one(
        self,
        item: str,
        category: str,
    ) -> tuple[EvidenceStatus, tuple[str, ...], tuple[str, ...]]:
        req_tokens = tokens(item)
        for account in self.evidence.accounts:
            for boundary in account.truth_boundaries:
                overlap = req_tokens & tokens(boundary)
                if len(overlap) >= min(2, max(1, len(req_tokens))):
                    return EvidenceStatus.CONTRADICTED, (account.account_id,), (boundary,)

        lowered = normalize(item)
        if (
            "authoriz" in lowered or "sponsor" in lowered
        ) and _US_JURISDICTION.search(item):
            no_sponsorship = "without sponsorship" in lowered or "no sponsorship" in lowered
            if no_sponsorship and self.evidence.requires_sponsorship is True:
                return EvidenceStatus.CONTRADICTED, (), ("profile: requires sponsorship",)
            if (
                self.evidence.authorized_to_work is True
                and self.evidence.requires_sponsorship is False
            ):
                if self._has_unsupported_conjunct(
                    item,
                    structured=re.compile(r"\b(?:authoriz|sponsor)\w*\b", re.IGNORECASE),
                ):
                    return EvidenceStatus.UNKNOWN, (), ()
                return EvidenceStatus.DIRECT, (), ("profile: work authorization",)

        if self._credential_is_unproven(item):
            return EvidenceStatus.UNKNOWN, (), ()

        if category == "logistics":
            return EvidenceStatus.UNKNOWN, (), ()
        if category == "work_context":
            accounts = self._adjacent_accounts(item, include_skills=False)
            if accounts:
                return EvidenceStatus.ADJACENT, accounts, ("related account evidence",)
            return EvidenceStatus.UNKNOWN, (), ()

        years = _YEARS.search(lowered)
        if years:
            required_years = int(years.group(1))
            matching_domains = [
                (domain, domain_years)
                for domain, domain_years in self.evidence.domain_experience.items()
                if tokens(domain) and tokens(domain) <= req_tokens
            ]
            if matching_domains:
                domain, domain_years = matching_domains[0]
                if domain_years is None:
                    return EvidenceStatus.UNKNOWN, (), ()
                status = (
                    EvidenceStatus.DIRECT
                    if domain_years >= required_years
                    else EvidenceStatus.CONTRADICTED
                )
                if status is EvidenceStatus.DIRECT and self._has_unsupported_conjunct(
                    item,
                    structured=_YEARS,
                ):
                    return EvidenceStatus.UNKNOWN, (), ()
                return status, (), (f"profile domain experience: {domain}",)

            if not _OVERALL_TENURE.fullmatch(lowered.rstrip(".")):
                return EvidenceStatus.UNKNOWN, (), ()
            if self.evidence.years_of_experience is not None:
                status = (
                    EvidenceStatus.DIRECT
                    if self.evidence.years_of_experience >= required_years
                    else EvidenceStatus.CONTRADICTED
                )
                if status is EvidenceStatus.DIRECT and self._has_unsupported_conjunct(
                    item,
                    structured=_YEARS,
                ):
                    return EvidenceStatus.UNKNOWN, (), ()
                return status, (), ("profile: stated overall experience",)

        direct_support = []
        for skill in self.evidence.skills:
            skill_tokens = tokens(skill)
            if skill_tokens and skill_tokens <= req_tokens:
                direct_support.append(f"profile skill: {skill}")
        direct_accounts = []
        for account in self.evidence.accounts:
            if any(tokens(skill) and tokens(skill) <= req_tokens for skill in account.skills):
                direct_accounts.append(account.account_id)
                direct_support.append(f"account: {account.title}")
        if direct_support:
            if category == "mandatory" and self._has_unsupported_conjunct(item):
                return EvidenceStatus.UNKNOWN, (), ()
            return EvidenceStatus.DIRECT, tuple(dict.fromkeys(direct_accounts)), tuple(direct_support)

        adjacent_accounts = self._adjacent_accounts(item)
        if adjacent_accounts:
            return EvidenceStatus.ADJACENT, adjacent_accounts, ("related account evidence",)
        return EvidenceStatus.UNKNOWN, (), ()
