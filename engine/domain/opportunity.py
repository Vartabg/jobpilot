"""Opportunities: one record per real role, however many places list it."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum

from jobpilot.engine.domain.identity import identify_role


class Lane(StrEnum):
    """Which pipeline an opportunity belongs to."""

    JOB = "job"
    GIG = "gig"


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


@dataclass(frozen=True)
class Listing:
    """What one source says about a role, before it enters the ledger."""

    company: str
    title: str
    url: str = ""
    location: str = ""
    lane: Lane = Lane.JOB
    provider: str = ""
    provider_job_id: str = ""

    def identity_key(self) -> str:
        """A stable key for the underlying role.

        The provider's own job ID wins, then the canonical URL. A listing with
        neither falls back to company and title, which is the weakest key, so
        sources should always supply a URL when they have one.
        """
        if self.url or self.provider_job_id:
            key = identify_role(
                self.url, self.provider, provider_job_id=self.provider_job_id
            ).key
            if key:
                return key
        company, title = _norm(self.company), _norm(self.title)
        if not company or not title:
            raise ValueError(
                "a listing needs a URL, a provider job ID, or a company and title"
            )
        return f"role:{company}:{title}"


def opportunity_id(identity_key: str) -> str:
    """A short, stable opportunity ID derived from the identity key."""
    return "opp_" + hashlib.sha256(identity_key.encode()).hexdigest()[:20]


@dataclass(frozen=True)
class Opportunity:
    """One real role in the ledger. Its status comes from its events."""

    id: str
    identity_key: str
    lane: Lane
    company: str
    title: str
    url: str
    location: str
    provider: str
    tenant: str
    provider_job_id: str
    first_seen: str
    last_seen: str
