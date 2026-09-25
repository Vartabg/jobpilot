"""Interfaces the engine needs from the outside world.

The service layer depends on these protocols, never on a concrete adapter, so
storage or a job source can be swapped without touching the engine's logic.
"""

from __future__ import annotations

from typing import Protocol

from jobpilot.engine.domain import Event, Lane, Listing, Opportunity, Status


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
