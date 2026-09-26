"""Pure domain model: no I/O and no third-party imports."""

from jobpilot.engine.domain.events import (
    Event,
    EventKind,
    Status,
    normalize_timestamp,
    status_of,
    utc_now,
)
from jobpilot.engine.domain.identity import (
    RoleIdentity,
    canonicalize_role_url,
    identify_role,
    infer_provider,
)
from jobpilot.engine.domain.opportunity import (
    Lane,
    Listing,
    Opportunity,
    opportunity_id,
)

__all__ = [
    "Event",
    "EventKind",
    "Lane",
    "Listing",
    "Opportunity",
    "RoleIdentity",
    "Status",
    "canonicalize_role_url",
    "identify_role",
    "infer_provider",
    "normalize_timestamp",
    "opportunity_id",
    "status_of",
    "utc_now",
]
