"""Shared data models across all gigpilot scrapers."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Gig:
    """A normalized gig/job listing across all sources."""
    id: str                          # deduped stable ID ("source-externalid")
    source: str                      # "remoteok", "wwr", "himalayas", "hn"
    title: str
    url: str                         # canonical source/post URL (always set)
    apply_url: str = ""              # mobile-tappable apply target if extracted (mailto, ATS, etc.)
    company: str = ""
    description: str = ""
    location: str = ""               # "Remote", "NYC", "Queens", etc.
    posted_at: str = ""              # ISO timestamp if available
    salary_min: float = 0.0          # annual, in `currency` if available
    salary_max: float = 0.0
    pay_hourly_est: float = 0.0      # parsed hourly rate (fallback if no salary)
    currency: str = "USD"            # ISO code for the pay figures above
    tags: list[str] = field(default_factory=list)
    fit_score: int = 0               # 0-100, filled by scorer
    fit_reasons: list[str] = field(default_factory=list)
