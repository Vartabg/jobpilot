"""Facts read out of posting text: required years, languages, travel, pay, region.

Every extractor is deliberately conservative. A requirement only counts when
it appears in a required context. Anything in a "preferred", "bonus", or "nice
to have" section, or on a line that calls itself a plus, is ignored, because a
false "you don't qualify" hides a real opportunity.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

_PREFERRED_WORDS = re.compile(
    r"\b(preferred|nice[- ]to[- ]have|bonus|a plus|is a plus|are a plus|ideally|desired|optional)\b",
    re.IGNORECASE,
)
_SECTION_WORDS = re.compile(
    r"\b(requirements?|qualifications?|responsibilit(?:y|ies)|preferred|nice[- ]to[- ]have|bonus|"
    r"about (?:you|the role|us)|who you are|what you(?:'|\u2019)ll|you have|you bring|"
    r"we(?:'|\u2019)re looking for|must[- ]haves?|benefits|perks|compensation|what we offer)\b",
    re.IGNORECASE,
)
_BULLETS = ("-", "•", "*", "·", "–")


def _is_header(raw: str, line: str) -> bool:
    """A section heading: ends with a colon, or is a short line naming a known section."""
    if raw.lstrip().startswith(_BULLETS) or len(line) > 80:
        return False
    if line.endswith(":"):
        return True
    short = len(line.split()) <= 8 and not line.endswith((".", "!", "?", ","))
    letters = [char for char in line if char.isalpha()]
    shouting = len(letters) >= 4 and all(char.isupper() for char in letters)
    return short and (shouting or bool(_SECTION_WORDS.search(line)))


_REQUIRED_HEADINGS = re.compile(
    r"\b(requirements?|qualifications?|what you(?:'|\u2019)ll need|you have|you bring|who you are|"
    r"must[- ]haves?|what we(?:'|\u2019)re looking for|about you|skills|experience)\b",
    re.IGNORECASE,
)


def sections(text: str) -> Iterator[tuple[str, str]]:
    """Each content line with the kind of section it sits in.

    The kind is "required" under requirement-style headings, "preferred" under
    preferred or bonus headings, and "other" everywhere else.
    """
    kind = "other"
    for raw in text.splitlines():
        line = raw.strip().lstrip("".join(_BULLETS)).strip()
        if not line:
            continue
        if _is_header(raw, line):
            if _PREFERRED_WORDS.search(line):
                kind = "preferred"
            elif _REQUIRED_HEADINGS.search(line):
                kind = "required"
            else:
                kind = "other"
            continue
        yield kind, line


def is_optional(line: str) -> bool:
    """A line that calls itself optional ("a plus", "preferred", "nice to have")."""
    return bool(_PREFERRED_WORDS.search(line))


def required_lines(text: str) -> Iterator[str]:
    """Lines of a posting outside preferred sections and not self-described as optional."""
    for kind, line in sections(text):
        if kind != "preferred" and not is_optional(line):
            yield line


# ----- years -----------------------------------------------------------------

_YEARS = re.compile(
    r"(?P<low>\d{1,2})\s*(?:\+|plus)?\s*"
    r"(?:(?:-|–|—|to)\s*(?P<high>\d{1,2})\s*\+?\s*)?"
    r"(?:or\s+more\s+)?(?:years?|yrs?)\b(?P<after>[^.;\n]{0,40})",
    re.IGNORECASE,
)
_EXPERIENCE_CONTEXT = re.compile(
    r"^\s*(?:'|\u2019|of\b|in\b|as\b|working\b|building\b|leading\b|selling\b|shipping\b|"
    r"with\b|experience\b|professional\b|related\b|relevant\b|hands[- ]on\b|industry\b|"
    r"progressive\b|combined\b|total\b|minimum\b|required\b)",
    re.IGNORECASE,
)


def required_years(text: str) -> int | None:
    """The most years of experience a posting requires, or None if it states none.

    Ranges count by their lower bound ("3–5 years" requires 3).
    """
    found: list[int] = []
    for line in required_lines(text):
        for match in _YEARS.finditer(line):
            low = int(match.group("low"))
            after = match.group("after")
            if not 1 <= low <= 30:
                continue
            if _EXPERIENCE_CONTEXT.search(after) or "experience" in line.lower():
                found.append(low)
    return max(found) if found else None


# ----- languages -------------------------------------------------------------

LANGUAGES = [
    "arabic",
    "armenian",
    "cantonese",
    "chinese",
    "czech",
    "danish",
    "dutch",
    "english",
    "farsi",
    "finnish",
    "french",
    "german",
    "greek",
    "hebrew",
    "hindi",
    "hungarian",
    "indonesian",
    "italian",
    "japanese",
    "korean",
    "malay",
    "mandarin",
    "norwegian",
    "persian",
    "polish",
    "portuguese",
    "romanian",
    "russian",
    "spanish",
    "swedish",
    "tagalog",
    "thai",
    "turkish",
    "ukrainian",
    "urdu",
    "vietnamese",
]
_LANG = "|".join(LANGUAGES)
_LANGUAGE_WORD = re.compile(rf"\b({_LANG})\b", re.IGNORECASE)
_SPEAKING = re.compile(rf"\b({_LANG})[\s-]+speak(?:ing|er)\b", re.IGNORECASE)
_FLUENCY = re.compile(r"\b(fluen(?:t|cy)|proficien(?:t|cy)|bilingual)\b", re.IGNORECASE)


def required_languages(title: str, text: str) -> set[str]:
    """Languages a posting requires, lowercase. The title always counts.

    "<Language>-speaking" counts wherever it appears. On a line about fluency,
    proficiency, or being bilingual, every language named counts.
    """
    found: set[str] = set()
    for line in (title, *required_lines(text)):
        found.update(match.lower() for match in _SPEAKING.findall(line))
        if _FLUENCY.search(line):
            found.update(match.lower() for match in _LANGUAGE_WORD.findall(line))
    return found


# ----- travel and pay ----------------------------------------------------------

_TRAVEL = re.compile(
    r"travel[^.\n]{0,60}?(\d{1,3})\s*%|(\d{1,3})\s*%[^.\n]{0,40}?travel",
    re.IGNORECASE,
)


def travel_percent(text: str) -> int | None:
    """The most travel a posting mentions, as a percent, or None."""
    values = [int(a or b) for a, b in _TRAVEL.findall(text)]
    values = [value for value in values if 0 <= value <= 100]
    return max(values) if values else None


_MONEY = re.compile(r"(?<![A-Za-z])\$\s?(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*([kKmM])?")


def annual_pay_max(compensation: str) -> int | None:
    """The top of a USD annual pay range, or None if absent, hourly, or another currency."""
    if not compensation or re.search(
        r"/\s*h(?:ou)?r|hourly|per hour", compensation, re.IGNORECASE
    ):
        return None
    amounts = []
    for number, unit in _MONEY.findall(compensation):
        value = float(number.replace(",", ""))
        value *= {"k": 1_000, "m": 1_000_000}.get(unit.lower(), 1)
        amounts.append(int(value))
    amounts = [amount for amount in amounts if amount >= 1_000]
    return max(amounts) if amounts else None


# ----- region ------------------------------------------------------------------

_US_STATES = [
    "alabama",
    "alaska",
    "arizona",
    "arkansas",
    "california",
    "colorado",
    "connecticut",
    "delaware",
    "florida",
    "georgia",
    "hawaii",
    "idaho",
    "illinois",
    "indiana",
    "iowa",
    "kansas",
    "kentucky",
    "louisiana",
    "maine",
    "maryland",
    "massachusetts",
    "michigan",
    "minnesota",
    "mississippi",
    "missouri",
    "montana",
    "nebraska",
    "nevada",
    "ohio",
    "oklahoma",
    "oregon",
    "pennsylvania",
    "tennessee",
    "texas",
    "utah",
    "vermont",
    "virginia",
    "washington",
    "wisconsin",
    "wyoming",
    "new hampshire",
    "new jersey",
    "new mexico",
    "new york",
    "north carolina",
    "north dakota",
    "rhode island",
    "south carolina",
    "south dakota",
    "west virginia",
]
_US_ABBREVIATIONS = frozenset(
    [
        "AL",
        "AK",
        "AZ",
        "AR",
        "CA",
        "CO",
        "CT",
        "DE",
        "FL",
        "GA",
        "HI",
        "ID",
        "IL",
        "IN",
        "IA",
        "KS",
        "KY",
        "LA",
        "ME",
        "MD",
        "MA",
        "MI",
        "MN",
        "MS",
        "MO",
        "MT",
        "NE",
        "NV",
        "NH",
        "NJ",
        "NM",
        "NY",
        "NC",
        "ND",
        "OH",
        "OK",
        "OR",
        "PA",
        "RI",
        "SC",
        "SD",
        "TN",
        "TX",
        "UT",
        "VT",
        "VA",
        "WA",
        "WV",
        "WI",
        "WY",
        "DC",
    ]
)
_US_WORDS = re.compile(
    r"(?<![a-z])(?:united states|usa|u\.s\.(?:a\.)?|us|nyc|san francisco|bay area|seattle|"
    r"boston|chicago|denver|los angeles|austin|dallas|houston|atlanta|miami|"
    r"washington,? d\.?c\.?|" + "|".join(_US_STATES) + r")(?![a-z])",
    re.IGNORECASE,
)
_NON_US_WORDS = re.compile(
    r"\b(canada|toronto|vancouver|montreal|ontario|london|united kingdom|uk|england|scotland|"
    r"ireland|dublin|germany|berlin|munich|france|paris|netherlands|amsterdam|spain|madrid|"
    r"barcelona|portugal|lisbon|italy|milan|israel|tel aviv|herzliya|india|bangalore|bengaluru|"
    r"hyderabad|pune|singapore|japan|tokyo|korea|seoul|australia|sydney|melbourne|new zealand|"
    r"brazil|s[ãa]o paulo|mexico|argentina|colombia|chile|poland|warsaw|sweden|stockholm|"
    r"switzerland|zurich|denmark|copenhagen|norway|oslo|finland|helsinki|emea|apac|latam|europe|latin america|south america|central america|asia|africa|middle east|"
    r"philippines|manila|vietnam|indonesia|south africa|nigeria|kenya|egypt|uae|dubai)\b",
    re.IGNORECASE,
)


_COUNTRY_ONLY = re.compile(
    r"^\s*(?:united states(?: of america)?|usa|us|u\.s\.(?:a\.)?|north america|americas?|anywhere)\s*$",
    re.IGNORECASE,
)


def names_no_city(location: str) -> bool:
    """True for a location that names only a country or a broad region."""
    return bool(_COUNTRY_ONLY.match(location))


def region(location: str) -> str:
    """ "us", "non_us", or "unknown" for one location string."""
    us = bool(_US_WORDS.search(location)) or any(
        code in _US_ABBREVIATIONS for code in re.findall(r",\s*([A-Z]{2})\b", location)
    )
    if us:
        return "us"
    return "non_us" if _NON_US_WORDS.search(location) else "unknown"
