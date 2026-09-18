"""Employer-offered relocation detection for job postings.

Keyword scan (no LLM): finds statements where the *employer* offers
relocation help ("relocation package", "relocation assistance", ...) and
rejects lookalikes — employer negations ("no relocation"), questions put
to the candidate ("Do you require relocation assistance?"), and
candidate-willingness language ("willing to relocate").

Both pipelines share this: the ATS scorer (``core.job_scorer``) and the
gigs scorer (``gigs.core.scorer``).
"""

from __future__ import annotations

import re

# Offer phrasing from real postings. Matched case-insensitively with word
# boundaries; keep entries specific — bare "relocation" alone is usually a
# screening question, not an offer.
_RELOCATION_OFFER_PATTERNS = (
    "relocation assistance",
    "relocation package",
    "relocation support",
    "relocation bonus",
    "relocation stipend",
    "relocation allowance",
    "relocation benefits",
    "relocation reimbursement",
    "relocation expenses",
    "relocation offered",
    "relocation available",
    "relocation provided",
    "relocation included",
    "assistance with relocation",
    "assist with relocation",
    "help with relocation",
    "covers relocation",
    "paid relocation",
)

# Employer-side negations — checked before positives, anywhere in the text.
_RELOCATION_NEGATIONS = (
    "no relocation",
    "without relocation",
    "relocation not offered",
    "relocation not provided",
    "relocation not available",
    "relocation not included",
    "not eligible for relocation",
    "does not offer relocation",
    "do not offer relocation",
    "unable to offer relocation",
    "cannot offer relocation",
    "no relocation assistance",
    "no relocation package",
)

# A relocation mention stays an employer offer unless the verb sits next to
# it ("require relocation assistance", "relocation required: yes/no") —
# those are screening asks aimed at the candidate. Kept adjacent-only so a
# genuine offer elsewhere in the sentence ("package for those who need to
# move") still counts.
_CANDIDATE_ASK_RE = re.compile(
    r"\b(?:require[sd]?|need[sd]?|request(?:s|ed|ing)?)\W+(?:\w+\W+){0,3}relocat"
    r"|relocat\w*\W+(?:\w+\W+){0,3}(?:require[sd]?|need[sd]?|request(?:s|ed|ing)?)\b"
)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT_RE.split(text or "") if s.strip()]


def detect_relocation_offer(text: str) -> str | None:
    """Return the matched offer phrase, or None when no employer offer found.

    A sentence counts as an offer when it contains an offer pattern and is
    neither a question nor a candidate-directed ask. Employer negations
    anywhere in the posting veto the whole text.
    """
    lowered = (text or "").lower()
    if not lowered:
        return None
    if any(neg in lowered for neg in _RELOCATION_NEGATIONS):
        return None
    for sentence in _sentences(lowered):
        if sentence.endswith("?"):
            continue
        if _CANDIDATE_ASK_RE.search(sentence):
            continue
        for pattern in _RELOCATION_OFFER_PATTERNS:
            if re.search(rf"(?<![a-z0-9]){re.escape(pattern)}(?![a-z0-9])", sentence):
                return pattern
    return None


def offers_relocation(text: str) -> bool:
    """True when the posting offers employer-paid relocation help."""
    return detect_relocation_offer(text) is not None
