"""Truth-bounded candidate evidence used by deterministic role decisions."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from jobpilot.core.profile_store import UserProfile


def normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9+#.]+", " ", str(value or "").lower()).strip()


def tokens(value: str) -> set[str]:
    ignored = {
        "a", "an", "and", "at", "be", "for", "in", "of", "on", "or",
        "the", "to", "with", "you", "your", "experience", "required",
        "preferred", "ability", "knowledge", "skills", "work",
    }
    cleaned = {part.strip(".") for part in normalize(value).split()}
    return {part for part in cleaned if len(part) > 1 and part not in ignored}


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


@dataclass(frozen=True)
class EvidenceAccount:
    account_id: str
    title: str
    summary: str = ""
    details: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    truth_boundaries: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()

    @property
    def claims(self) -> tuple[str, ...]:
        return tuple(item for item in (self.title, self.summary, *self.details) if item)


@dataclass(frozen=True)
class CandidateEvidence:
    version: int | None
    skills: tuple[str, ...]
    target_titles: tuple[str, ...]
    domain_experience: dict[str, int | None]
    accounts: tuple[EvidenceAccount, ...]
    authorized_to_work: bool | None
    requires_sponsorship: bool | None
    open_to_relocation: bool | None
    years_of_experience: int | None

    @classmethod
    def load(cls, profile: UserProfile, accounts_path: Path | None = None) -> CandidateEvidence:
        version: int | None = None
        accounts: list[EvidenceAccount] = []
        if accounts_path is not None and Path(accounts_path).is_file():
            try:
                raw = json.loads(Path(accounts_path).read_text())
            except (OSError, ValueError, TypeError):
                raw = {}
            if isinstance(raw, dict):
                raw_version = raw.get("version")
                version = raw_version if isinstance(raw_version, int) else 1
                items = raw.get("accounts", [])
            elif isinstance(raw, list):
                version, items = 1, raw
            else:
                items = []
            if isinstance(items, list):
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    account_id = str(item.get("id", "")).strip()
                    title = str(item.get("title", "")).strip()
                    if not account_id or not title:
                        continue
                    accounts.append(EvidenceAccount(
                        account_id=account_id,
                        title=title,
                        summary=str(item.get("summary", "")).strip(),
                        details=_strings(item.get("details")),
                        skills=_strings(item.get("skills")),
                        truth_boundaries=_strings(item.get("truth_boundaries")),
                        tags=_strings(item.get("tags")),
                    ))

        skills = list(profile.skills)
        legacy = profile.custom_answers.get("skills", "")
        if legacy:
            skills.extend(part.strip() for part in re.split(r"[,;\n]", legacy) if part.strip())
        return cls(
            version=version,
            skills=tuple(dict.fromkeys(skills)),
            target_titles=tuple(profile.target_titles),
            domain_experience=dict(profile.domain_experience),
            accounts=tuple(accounts),
            authorized_to_work=profile.authorized_to_work,
            requires_sponsorship=profile.requires_sponsorship,
            open_to_relocation=profile.open_to_relocation,
            years_of_experience=profile.years_of_experience,
        )
