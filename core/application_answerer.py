"""Role-aware application answer drafting from true candidate accounts.

The generator uses a local account bank as the source of truth. AI drafting is
allowed only as a rewrite layer over those accounts, never as an evidence
source.
"""

from __future__ import annotations

import json
import re
import textwrap
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from jobpilot.core.bro_client import chat as bro_chat, is_bro_running
from jobpilot.core.config import DATA_DIR
from jobpilot.core.profile_store import ProfileStore, UserProfile, get_profile_store

TRUE_ACCOUNTS_PATH = DATA_DIR / "true_accounts.json"

# Shown when there is nothing in the profile or account bank to build a
# background answer from. Never substitute someone else's biography.
PROFILE_PLACEHOLDER = "[Add a short background summary with 'jobpilot profile --edit']"

# Tags counted as field/customer-site experience in fallback narratives.
_FIELD_TAGS = {"field", "field-primary", "fieldwork"}


@dataclass
class TrueAccount:
    """One verified account from data/true_accounts.json.

    Optional ``label`` is the human-friendly name used inside drafted
    sentences (defaults to ``title``). Optional ``tags`` tune scoring and
    narrative selection without hardcoding account ids:
      - "headline":      small boost on "why this role" questions
      - "field-primary": strongest boost for customer-site / deployment roles
      - "field":         moderate boost for customer-site / deployment roles
      - "fieldwork":     counts as field experience in fallback narratives
                          without affecting selection scoring
      - "ai":            boost when the role text mentions AI
    """

    id: str
    title: str
    timeframe: str = ""
    source: str = ""
    summary: str = ""
    label: str = ""
    details: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    question_fit: list[str] = field(default_factory=list)
    truth_boundaries: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


@dataclass
class AnswerDraft:
    question: str
    answer: str
    company: str = ""
    title: str = ""
    account_ids: list[str] = field(default_factory=list)
    source: str = "fallback"
    confidence: float = 0.0
    warnings: list[str] = field(default_factory=list)


class ApplicationAnswerer:
    """Draft paste-ready answers grounded in known true accounts."""

    def __init__(
        self,
        profile_store: Optional[ProfileStore] = None,
        *,
        accounts_path: Optional[Path] = None,
        use_bro: bool = True,
    ) -> None:
        self.profile_store = profile_store or get_profile_store()
        self.accounts_path = accounts_path or TRUE_ACCOUNTS_PATH
        self.use_bro = use_bro

    def draft(
        self,
        question: str,
        *,
        jd_text: str = "",
        company: str = "",
        title: str = "",
        max_words: int = 160,
    ) -> AnswerDraft:
        """Generate one answer for one application question."""
        clean_question = " ".join((question or "").split())
        profile = self.profile_store.load()
        accounts = self.load_accounts()
        selected = self.select_accounts(clean_question, jd_text=jd_text, company=company, title=title)

        if not selected:
            return AnswerDraft(
                question=clean_question,
                answer="",
                company=company,
                title=title,
                warnings=["No true accounts are available. Add data/true_accounts.json entries before drafting."],
            )

        ai_answer = ""
        if self.use_bro and is_bro_running():
            ai_answer = self._draft_with_ai(
                clean_question,
                profile=profile,
                accounts=selected,
                jd_text=jd_text,
                company=company,
                title=title,
                max_words=max_words,
            )

        if ai_answer:
            answer = ai_answer
            source = "bro"
            confidence = 0.82
        else:
            answer = self._fallback_answer(
                clean_question,
                profile=profile,
                accounts=selected,
                jd_text=jd_text,
                company=company,
                title=title,
                max_words=max_words,
            )
            source = "fallback"
            confidence = 0.58

        warnings: list[str] = []
        if source == "fallback":
            warnings.append("Bro/local AI was unavailable or disabled; used account-grounded fallback drafting.")
        if len(answer.split()) > max_words:
            answer = " ".join(answer.split()[:max_words]).rstrip(",.;") + "."
            warnings.append(f"Trimmed answer to {max_words} words.")

        answer = self._ascii_clean(answer)
        return AnswerDraft(
            question=clean_question,
            answer=answer,
            company=company,
            title=title,
            account_ids=[account.id for account in selected],
            source=source,
            confidence=confidence,
            warnings=warnings,
        )

    def load_accounts(self) -> list[TrueAccount]:
        if not self.accounts_path.exists():
            return []
        raw = json.loads(self.accounts_path.read_text())
        items = raw.get("accounts", raw if isinstance(raw, list) else [])
        accounts: list[TrueAccount] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            account_id = str(item.get("id", "")).strip()
            title = str(item.get("title", "")).strip()
            if not account_id or not title:
                continue
            accounts.append(
                TrueAccount(
                    id=account_id,
                    title=title,
                    timeframe=str(item.get("timeframe", "")).strip(),
                    source=str(item.get("source", "")).strip(),
                    summary=str(item.get("summary", "")).strip(),
                    label=str(item.get("label", "")).strip(),
                    details=[str(v).strip() for v in item.get("details", []) if str(v).strip()],
                    skills=[str(v).strip() for v in item.get("skills", []) if str(v).strip()],
                    question_fit=[str(v).strip() for v in item.get("question_fit", []) if str(v).strip()],
                    truth_boundaries=[str(v).strip() for v in item.get("truth_boundaries", []) if str(v).strip()],
                    tags=[str(v).strip() for v in item.get("tags", []) if str(v).strip()],
                )
            )
        return accounts

    def load_narrative(self) -> dict[str, str]:
        """Optional first-person copy blocks from the account bank.

        The top-level "narrative" object in data/true_accounts.json lets the
        user store reusable first-person phrasing (e.g. "background_summary",
        "value_proposition", "strengths_opener", "strengths_secondary") so the
        drafter never falls back to anyone else's biography.
        """
        if not self.accounts_path.exists():
            return {}
        raw = json.loads(self.accounts_path.read_text())
        narrative = raw.get("narrative", {}) if isinstance(raw, dict) else {}
        if not isinstance(narrative, dict):
            return {}
        return {str(key): str(value).strip() for key, value in narrative.items() if str(value).strip()}

    def select_accounts(
        self,
        question: str,
        *,
        jd_text: str = "",
        company: str = "",
        title: str = "",
        limit: int = 3,
    ) -> list[TrueAccount]:
        accounts = self.load_accounts()
        if not accounts:
            return []

        haystack = self._tokenize(" ".join([question, jd_text[:4000], company, title]))
        scored: list[tuple[int, TrueAccount]] = []
        for account in accounts:
            account_text = " ".join([
                account.id,
                account.title,
                account.summary,
                " ".join(account.details),
                " ".join(account.skills),
                " ".join(account.question_fit),
            ])
            account_tokens = self._tokenize(account_text)
            overlap = len(haystack & account_tokens)
            phrase_hits = sum(
                2 for phrase in account.question_fit + account.skills
                if phrase and phrase.lower() in " ".join([question, jd_text, title]).lower()
            )
            score = overlap + phrase_hits
            if "why" in question.lower() and "headline" in account.tags:
                score += 2
            role_text = " ".join([question, jd_text, title]).lower()
            if any(token in role_text for token in ("forward deployed", "client", "customer", "travel", "deployment")):
                if "field-primary" in account.tags:
                    score += 6
                elif "field" in account.tags:
                    score += 3
            if "ai" in role_text and "ai" in account.tags:
                score += 3
            scored.append((score, account))

        scored.sort(key=lambda item: item[0], reverse=True)
        selected = [account for score, account in scored if score > 0][:limit]
        return selected or [account for _score, account in scored[: min(limit, len(scored))]]

    def _draft_with_ai(
        self,
        question: str,
        *,
        profile: UserProfile,
        accounts: list[TrueAccount],
        jd_text: str,
        company: str,
        title: str,
        max_words: int,
    ) -> str:
        prompt = self._build_prompt(
            question,
            profile=profile,
            accounts=accounts,
            jd_text=jd_text,
            company=company,
            title=title,
            max_words=max_words,
        )
        reply = bro_chat(prompt, force_smart=True)
        if not reply or reply.startswith("Error") or reply.startswith("Bro is not") or reply.startswith("Request timed out"):
            return ""
        reply = reply.strip()
        reply = re.sub(r"^(answer|draft answer)\s*:\s*", "", reply, flags=re.IGNORECASE).strip()
        if len(reply.split()) < 12:
            return ""
        return reply

    def _build_prompt(
        self,
        question: str,
        *,
        profile: UserProfile,
        accounts: list[TrueAccount],
        jd_text: str,
        company: str,
        title: str,
        max_words: int,
    ) -> str:
        account_blocks = []
        for account in accounts:
            account_blocks.append(
                "\n".join([
                    f"ACCOUNT ID: {account.id}",
                    f"TITLE: {account.title}",
                    f"TIMEFRAME: {account.timeframe}",
                    f"SUMMARY: {account.summary}",
                    "DETAILS:",
                    *[f"- {detail}" for detail in account.details],
                    "SKILLS:",
                    *[f"- {skill}" for skill in account.skills],
                    "TRUTH BOUNDARIES:",
                    *[f"- {boundary}" for boundary in account.truth_boundaries],
                ])
            )

        profile_bits = [
            f"Name: {profile.first_name} {profile.last_name}".strip(),
            f"Current title: {profile.current_title}",
            f"Location: {profile.city}, {profile.state}",
            f"Years of experience: {profile.years_of_experience}",
            "US work authorized: yes" if profile.authorized_to_work else "US work authorized: no",
            "Requires sponsorship: yes" if profile.requires_sponsorship else "Requires sponsorship: no",
        ]

        return textwrap.dedent(f"""
            You are drafting a job application answer for the candidate described below.

            Rules:
            - Use only the candidate profile, job context, and TRUE ACCOUNTS below.
            - Do not invent metrics, customer names, employers, degrees, titles, dates, products, or outcomes.
            - If a detail is not in the true accounts, avoid the claim.
            - First person, direct, specific, natural.
            - ASCII only. No em dashes or curly quotes.
            - Keep it under {max_words} words.
            - Return only the answer text.

            Candidate profile:
            {chr(10).join(profile_bits)}

            Target role:
            Company: {company or "Not specified"}
            Title: {title or "Not specified"}
            Job context:
            {(jd_text or "")[:4500]}

            Application question:
            {question}

            TRUE ACCOUNTS:
            {"\n\n".join(account_blocks)}
        """).strip()

    def _fallback_answer(
        self,
        question: str,
        *,
        profile: UserProfile,
        accounts: list[TrueAccount],
        jd_text: str,
        company: str,
        title: str,
        max_words: int,
    ) -> str:
        q = question.lower()
        role = title or "this role"
        org = self._display_company(company) or "your team"
        narrative = self.load_narrative()
        primary = accounts[0]
        secondary = accounts[1] if len(accounts) > 1 else None
        field_account = next(
            (account for account in accounts if self._has_field_experience(account)),
            None,
        )

        if any(token in q for token in ("why", "interest", "excited", "motivat")):
            pitch = narrative.get("value_proposition", "")
            if pitch:
                opener = f"I am interested in {org} because {role} maps closely to the work I do best: {pitch}."
            else:
                opener = f"I am interested in {org} because {role} maps closely to work I can back with specific examples."
            parts = [
                opener,
                f"The clearest account is {self._account_label(primary)}: {self._first_person_summary(primary)}",
            ]
            if field_account and field_account.id != primary.id:
                parts.append(f"I also bring field experience from {self._account_label(field_account)}, where the work required customer-site execution, debugging, and ownership under pressure.")
            elif secondary:
                parts.append(f"A second relevant account is {self._account_label(secondary)}: {self._first_person_summary(secondary)}")
            parts.append("That mix is why the role feels like a practical fit rather than just a keyword match.")
            return " ".join(parts)

        if any(token in q for token in ("experience", "tell us about", "describe", "project", "challenge")):
            details = " ".join(self._first_person_detail(detail) for detail in primary.details[:3])
            answer = f"One relevant account is {self._account_label(primary)}. {self._first_person_summary(primary)}"
            if details:
                answer += f" In practice, that meant: {details}"
            if secondary:
                answer += f" A second useful thread is {self._account_label(secondary)}: {self._first_person_summary(secondary)}"
            return answer

        if any(token in q for token in ("strength", "bring", "contribution", "fit")):
            skills = ", ".join(primary.skills[:5])
            opener = narrative.get("strengths_opener", "") or "My strengths come from documented work rather than generic claims."
            if skills:
                answer = f"{opener} From {self._account_label(primary)}, I can point to hands-on work across {skills}. "
            else:
                answer = f"{opener} From {self._account_label(primary)}, I can point to documented hands-on work. "
            if secondary:
                secondary_pitch = narrative.get("strengths_secondary", "") or ", ".join(secondary.skills[:4])
                if secondary_pitch:
                    answer += f"From {self._account_label(secondary)}, I bring {secondary_pitch}."
                else:
                    answer += f"A second relevant account is {self._account_label(secondary)}: {self._first_person_summary(secondary)}"
            return answer

        if any(token in q for token in ("yourself", "background", "who are you")):
            return self._background_answer(profile, accounts, narrative)

        return (
            f"The most relevant true account is {self._account_label(primary)}. {self._first_person_summary(primary)} "
            f"I would connect that experience to {role} by focusing on practical ownership, fast learning, and clear delivery against real workflow constraints."
        )

    def _background_answer(
        self,
        profile: UserProfile,
        accounts: list[TrueAccount],
        narrative: dict[str, str],
    ) -> str:
        """Build a 'tell me about yourself' answer from the user's own data only."""
        stored = narrative.get("background_summary", "")
        if stored:
            return stored

        parts: list[str] = []
        current_title = (profile.current_title or "").strip()
        years = profile.years_of_experience or 0
        if current_title and years:
            parts.append(f"I am {self._with_article(current_title)} with {years}+ years of experience.")
        elif current_title:
            parts.append(f"I am {self._with_article(current_title)}.")
        elif years:
            parts.append(f"I bring {years}+ years of professional experience.")
        for account in accounts[:2]:
            summary = self._first_person_summary(account)
            if summary:
                parts.append(summary if summary.endswith(".") else f"{summary}.")
        if parts:
            return " ".join(parts)
        return PROFILE_PLACEHOLDER

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        return {
            token for token in re.sub(r"[^a-z0-9+#.]+", " ", (text or "").lower()).split()
            if len(token) > 2 and token not in {"the", "and", "for", "with", "this", "that", "role", "job"}
        }

    @staticmethod
    def _ascii_clean(text: str) -> str:
        replacements = {
            "\u2018": "'",
            "\u2019": "'",
            "\u201c": '"',
            "\u201d": '"',
            "\u2013": "-",
            "\u2014": "-",
            "\u2022": "-",
        }
        for old, new in replacements.items():
            text = text.replace(old, new)
        text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _display_company(company: str) -> str:
        cleaned = re.sub(r"[-_]+", " ", (company or "")).strip()
        if not cleaned:
            return ""
        words = ["AI" if word.lower() == "ai" else word.capitalize() for word in cleaned.split()]
        label = " ".join(words)
        return label.replace("P 1 AI", "P-1 AI")

    @staticmethod
    def _account_label(account: TrueAccount) -> str:
        return account.label or account.title

    @staticmethod
    def _has_field_experience(account: TrueAccount) -> bool:
        return bool(_FIELD_TAGS.intersection(account.tags))

    @staticmethod
    def _with_article(noun: str) -> str:
        article = "an" if noun[:1].lower() in "aeiou" else "a"
        return f"{article} {noun}"

    @staticmethod
    def _first_person_summary(account: TrueAccount) -> str:
        summary = account.summary.strip()
        replacements = {
            "Built ": "I built ",
            "Shipped ": "I shipped ",
            "Architected ": "I architected ",
            "Worked as ": "I worked as ",
            "Served in ": "I served in ",
            "Earned ": "I earned ",
        }
        for old, new in replacements.items():
            if summary.startswith(old):
                return new + summary[len(old):]
        return summary

    @staticmethod
    def _first_person_detail(detail: str) -> str:
        cleaned = detail.strip().rstrip(".")
        replacements = {
            "Used ": "I used ",
            "Integrated ": "I integrated ",
            "Designed ": "I designed ",
            "Added ": "I added ",
            "Built ": "I built ",
            "Configured ": "I configured ",
            "Served as ": "I served as ",
            "Worked directly ": "I worked directly ",
            "Delivered ": "I delivered ",
            "Performed ": "I performed ",
            "Communicated ": "I communicated ",
            "Supported ": "I supported ",
            "Developed ": "I developed ",
        }
        for old, new in replacements.items():
            if cleaned.startswith(old):
                cleaned = new + cleaned[len(old):]
                break
        return f"{cleaned}."
