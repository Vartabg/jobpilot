"""The append-only history of an opportunity, and the one status derived from it.

Every screen used to keep its own status list (queue, tracker, gigs pipeline,
Mac app). Here status is never stored: it is computed from events, so it can't
drift and every screen agrees.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any


class EventKind(StrEnum):
    """Everything that can happen to an opportunity."""

    DISCOVERED = "discovered"  # a source listed the role
    SHORTLISTED = "shortlisted"  # you want to pursue it
    SKIPPED = "skipped"  # you passed on it
    KIT_PREPARED = "kit_prepared"  # resume and answers are ready
    APPLIED = "applied"
    OUTREACH = "outreach"  # you contacted someone; status is unchanged
    REPLIED = "replied"  # a person replied
    SCREEN = "screen"
    INTERVIEW = "interview"
    OFFER = "offer"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"
    NO_RESPONSE = "no_response"
    CLOSED = "closed"  # the posting came down


class Status(StrEnum):
    """The one status vocabulary shared by every screen."""

    NEW = "new"
    SHORTLISTED = "shortlisted"
    SKIPPED = "skipped"
    READY = "ready"
    APPLIED = "applied"
    INTERVIEWING = "interviewing"
    OFFER = "offer"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"
    NO_RESPONSE = "no_response"
    CLOSED = "closed"


def utc_now() -> str:
    """The current time as a normalized UTC timestamp."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def normalize_timestamp(value: str) -> str:
    """Parse an ISO-8601 timestamp that carries a timezone, and return it in UTC.

    Storing one canonical form keeps string order equal to time order.
    """
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        raise ValueError(f"timestamp needs a timezone: {value!r}")
    return moment.astimezone(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Event:
    """One immutable fact about one opportunity.

    ``key`` is the idempotency key: recording the same key twice is a no-op.
    It defaults to (opportunity, kind, time, source). Importers pass their own
    key so that re-running an import never duplicates history. ``id`` is derived
    from the key, and storage passes it back in when loading events.
    """

    opportunity_id: str
    kind: EventKind
    occurred_at: str
    source: str
    data: Mapping[str, Any] = field(default_factory=dict)
    key: str = ""
    id: str = ""

    def __post_init__(self) -> None:
        if not self.opportunity_id:
            raise ValueError("an event needs an opportunity_id")
        if not self.source:
            raise ValueError("an event needs a source")
        object.__setattr__(self, "kind", EventKind(self.kind))
        object.__setattr__(self, "occurred_at", normalize_timestamp(self.occurred_at))
        object.__setattr__(self, "data", MappingProxyType(dict(self.data)))
        if not self.id:
            basis = self.key or json.dumps(
                [self.opportunity_id, self.kind.value, self.occurred_at, self.source],
                separators=(",", ":"),
            )
            object.__setattr__(
                self, "id", "evt_" + hashlib.sha256(basis.encode()).hexdigest()[:24]
            )


_DECISIONS = {
    EventKind.SHORTLISTED: Status.SHORTLISTED,
    EventKind.SKIPPED: Status.SKIPPED,
    EventKind.KIT_PREPARED: Status.READY,
}
_CONVERSATION = frozenset({EventKind.REPLIED, EventKind.SCREEN, EventKind.INTERVIEW})
_ENDINGS = {
    EventKind.REJECTED: Status.REJECTED,
    EventKind.WITHDRAWN: Status.WITHDRAWN,
    EventKind.NO_RESPONSE: Status.NO_RESPONSE,
}
_BEFORE_APPLYING = frozenset(
    {Status.NEW, Status.SHORTLISTED, Status.SKIPPED, Status.READY, Status.CLOSED}
)


def status_of(events: Iterable[Event]) -> Status:
    """Fold an opportunity's events, oldest first, into its one status.

    - Seeing a role again never lowers its status. It only reopens a closed posting.
    - Shortlisting, skipping, and kit preparation matter only before you apply.
    - A closed posting only matters if you never applied.
    - The latest outcome wins, so a rejection can be followed by a new interview.
    """
    status = Status.NEW
    for event in events:
        kind = event.kind
        if kind is EventKind.DISCOVERED:
            if status is Status.CLOSED:
                status = Status.NEW
        elif kind in _DECISIONS:
            if status in _BEFORE_APPLYING:
                status = _DECISIONS[kind]
        elif kind is EventKind.CLOSED:
            if status in _BEFORE_APPLYING:
                status = Status.CLOSED
        elif kind is EventKind.APPLIED:
            if status not in {Status.INTERVIEWING, Status.OFFER}:
                status = Status.APPLIED
        elif kind in _CONVERSATION:
            if status is not Status.OFFER:
                status = Status.INTERVIEWING
        elif kind is EventKind.OFFER:
            status = Status.OFFER
        elif kind in _ENDINGS:
            status = _ENDINGS[kind]
    return status
