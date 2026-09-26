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
from jobpilot.engine.domain.settings import (
    CompanyRules,
    LevelRules,
    LocationRules,
    Profile,
    Remote,
    Rules,
    Settings,
    SettingsError,
    Targets,
    parse_settings,
)

__all__ = [
    "CompanyRules",
    "Event",
    "EventKind",
    "Lane",
    "LevelRules",
    "Listing",
    "LocationRules",
    "Opportunity",
    "Profile",
    "Remote",
    "RoleIdentity",
    "Rules",
    "Settings",
    "SettingsError",
    "Status",
    "Targets",
    "canonicalize_role_url",
    "identify_role",
    "infer_provider",
    "normalize_timestamp",
    "opportunity_id",
    "parse_settings",
    "status_of",
    "utc_now",
]
