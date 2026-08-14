"""Provider-aware canonical URLs and stable role identities."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_KEYS = frozenset({
    "from", "fromage", "gh_src", "lever-source", "lever-via", "ref",
    "referrer", "source", "sourceid", "trk", "utm_campaign", "utm_content",
    "utm_medium", "utm_source", "utm_term",
})

_GREENHOUSE_HOST_ALIASES = frozenset({
    "boards.greenhouse.io",
    "job-boards.greenhouse.io",
})


def infer_provider(url: str) -> str:
    try:
        parsed = urlsplit(url.strip())
        host = (parsed.hostname or "").lower()
        parsed.port
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
    """Remove presentation/tracking noise while retaining role identity."""
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
    normalized_provider = provider.strip().lower() or infer_provider(raw)
    if normalized_provider == "greenhouse" and host in _GREENHOUSE_HOST_ALIASES:
        # Greenhouse serves the same board roles from both hostnames. Keeping a
        # single canonical host prevents a host migration from creating a new
        # role in application history.
        host = "boards.greenhouse.io"
    display_host = f"[{host}]" if ":" in host else host
    netloc = (
        display_host
        if not port or port in {80, 443}
        else f"{display_host}:{port}"
    )
    path = re.sub(r"/{2,}", "/", parsed.path).rstrip("/") or "/"
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    if normalized_provider == "indeed":
        pairs = [(key, value) for key, value in pairs if key.lower() == "jk"]
    elif normalized_provider in {"lever", "ashby"}:
        pairs = []
    elif normalized_provider == "greenhouse":
        pairs = [
            (key, value)
            for key, value in pairs
            if key.lower() in {"for", "gh_jid", "token"}
        ]
    else:
        pairs = [(key, value) for key, value in pairs if key.lower() not in _TRACKING_KEYS]
    pairs.sort(key=lambda pair: (pair[0].lower(), pair[1]))
    query = urlencode(pairs, doseq=True)
    return urlunsplit(((parsed.scheme or "https").lower(), netloc, path, query, ""))


def is_safe_public_role_url(url: str) -> bool:
    """Only absolute HTTPS URLs may be exposed as posting links."""
    raw = (url or "").strip()
    if any(ord(character) < 32 or ord(character) == 127 for character in raw):
        return False
    try:
        parsed = urlsplit(raw)
        host = parsed.hostname
        parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme.lower() == "https"
        and bool(host)
        and parsed.username is None
        and parsed.password is None
    )


@dataclass(frozen=True)
class RoleIdentity:
    provider: str
    tenant: str
    provider_job_id: str
    canonical_url: str

    @property
    def key(self) -> str:
        pieces = [self.provider, self.tenant, self.provider_job_id]
        present = [piece for piece in pieces if piece]
        if self.provider_job_id:
            return ":".join(present)
        digest = hashlib.sha256(self.canonical_url.encode()).hexdigest()[:24]
        return f"url:{digest}"


def identify_role(
    url: str,
    provider: str = "",
    tenant: str = "",
    provider_job_id: str = "",
) -> RoleIdentity:
    """Extract a provider-native identity, falling back to canonical URL."""
    normalized_provider = provider.strip().lower() or infer_provider(url)
    canonical = canonicalize_role_url(url, normalized_provider)
    parsed = urlsplit(canonical)
    parts = [part for part in parsed.path.split("/") if part]
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    normalized_tenant = tenant.strip().lower()
    job_id = str(provider_job_id).strip()

    if normalized_provider == "greenhouse":
        if len(parts) >= 3 and parts[-2] == "jobs":
            normalized_tenant = normalized_tenant or parts[-3].lower()
            job_id = job_id or parts[-1]
        normalized_tenant = normalized_tenant or query.get("for", "").lower()
        job_id = job_id or query.get("gh_jid", "") or query.get("token", "")
    elif normalized_provider in {"lever", "ashby"} and len(parts) >= 2:
        normalized_tenant = normalized_tenant or parts[0].lower()
        job_id = job_id or parts[1]
    elif normalized_provider == "indeed":
        job_id = job_id or query.get("jk", "")

    return RoleIdentity(normalized_provider, normalized_tenant, job_id, canonical)


def explicit_role_identity_key(url: str) -> str:
    """Return a provider-native role key, or ``""`` when none is observable.

    A canonical URL hash is useful for general deduplication, but it is not an
    explicit provider assertion. History matching uses only native IDs here so
    that a coincidentally similar URL cannot inherit another role's outcome.
    """
    identity = identify_role(url)
    if not identity.provider or not identity.provider_job_id:
        return ""
    return identity.key


build_role_identity = identify_role
canonical_role_url = canonicalize_role_url
