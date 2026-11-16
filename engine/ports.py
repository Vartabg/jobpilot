"""Interfaces the engine needs from the outside world.

The service layer depends on these protocols, never on a concrete adapter, so
storage or a job source can be swapped without touching the engine's logic.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from jobpilot.engine.domain import (
    Assessment,
    BoardResult,
    BoardTarget,
    EmployerRecord,
    Event,
    FormSnapshot,
    HrContact,
    Lane,
    Listing,
    Opportunity,
    Posting,
    RequirementObservation,
    Settings,
    Status,
)
from jobpilot.engine.domain.fit import Fit


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


class FitChecker(Protocol):
    """Judges how well one posting fits the candidate."""

    name: str

    def assess(self, posting: Posting, settings: Settings) -> Fit: ...


class LanguageModel(Protocol):
    """A model that answers with JSON matching a schema."""

    name: str

    def generate_json(self, prompt: str, *, schema: Mapping[str, Any]) -> Any:
        """The parsed JSON answer. Raises ModelError when there's no usable answer."""
        ...


class EmployerStore(Protocol):
    """Persistent store of employer profiles, HR contacts, and pre-submit requirements."""

    def upsert_employer(self, employer: EmployerRecord) -> EmployerRecord: ...

    def upsert_contact(self, contact: HrContact, *, message_key: str = "") -> bool:
        """Record an HR contact and optional deduplicated message_key."""
        ...

    def record_observation(self, observation: RequirementObservation) -> bool: ...

    def replace_observations_for_role(
        self,
        role_key: str,
        source_kind: str,
        observations: Sequence[RequirementObservation],
    ) -> int: ...

    def record_snapshot(self, snapshot: FormSnapshot) -> None: ...

    def has_snapshot(
        self,
        role_key: str,
        *,
        statuses: tuple[str, ...] = ("ok", "inferred_gate", "unsupported"),
    ) -> bool: ...

    def employers(self) -> list[EmployerRecord]: ...

    def get_employer(self, employer_key: str) -> EmployerRecord | None: ...

    def contacts(self, employer_key: str | None = None) -> list[HrContact]: ...

    def observations(
        self, employer_key: str | None = None
    ) -> list[RequirementObservation]: ...

    def snapshots(self, employer_key: str | None = None) -> list[FormSnapshot]: ...


class FormSchemas(Protocol):
    """Read-only inspector of public ATS application form schemas."""

    def fetch(
        self, opportunity: Opportunity
    ) -> tuple[FormSnapshot, tuple[RequirementObservation, ...]]:
        """Never raises for HTTP/board failures; returns FormSnapshot(status='error')."""
        ...
