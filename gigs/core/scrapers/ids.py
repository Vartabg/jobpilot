"""Stable gig IDs independent of Python's salted hash()."""

from __future__ import annotations

import hashlib


def stable_url_suffix(url: str, *, nbytes: int = 12) -> str:
    return hashlib.sha256((url or "").encode("utf-8")).hexdigest()[:nbytes]