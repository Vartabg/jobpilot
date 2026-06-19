"""Shared compensation parsing for scraper text.

Hourly patterns are matched before salary patterns: "$70 - $90 per hour"
must never be misread as a "$70K-$90K" annual salary — the scorer's
2000-hours normalization would turn that $90/hr gig into a phantom
$35-45/hr one and silently drop it below the pay floor.

Salary ranges additionally require that the match is NOT followed by an
hourly marker, and comma-thousands ("$150,000 - $200,000") are parsed —
the old \\d{2,3} groups couldn't bridge the comma and matched nothing.
"""

from __future__ import annotations

import re

# "per hour"-style units. The bare "hr" variant ("$90 hr", "$90hr") is only
# used to *detect* hourly pay — the salary guard below excludes it so prose
# like "HR will reach out" can't veto a real salary range.
_HOURLY_UNIT = r"(?:per\s+hour|/\s*hour|/\s*hr|an\s+hour|hourly|hr\b)"
_HOURLY_GUARD = r"(?:per\s+hour|/\s*hour|/\s*hr|an\s+hour|hourly)"

_HOURLY_RANGE_RE = re.compile(
    rf"\$\s*(\d{{2,4}})\s*[-–—]\s*\$?\s*(\d{{2,4}})\s*{_HOURLY_UNIT}",
    re.IGNORECASE,
)
_HOURLY_RE = re.compile(
    rf"\$\s*(\d{{2,4}})\s*{_HOURLY_UNIT}",
    re.IGNORECASE,
)
# (?!,?\d) stops a comma-thousands number from half-matching ("$200,000"
# must not parse as 200); the hourly guard stops "$70 - $90 per hour" from
# parsing as a salary when the hourly-range pattern somehow missed it.
_SALARY_RE = re.compile(
    rf"\$\s*(\d{{2,3}}(?:,\d{{3}})?)[Kk]?\s*[-–—]\s*\$?\s*(\d{{2,3}}(?:,\d{{3}})?)[Kk]?"
    rf"(?!,?\d)(?!\s*{_HOURLY_GUARD})",
    re.IGNORECASE,
)


_CURRENCY_RE = re.compile(r"\b(USD|CAD|AUD|NZD|EUR|GBP|SGD)\b", re.IGNORECASE)


def detect_currency(text: str) -> str:
    """Best-effort currency for a comp string. Defaults to USD.

    A bare '$' is ambiguous (USD/CAD/AUD), so an explicit token wins:
    'C$' / 'CAD' -> CAD, '£' -> GBP, '€' -> EUR, etc. Without this, a
    '$105K-$125K CAD' role is silently treated (and salary-anchored) as USD.
    """
    if not text:
        return "USD"
    m = _CURRENCY_RE.search(text)
    if m:
        return m.group(1).upper()
    low = text.lower()
    if "c$" in low:
        return "CAD"
    if "a$" in low:
        return "AUD"
    if "£" in text:
        return "GBP"
    if "€" in text:
        return "EUR"
    return "USD"


def _to_number(group: str) -> float:
    return float(group.replace(",", ""))


def parse_comp(text: str) -> tuple[float, float, float]:
    """Returns (salary_min, salary_max, hourly_est) in USD.

    Hourly ranges win over salary ranges; a lone hourly figure is the last
    resort so "$150K-$200K (contract: $90/hr)" still reads as the salary
    range it leads with. Hourly ranges report the high end, mirroring the
    salary_max convention the scorer normalizes on.
    """
    if not text:
        return 0.0, 0.0, 0.0
    m = _HOURLY_RANGE_RE.search(text)
    if m:
        return 0.0, 0.0, _to_number(m.group(2))
    m = _SALARY_RE.search(text)
    if m:
        lo, hi = _to_number(m.group(1)), _to_number(m.group(2))
        if lo < 500:  # "$120K-150K" / unit-less "$120-150" — in thousands
            lo, hi = lo * 1000, hi * 1000
        return lo, hi, 0.0
    m = _HOURLY_RE.search(text)
    if m:
        return 0.0, 0.0, _to_number(m.group(1))
    return 0.0, 0.0, 0.0
