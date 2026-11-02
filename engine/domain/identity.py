"""Provider-aware canonical URLs and stable role identities.

The same role shows up under many URLs: tracking parameters, two Greenhouse
hostnames, embedded boards. Identity prefers the provider's own job ID, then a
canonical URL, so one role maps to one opportunity however it was found.
Adapted from the unmerged opportunity-ledger branch (2026-08).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_KEYS = frozenset(
    {
        "from",
        "fromage",
        "gh_src",
        "lever-source",
        "lever-via",
        "ref",
        "referrer",
        "source",
        "sourceid",
        "trk",
        "utm_campaign",
        "utm_content",
        "utm_medium",
        "utm_source",
        "utm_term",
    }
)

_GREENHOUSE_HOSTS = frozenset({"boards.greenhouse.io", "job-boards.greenhouse.io"})


def infer_provider(url: str) -> str:
    """Name the job board behind a URL, or return "" when it is unknown."""
    try:
        parsed = urlsplit(url.strip())
        host = (parsed.hostname or "").lower()
        _ = parsed.port  # raises ValueError on a malformed port
    except ValueError:
        return ""
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if "greenhouse.io" in host or "gh_jid" in query:
        return "greenhouse"
    if host.endswith("lever.co"):
        return "lever"
    if host.endswith("ashbyhq.com"):
        return "ashby"
    if "indeed." in host:
        return "indeed"
    if "adzuna." in host:
        return "adzuna"
    return ""


def canonicalize_role_url(url: str, provider: str = "") -> str:
    """Remove presentation and tracking noise while keeping the role's identity."""
    raw = url.strip()
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
        host = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError:
        return ""
    if not parsed.netloc or not host:
        return ""
    provider = provider.strip().lower() or infer_provider(raw)
    if provider == "greenhouse" and host in _GREENHOUSE_HOSTS:
        # Greenhouse serves the same role from both hostnames; one canonical
        # host keeps a hostname change from creating a second opportunity.
        host = "boards.greenhouse.io"
    display_host = f"[{host}]" if ":" in host else host
    netloc = display_host if not port or port in {80, 443} else f"{display_host}:{port}"
    path = re.sub(r"/{2,}", "/", parsed.path).rstrip("/") or "/"

    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    if provider == "indeed":
        pairs = [(key, value) for key, value in pairs if key.lower() == "jk"]
    elif provider in {"lever", "ashby"}:
        pairs = []
    elif provider == "greenhouse":
        pairs = [
            (key, value)
            for key, value in pairs
            if key.lower() in {"for", "gh_jid", "token"}
        ]
    else:
        pairs = [
            (key, value) for key, value in pairs if key.lower() not in _TRACKING_KEYS
        ]
    pairs.sort(key=lambda pair: (pair[0].lower(), pair[1]))
    return urlunsplit(
        ((parsed.scheme or "https").lower(), netloc, path, urlencode(pairs), "")
    )


@dataclass(frozen=True)
class RoleIdentity:
    """What can be observed about a role's identity from its URL."""

    provider: str
    tenant: str
    provider_job_id: str
    canonical_url: str

    @property
    def key(self) -> str:
        """The provider-native key when observable, else a canonical-URL hash, else ""."""
        if self.provider and self.provider_job_id:
            return ":".join(
                part
                for part in (self.provider, self.tenant, self.provider_job_id)
                if part
            )
        if self.canonical_url:
            return "url:" + hashlib.sha256(self.canonical_url.encode()).hexdigest()[:24]
        return ""


def identify_role(
    url: str, provider: str = "", tenant: str = "", provider_job_id: str = ""
) -> RoleIdentity:
    """Extract a provider-native identity, falling back to the canonical URL."""
    provider = provider.strip().lower() or infer_provider(url)
    canonical = canonicalize_role_url(url, provider)
    parsed = urlsplit(canonical)
    parts = [part for part in parsed.path.split("/") if part]
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    tenant = tenant.strip().lower()
    job_id = str(provider_job_id).strip()

    if provider == "greenhouse":
        if len(parts) >= 3 and parts[-2] == "jobs":
            tenant = tenant or parts[-3].lower()
            job_id = job_id or parts[-1]
        tenant = tenant or query.get("for", "").lower()
        job_id = job_id or query.get("gh_jid", "") or query.get("token", "")
    elif provider in {"lever", "ashby"} and len(parts) >= 2:
        tenant = tenant or parts[0].lower()
        job_id = job_id or parts[1]
    elif provider == "indeed":
        job_id = job_id or query.get("jk", "")

    return RoleIdentity(provider, tenant, job_id, canonical)
