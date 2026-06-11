"""Job fit scoring for pre-apply triage.

Turns a job description into a simple 0-100 fit score using the existing
profile store, JD parser helpers, and an optional AI verdict when a backend
(local Bro or Gemini) is available.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional, Protocol

from jobpilot.core import llm_client
from jobpilot.core.bro_client import is_bro_running, query_rag
from jobpilot.core.jd_parser import JDParser, ParsedJD
from jobpilot.core.policy_config import Policy, get_policy
from jobpilot.core.profile_store import ProfileStore, UserProfile, get_profile_store


_STOPWORDS = {
    "a", "an", "and", "at", "for", "in", "of", "on", "or", "the", "to",
    "engineer", "engineering", "developer", "software", "senior", "staff",
    "principal", "lead", "manager", "role", "job",
}


@dataclass
class JobFitResult:
    """Structured fit score result."""

    score: int
    recommendation: str
    parsed_jd: ParsedJD
    components: dict[str, int] = field(default_factory=dict)
    matched_skills: list[str] = field(default_factory=list)
    missing_skills: list[str] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    ai_summary: str = ""


class SupportsProfileLoad(Protocol):
    """Minimal protocol for objects that can load a `UserProfile`."""

    def load(self) -> UserProfile: ...


class JobScorer:
    """Compute a lightweight, explainable job-fit score."""

    def __init__(
        self,
        profile_store: ProfileStore | SupportsProfileLoad | None = None,
        *,
        use_bro: bool = True,
        policy: Policy | None = None,
    ):
        self.profile_store = profile_store or get_profile_store()
        self.use_bro = use_bro
        self.policy = policy or get_policy()

    def score_text(
        self,
        raw_text: str,
        *,
        title: str = "",
        company: str = "",
    ) -> JobFitResult:
        """Score a raw job description string."""
        text = (raw_text or "").strip()
        parsed = ParsedJD(
            title=title.strip() or self._guess_title(text),
            company=company.strip() or self._guess_company(text),
            raw_text=text,
        )
        parsed.requirements, parsed.nice_to_haves = JDParser._extract_sections(text)
        parsed.skills = JDParser._extract_skills(text)
        parsed.location_type = self._extract_location_type(text)
        parsed.salary_range = self._extract_salary(text)
        return self.score_parsed_jd(parsed)

    def score_parsed_jd(self, parsed_jd: ParsedJD) -> JobFitResult:
        """Score an already-parsed JD object.

        Applies the user's policy filter (``data/policy.json``) first.
        Roles matching a refused company or title keyword short-circuit to
        score=0 + REFUSED recommendation regardless of other components
        (via -100 Alignment penalty clamped at 0). Deprioritized companies
        receive a -25 penalty and a downgraded recommendation tier. The
        shipped defaults refuse and deprioritize nothing.
        """
        profile = self.profile_store.load()
        raw_text = parsed_jd.raw_text or parsed_jd.summary()
        jd_skills = parsed_jd.skills or JDParser._extract_skills(raw_text)
        candidate_skills = self._candidate_skills(profile)

        matched_skills = [skill for skill in jd_skills if skill.lower() in candidate_skills]
        missing_skills = [skill for skill in jd_skills if skill.lower() not in candidate_skills]

        # Policy filter (applied first; can short-circuit the total)
        alignment_status, alignment_rationale = self._check_alignment(parsed_jd)
        if alignment_status == "refused":
            alignment_points = -100  # forces total to 0 via the clamp below
        elif alignment_status == "deprioritized":
            alignment_points = -25
        else:
            alignment_points = 0

        title_points = self._score_title_alignment(profile.current_title, parsed_jd.title)
        skill_points = self._score_skills(jd_skills, matched_skills)
        exp_points, exp_risk = self._score_experience(profile.years_of_experience, raw_text)
        auth_points, auth_risk = self._score_work_auth(profile.authorized_to_work, profile.requires_sponsorship, raw_text)
        location_points = self._score_location(parsed_jd.location_type or self._extract_location_type(raw_text))

        components = {
            "Alignment": alignment_points,
            "Title match": title_points,
            "Skills": skill_points,
            "Experience": exp_points,
            "Work auth": auth_points,
            "Location": location_points,
        }

        total = max(0, min(100, sum(components.values())))
        strengths: list[str] = []
        risks: list[str] = []

        if alignment_rationale:
            risks.append(alignment_rationale)

        if matched_skills:
            strengths.append(f"Matched skills: {', '.join(matched_skills[:4])}")
        if title_points >= 18 and parsed_jd.title:
            strengths.append(f"Title alignment looks solid for {parsed_jd.title}")
        if exp_points >= 16:
            strengths.append("Experience level looks aligned with the role")
        if parsed_jd.location_type == "remote":
            strengths.append("Remote role — lower friction to pursue")

        if missing_skills:
            risks.append(f"Missing or unclear skills: {', '.join(missing_skills[:4])}")
        if exp_risk:
            risks.append(exp_risk)
        if auth_risk:
            risks.append(auth_risk)
        if parsed_jd.location_type == "onsite":
            risks.append("Onsite role may need extra scrutiny")

        ai_summary = self._maybe_get_ai_summary(profile, parsed_jd, matched_skills, missing_skills)

        return JobFitResult(
            score=total,
            recommendation=self._recommendation(total, alignment_status=alignment_status),
            parsed_jd=parsed_jd,
            components=components,
            matched_skills=matched_skills,
            missing_skills=missing_skills,
            strengths=strengths,
            risks=risks,
            ai_summary=ai_summary,
        )

    @staticmethod
    def _normalize_tokens(text: str) -> set[str]:
        cleaned = re.sub(r"[^a-z0-9+.#]+", " ", (text or "").lower())
        return {
            token for token in cleaned.split()
            if len(token) > 1 and token not in _STOPWORDS
        }

    def _candidate_skills(self, profile: UserProfile) -> set[str]:
        profile_text = " ".join(
            filter(
                None,
                [
                    profile.current_title,
                    profile.current_company,
                    profile.portfolio_url,
                    profile.github_url,
                    " ".join(profile.custom_answers.values()),
                ],
            )
        )

        if self.use_bro and is_bro_running():
            rag_context = query_rag(
                "List the candidate's strongest technical skills and domain strengths.",
                top_k=4,
            )
            if rag_context:
                profile_text = f"{profile_text}\n{rag_context}"

        skills = {skill.lower() for skill in JDParser._extract_skills(profile_text)}
        skills.update(self._normalize_tokens(profile.current_title))
        return skills

    def _score_title_alignment(self, current_title: str, jd_title: str) -> int:
        current_tokens = self._normalize_tokens(current_title)
        jd_tokens = self._normalize_tokens(jd_title)
        if not current_tokens or not jd_tokens:
            return 10

        overlap = current_tokens & jd_tokens
        ratio = len(overlap) / max(len(jd_tokens), 1)
        return min(25, max(0, int(round(25 * ratio))))

    @staticmethod
    def _score_skills(jd_skills: list[str], matched_skills: list[str]) -> int:
        if not jd_skills:
            return 18
        ratio = len(matched_skills) / max(len(jd_skills), 1)
        return min(35, max(0, int(round(35 * ratio))))

    @staticmethod
    def _score_experience(years_of_experience: int, raw_text: str) -> tuple[int, str]:
        matches = [int(m.group(1)) for m in re.finditer(r"(\d+)\+?\s*(?:years|yrs)", raw_text, re.IGNORECASE)]
        required_years = max(matches) if matches else 0

        if required_years <= 0:
            return (12 if years_of_experience else 8), ""

        ratio = min(1.0, (years_of_experience or 0) / required_years)
        points = min(20, max(0, int(round(20 * ratio))))
        risk = ""
        if years_of_experience < required_years:
            risk = f"Role asks for about {required_years}+ years; profile shows {years_of_experience}."
        return points, risk

    @staticmethod
    def _score_work_auth(authorized_to_work: bool, requires_sponsorship: bool, raw_text: str) -> tuple[int, str]:
        lowered = raw_text.lower()
        mentions_auth = "authorized to work" in lowered or "work authorization" in lowered
        mentions_no_sponsorship = any(
            phrase in lowered
            for phrase in [
                "without sponsorship",
                "no sponsorship",
                "not provide sponsorship",
                "cannot sponsor",
            ]
        )

        if mentions_no_sponsorship and requires_sponsorship:
            return 0, "Posting requires work authorization without sponsorship."
        if mentions_auth and not authorized_to_work:
            return 2, "Posting mentions work authorization and profile is not authorized."
        if mentions_auth and authorized_to_work:
            return 10, ""
        return (8 if authorized_to_work else 5), ""

    @staticmethod
    def _score_location(location_type: str) -> int:
        if location_type == "remote":
            return 10
        if location_type == "hybrid":
            return 8
        if location_type == "onsite":
            return 5
        return 7

    def _check_alignment(self, parsed_jd: ParsedJD) -> tuple[str, str]:
        """Apply the user's policy filter (``data/policy.json``).

        Returns (status, rationale) where status is one of:
            "refused"        — company or title matches the policy's refused lists
            "deprioritized"  — company is configured as deprioritized
            "aligned"        — no policy entry matched (always, with defaults)
        """
        scoring = self.policy.scoring
        company_lower = (parsed_jd.company or "").lower()
        title_lower = (parsed_jd.title or "").lower()

        # Hard refused — company-level
        for refused_co, reason in scoring.refused_companies.items():
            if refused_co in company_lower:
                rationale = f"Refused by policy: {refused_co.title()}"
                if reason:
                    rationale += f" — {reason}"
                return ("refused", rationale)

        # Hard refused — title-level (keywords matched regardless of company)
        for keyword, reason in scoring.refused_title_keywords.items():
            if keyword in title_lower:
                rationale = f"Refused by policy: title contains '{keyword}'"
                if reason:
                    rationale += f" — {reason}"
                return ("refused", rationale)

        # Deprioritized — penalized and downgraded, but not zeroed
        for dep_co, reason in scoring.deprioritized_companies.items():
            if dep_co in company_lower:
                rationale = f"{dep_co.title()}: deprioritized by policy"
                if reason:
                    rationale += f" — {reason}"
                return ("deprioritized", rationale)

        return ("aligned", "")

    @staticmethod
    def _recommendation(score: int, alignment_status: str = "aligned") -> str:
        if alignment_status == "refused":
            return "REFUSED by policy — do not submit"
        if alignment_status == "deprioritized":
            if score >= 65:
                return "Aligned but deprioritized by policy — manual override required"
            return "Aligned but deprioritized — low priority"
        if score >= 80:
            return "Strong fit — prioritize"
        if score >= 65:
            return "Good fit — worth a closer look"
        if score >= 50:
            return "Stretch fit — apply selectively"
        return "Low fit — likely skip"

    @staticmethod
    def _guess_title(text: str) -> str:
        for line in text.splitlines():
            stripped = line.strip()
            if 4 <= len(stripped) <= 80:
                return stripped
        return ""

    @staticmethod
    def _guess_company(text: str) -> str:
        candidates = [line.strip() for line in text.splitlines() if line.strip()]
        return candidates[1] if len(candidates) > 1 and len(candidates[1]) <= 80 else ""

    @staticmethod
    def _extract_salary(text: str) -> str:
        match = re.search(
            r"\$[\d,]+(?:\s*[-–]\s*\$[\d,]+)?(?:\s*/?\s*(?:yr|year|annually|hr|hour))?",
            text,
            re.IGNORECASE,
        )
        return match.group(0).strip() if match else ""

    @staticmethod
    def _extract_location_type(text: str) -> str:
        lowered = text.lower()
        if "remote" in lowered:
            return "remote"
        if "hybrid" in lowered:
            return "hybrid"
        if "on-site" in lowered or "onsite" in lowered or "on site" in lowered:
            return "onsite"
        return ""

    def _maybe_get_ai_summary(
        self,
        profile: UserProfile,
        parsed_jd: ParsedJD,
        matched_skills: list[str],
        missing_skills: list[str],
    ) -> str:
        if not self.use_bro or not llm_client.is_available() or not parsed_jd.summary():
            return ""

        prompt = (
            "Give a brief 2-sentence job-fit verdict. "
            "Sentence 1: strongest reason to pursue. "
            "Sentence 2: biggest risk or gap to watch.\n\n"
            f"Candidate title: {profile.current_title or 'N/A'}\n"
            f"Experience: {profile.years_of_experience} years\n"
            f"Job: {parsed_jd.summary()}\n"
            f"Matched skills: {', '.join(matched_skills) or 'none'}\n"
            f"Missing skills: {', '.join(missing_skills[:4]) or 'none'}"
        )
        # RAG context only exists on the local Bro backend.
        context = ""
        if is_bro_running():
            context = query_rag(
                f"Summarize candidate strengths relevant to {parsed_jd.title or 'this role'} at {parsed_jd.company or 'the company'}",
                top_k=4,
            )
        try:
            reply = llm_client.complete(prompt, context=context or None)
        except llm_client.LLMUnavailable:
            return ""
        return reply.strip()
