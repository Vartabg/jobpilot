"""Strict external-link validation for public gig sources and UI actions."""

from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit

_EMAIL = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def safe_external_url(value: str, *, allow_mailto: bool = False) -> str:
    """Return a normalized safe URL or ``""`` for an unsafe target.

    Public listing text is untrusted.  Web targets must have an HTTP(S)
    hostname and no embedded credentials.  Mail targets, where explicitly
    enabled, are limited to one plain address with no query/body payload.
    """
    value = str(value or "").strip()
    decoded = unquote(value)
    if (
        not value
        or any(char.isspace() for char in value)
        or any(ord(char) < 32 or ord(char) == 127 for char in decoded)
    ):
        return ""
    try:
        parsed = urlsplit(value)
        _ = parsed.port  # Force validation of malformed ports.
    except ValueError:
        return ""
    scheme = parsed.scheme.lower()
    if scheme == "mailto" and allow_mailto:
        address = parsed.path
        if parsed.netloc or parsed.query or parsed.fragment or not _EMAIL.fullmatch(address):
            return ""
        return f"mailto:{address}"
    if scheme not in {"http", "https"}:
        return ""
    if not parsed.hostname or parsed.username is not None or parsed.password is not None:
        return ""
    return value
