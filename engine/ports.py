"""Interfaces the engine needs from the outside world.

The service layer depends on these protocols, never on a concrete adapter, so
storage or a job source can be swapped without touching the engine's logic.
"""

from __future__ import annotations

from typing import Protocol

from jobpilot.engine.domain import (
    Assessment,
    BoardResult,
    BoardTarget,
    Event,
    Lane,
    Listing,
    Opportunity,
    Posting,
    Settings,
    Status,
)


class Ledger(Protocol):
    """The one store of opportunities and their append-only history."""

    def upsert(self, listing: Listing, *, seen_at: str) -> Opportunity:
        """Create the opportunity behind a listing, or refresh it, and return it."""
        ...

    def append(self, event: Event) -> bool:
        """Record an event. Returns False when the same event key is already recorded."""
        ...

    def get(self, opportunity_id: str) -> Opportunity | None: ...

    def events(self, opportunity_id: str) -> list[Event]:
        """The opportunity's events, oldest first."""
        ...

    def status(self, opportunity_id: str) -> Status: ...

    def opportunities(self, lane: Lane | None = None) -> list[Opportunity]:
        """All opportunities, most recently seen first."""
        ...

    def save_posting(
        self, opportunity_id: str, posting: Posting, *, fetched_at: str
    ) -> bool:
        """Store the latest full text of a role. Returns True when the text changed."""
        ...

    def posting(self, opportunity_id: str) -> Posting | None: ...

    def record_assessment(self, assessment: Assessment) -> bool:
        """Record a judgment. Returns False when the same inputs were already assessed."""
        ...

    def latest_assessment(
        self, opportunity_id: str, kind: str
    ) -> Assessment | None: ...


class Boards(Protocol):
    """Public job boards: one fetch returns every posting on one board."""

    def fetch(self, target: BoardTarget) -> BoardResult:
        """Never raises for board problems; they come back as ``BoardResult.error``."""
        ...


class SettingsSource(Protocol):
    """Where the user's settings come from. The engine reads them, never writes them."""

    def load(self) -> tuple[Settings, list[str]]:
        """The settings plus warnings. Raises SettingsError when they can't be used."""
        ...
