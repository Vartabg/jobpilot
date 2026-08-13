"""Retired form-filler compatibility API and read-only answer helpers.

The public fill and submit signatures remain importable for older callers but
always fail closed.  Mapping, option-matching, location, and resume-selection
helpers remain available for generating and reviewing manual paste materials.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from jobpilot.core.profile_store import UserProfile
from jobpilot.core.resume_tailor import ResumeTailor

# Default answer for EEOC / demographic self-identification questions when the
# user hasn't provided one in their profile: decline to self-identify. This is
# a PIPE-SEPARATED synonym list — the option matchers try each variant in turn
# because every ATS words the "decline" option differently.
DECLINE_TO_SELF_IDENTIFY = (
    "Prefer not to say||I don't wish to answer||I do not wish to answer||"
    "Decline to answer||Decline to self-identify||Decline to self identify||"
    "Don't wish to identify||Rather not say||Choose not to identify"
)

# Where to look in profile.custom_answers when profile.demographics is absent
# (older profiles stored demographic answers as question-keyed custom answers).
_DEMOGRAPHIC_CUSTOM_ANSWER_HINTS: dict[str, tuple[str, ...]] = {
    "gender": ("gender",),
    "sexual_orientation": ("sexual orientation",),
    "hispanic": ("hispanic", "latino"),
    "race": ("race", "ethnicity"),
    "veteran": ("veteran",),
    "disability": ("disability",),
}

DISALLOWED_FORM_LOCATION_PATTERNS = (
    "authorized to work in the uk",
    "authorised to work in the uk",
    "authorized to work in united kingdom",
    "authorised to work in united kingdom",
    "eligible to work in the uk",
    "eligible to work in united kingdom",
    "work in the uk",
    "work in united kingdom",
    "based in the uk",
    "based in united kingdom",
    "commuting distance of london",
    "london office",
    "based in germany",
    "work in germany",
    "professional fluency in german",
    "commuting distance of munich",
    "munich office",
)


@dataclass
class FillResult:
    success: bool
    filled_fields: list[str] = field(default_factory=list)
    skipped_fields: list[str] = field(default_factory=list)
    error: str | None = None
    stopped_before_submit: bool = True
    final_url: str | None = None
    final_title: str | None = None


# ---------------------------------------------------------------------------
# Label → profile-value mapping
# ---------------------------------------------------------------------------

def _field_to_value(label: str, profile: UserProfile) -> str | None:
    normalized_label = label.lower()

    if any(k in normalized_label for k in ["first name", "firstname", "given name", "first_name"]):
        return profile.first_name
    if any(k in normalized_label for k in ["last name", "lastname", "family name", "surname", "last_name"]):
        return profile.last_name
    if "preferred name" in normalized_label or "nickname" in normalized_label or "goes by" in normalized_label:
        return profile.first_name
    if "full name" in normalized_label or normalized_label.strip() in ("name", "your name", "full_name"):
        return f"{profile.first_name} {profile.last_name}".strip()

    if "email" in normalized_label:
        return profile.email
    if any(k in normalized_label for k in ["phone", "mobile", "cell", "telephone"]):
        return profile.phone

    if "location (city)" in normalized_label or normalized_label in ("city", "town"):
        return profile.city
    if normalized_label == "state" or "region" in normalized_label:
        return profile.state
    if normalized_label.strip() == "zip" or "postal" in normalized_label or "zip code" in normalized_label:
        return profile.zip_code
    if "country" in normalized_label:
        return profile.country
    if "location" in normalized_label or "based" in normalized_label or "address" in normalized_label:
        return f"{profile.city}, {profile.state}"

    if "linkedin" in normalized_label:
        return profile.linkedin_url
    if "github" in normalized_label:
        return profile.github_url
    if any(k in normalized_label for k in ["portfolio", "website", "personal site", "blog"]):
        return profile.portfolio_url

    if any(k in normalized_label for k in ["current title", "current role", "current position", "job title", "headline"]):
        return profile.current_title
    if any(k in normalized_label for k in ["current company", "current employer", "employer", "organization"]):
        return profile.current_company
    if "years of experience" in normalized_label or "years experience" in normalized_label:
        return (
            str(profile.years_of_experience)
            if profile.years_of_experience is not None
            else ""
        )
    if "salary" in normalized_label or "compensation" in normalized_label or "desired pay" in normalized_label:
        return profile.desired_salary

    # "How did you hear about this job?" — common required field
    if any(
        phrase in normalized_label
        for phrase in (
            "how did you hear",
            "how you heard",
            "hear about",
            "referral source",
        )
    ):
        answers = profile.custom_answers or {}
        for q, a in answers.items():
            if "how did you hear" in q.lower() or "hear about" in q.lower():
                return a
        return None

    return None


def _demographic_value(profile: UserProfile, key: str) -> str:
    """Resolve a demographic (EEOC self-identification) answer.

    Demographic answers are NEVER hardcoded — they belong to the user.
    Sources, in priority order:
      1. ``profile.demographics[key]`` — the dedicated demographics block
         (keys: veteran, gender, race, hispanic, disability,
         sexual_orientation).
      2. ``profile.custom_answers`` — question-keyed answers from older
         profiles (e.g. "Veteran Status": "...").
      3. ``DECLINE_TO_SELF_IDENTIFY`` — when unset we decline to identify
         rather than assert anything on the user's behalf.

    Values may be "||"-separated synonym lists; the option matchers try
    each variant in turn.
    """
    demographics = getattr(profile, "demographics", None)
    if isinstance(demographics, dict):
        value = (demographics.get(key) or "").strip()
        if value:
            return value
    answers = getattr(profile, "custom_answers", None) or {}
    hints = _DEMOGRAPHIC_CUSTOM_ANSWER_HINTS.get(key, ())
    for question, answer in answers.items():
        ql = question.lower()
        if answer and any(h in ql for h in hints):
            return answer
    return DECLINE_TO_SELF_IDENTIFY


def _yesno_for_question(label: str, profile: UserProfile) -> str | None:
    normalized_label = label.lower()
    if any(p in normalized_label for p in [
        "authorized to work", "legally authorized", "authorization to work",
        "eligible to work", "work in the us", "work in the united states",
        "us citizen", "u.s. citizen", "citizenship",
    ]):
        if profile.authorized_to_work is None:
            return None
        return "Yes" if profile.authorized_to_work else "No"
    if any(p in normalized_label for p in [
        "sponsorship", "visa sponsorship", "require sponsorship",
        "need sponsorship", "will you require employment",
    ]):
        if profile.requires_sponsorship is None:
            return None
        return "No" if not profile.requires_sponsorship else "Yes"

    answers = profile.custom_answers or {}
    for question, answer in answers.items():
        ql = question.lower()
        if ql in normalized_label or normalized_label in ql:
            return answer

    if any(p in normalized_label for p in [
        "willing to work", "open to work", "open to hybrid", "on-site", "in-office",
        "hub location", "able to commute", "willing to commute",
        "willing to relocate", "open to relocation", "able to relocate",
        "will you relocate", "comfortable relocating",
        "days per week in", "days a week in", "days in the office",
        "comfortable with hybrid", "hybrid work schedule", "hybrid schedule",
        "office presence", "onsite requirement",
    ]):
        return None
    # Don't preemptively request relocation ASSISTANCE — skip those, user reviews
    if any(p in normalized_label for p in [
        "relocation assistance", "require relocation", "need relocation",
        "relocation package",
    ]):
        return None  # user decides — better to leave blank than say Yes too early

    # EEOC / self-identification questions — answers come from the user's
    # profile via _demographic_value (default: decline to self-identify).
    # Returns a PIPE-SEPARATED synonym list so the select/dropdown matcher
    # can find ANY of these substrings in the option text. The answer_selects
    # code splits on "||" and tries each.
    if "gender identity" in normalized_label or (
        "gender" in normalized_label and "violence" not in normalized_label
    ):
        return _demographic_value(profile, "gender")
    if "sexual orientation" in normalized_label:
        return _demographic_value(profile, "sexual_orientation")
    if "hispanic" in normalized_label or "latino" in normalized_label:
        return _demographic_value(profile, "hispanic")
    if "race" in normalized_label or "ethnicity" in normalized_label:
        return _demographic_value(profile, "race")
    if "veteran" in normalized_label:
        return _demographic_value(profile, "veteran")
    if "disability" in normalized_label or "disabled" in normalized_label:
        return _demographic_value(profile, "disability")

    # These questions require an explicit user answer; absence is not "No".
    if any(p in normalized_label for p in [
        "family member", "close personal relationship", "relative who works",
        "outside business activity", "outside business",
        "worked for", "past employee of", "previous employment with",
        "currently live within", "live within 50 miles", "live within",
        "conflict of interest", "improperly bias",
    ]):
        return None
    return None


def _normalize_option_text(text: str) -> str:
    return " ".join((text or "").lower().split())


def _radio_option_matches(candidate: str, radio_label: str, radio_value: str) -> bool:
    """Match a radio option strictly — exact text or whole-word match only.

    Plain substring matching is too loose here: the answer "No" must not
    match "None of the above" or "Not sure". Mirrors the stricter select
    matcher: exact normalized equality first, then a word-boundary search
    for answers embedded in verbose labels ("No, I do not require...").
    """
    needle = _normalize_option_text(candidate)
    if not needle:
        return False
    for haystack in (_normalize_option_text(radio_label), _normalize_option_text(radio_value)):
        if not haystack:
            continue
        if needle == haystack:
            return True
        if re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack):
            return True
    return False


def _detect_disallowed_form_location(text: str) -> str | None:
    normalized = " ".join((text or "").lower().split())
    for pattern in DISALLOWED_FORM_LOCATION_PATTERNS:
        if pattern in normalized:
            return pattern
    return None


# ---------------------------------------------------------------------------
# Read-only material helpers
# ---------------------------------------------------------------------------


def _preferred_resume_upload(profile: UserProfile) -> tuple[Path | None, str]:
    """Prefer the latest tailored resume PDF, then fall back to profile resume."""
    latest_draft = ResumeTailor.load_latest_draft_summary()
    if latest_draft:
        pdf_path = str(latest_draft.get("pdf_path", "") or "")
        if pdf_path:
            tailored_pdf = Path(pdf_path).expanduser()
            if tailored_pdf.exists() and tailored_pdf.is_file():
                return tailored_pdf, "tailored"

    resume_path = getattr(profile, "resume_path", "") or ""
    if resume_path:
        candidate = Path(resume_path).expanduser()
        if candidate.exists() and candidate.is_file():
            return candidate, "profile"

    return None, "missing"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def fill_application(
    job_url: str,
    job_title: str,
    company: str,
    profile: UserProfile,
    cdp_port: int = 9222,
) -> FillResult:
    """Retired live-form entry point kept as a fail-closed compatibility shim."""
    del job_url, job_title, company, profile, cdp_port
    return FillResult(
        success=False,
        error=(
            "Live ATS form filling is retired. Generate a paste sheet, then "
            "paste, review, and submit in your normal browser."
        ),
        stopped_before_submit=True,
    )


async def submit_application(cdp_port: int = 9222) -> tuple[bool, str]:
    """Submit is disabled by policy.

    Live filling and submission are both retired. Keep this function as a hard
    fence so future integrations cannot accidentally resurrect either path.
    """
    return False, (
        "Auto-submit disabled: live ATS automation is retired. Paste and review "
        "the prepared material, then submit it yourself."
    )
