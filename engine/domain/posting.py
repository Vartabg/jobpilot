"""Postings, and the sources that publish them.

A posting is the full text of one role as a job board published it. Screening
and fit checks read postings, never just titles.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum

from jobpilot.engine.domain.opportunity import Listing


class Workplace(StrEnum):
    REMOTE = "remote"
    HYBRID = "hybrid"
    ONSITE = "onsite"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Posting:
    """One role's full published text plus the structured facts a board gives."""

    listing: Listing
    description: str = ""
    workplace: Workplace = Workplace.UNKNOWN
    locations: tuple[str, ...] = ()
    compensation: str = ""
    employment_type: str = ""
    posted_at: str = ""

    @property
    def all_locations(self) -> tuple[str, ...]:
        """Every listed location, falling back to the listing's own location."""
        if self.locations:
            return self.locations
        return (self.listing.location,) if self.listing.location else ()

    @property
    def content_hash(self) -> str:
        """Changes whenever anything that screening or fit checks read changes."""
        payload = json.dumps(
            [
                self.listing.company,
                self.listing.title,
                self.description,
                self.workplace.value,
                list(self.all_locations),
                self.compensation,
                self.employment_type,
            ],
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:24]


@dataclass(frozen=True)
class BoardTarget:
    """One public job board to read: a provider and the company's board token."""

    provider: str
    token: str
    company: str = ""

    @property
    def name(self) -> str:
        return f"{self.provider}:{self.token}"


@dataclass(frozen=True)
class BoardResult:
    """Everything one board fetch returned. ``error`` means the fetch failed."""

    target: BoardTarget
    postings: tuple[Posting, ...] = ()
    error: str = ""
