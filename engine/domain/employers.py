"""Pure employer, HR contact, and pre-submit requirement domain model.

No I/O and no third-party imports: every identifier, gate, field classifier,
contact extractor, and summary calculation is deterministic and pure.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from email.utils import getaddresses, parseaddr
from enum import StrEnum
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from jobpilot.engine.domain.identity import (
    RoleIdentity,
    canonicalize_role_url,
    identify_role,
)


class GateKind(StrEnum):
    """Pre-submit gate or blocker observed on an employer's application flow."""

    NONE = "none"
    ACCOUNT_REQUIRED = "account_required"
    EMAIL_VERIFICATION_CODE = "email_verification_code"
    CAPTCHA = "captcha"
    APPLICATION_LIMIT = "application_limit"
    CONSENT_WALL = "consent_wall"
    SILENT_DROP_OR_RATE_LIMIT = "silent_drop_or_rate_limit"


class ChannelKind(StrEnum):
    """How an HR or recruiting email sender reaches the applicant."""

    DIRECT_PERSON = "direct_person"
    COMPANY_RECRUITING = "company_recruiting"
    ATS_RELAY = "ats_relay"


class FrictionTier(StrEnum):
    """Overall pre-submit friction for an employer."""

    LOW_FRICTION = "low_friction"
    CUSTOM_QUESTIONS = "custom_questions"
    GATED = "gated"


class RequirementCategory(StrEnum):
    """Normalized pre-submit application field or condition category."""

    IDENTITY = "identity"
    CONTACT = "contact"
    DOCUMENTS = "documents"
    PROFESSIONAL_LINKS = "professional_links"
    ADDRESS = "address"
    WORK_AUTHORIZATION = "work_authorization"
    CITIZENSHIP_EXPORT = "citizenship_export"
    LOCATION_SCHEDULE = "location_schedule"
    COMPENSATION = "compensation"
    AVAILABILITY = "availability"
    EXPERIENCE_TENURE = "experience_tenure"
    TECHNICAL_SKILLS = "technical_skills"
    EDUCATION_GPA = "education_gpa"
    WORK_SAMPLE_PORTFOLIO = "work_sample_portfolio"
    MEDIA_VOICE_VIDEO = "media_voice_video"
    BESPOKE_ESSAY = "bespoke_essay"
    AI_POLICY = "ai_policy"
    PRIVACY_LEGAL_CONSENT = "privacy_legal_consent"
    BACKGROUND_DRUG_MVR = "background_drug_mvr"
    PHYSICAL_REQUIREMENTS = "physical_requirements"
    REFERENCES = "references"
    DEMOGRAPHICS_EEO = "demographics_eeo"
    OTHER = "other"


BLOCKING_GATES: frozenset[GateKind] = frozenset(
    {
        GateKind.ACCOUNT_REQUIRED,
        GateKind.EMAIL_VERIFICATION_CODE,
        GateKind.CAPTCHA,
        GateKind.APPLICATION_LIMIT,
        GateKind.CONSENT_WALL,
        GateKind.SILENT_DROP_OR_RATE_LIMIT,
    }
)

ROUTINE_POLICY_CATEGORIES: frozenset[RequirementCategory] = frozenset(
    {
        RequirementCategory.WORK_AUTHORIZATION,
        RequirementCategory.LOCATION_SCHEDULE,
    }
)

PUBLIC_API_PROVIDERS: frozenset[str] = frozenset({"greenhouse", "ashby", "lever"})

_SENSITIVE_QUERY_KEYS: frozenset[str] = frozenset(
    {
        "access_token",
        "auth",
        "code",
        "email",
        "key",
        "secret",
        "session",
        "sig",
        "signature",
        "token",
    }
)

_PROVIDER_HOST_RULES: tuple[tuple[tuple[str, ...], str, GateKind | None], ...] = (
    (("myworkdayjobs.com",), "workday", GateKind.ACCOUNT_REQUIRED),
    (("ultipro.com",), "ukg", GateKind.ACCOUNT_REQUIRED),
    (("icims.com",), "icims", GateKind.ACCOUNT_REQUIRED),
    (
        ("oraclecloud.com", "enterpriseplatform.dell.com"),
        "oracle",
        GateKind.ACCOUNT_REQUIRED,
    ),
    (("paycomonline.net",), "paycom", GateKind.ACCOUNT_REQUIRED),
    (
        (
            "successfactors.com",
            "successfactors.eu",
            "opportunities.lamresearch.com",
            "careers.hhs.texas.gov",
        ),
        "successfactors",
        GateKind.ACCOUNT_REQUIRED,
    ),
    (("ats.rippling.com",), "rippling", GateKind.CONSENT_WALL),
    (("pinpointhq.com",), "pinpoint", GateKind.CONSENT_WALL),
    (("smartrecruiters.com",), "smartrecruiters", None),
    (("workable.com",), "workable", None),
    (("jobvite.com",), "jobvite", None),
    (("applytojob.com",), "jazzhr", None),
    (("bamboohr.com",), "bamboohr", None),
    (("paylocity.com",), "paylocity", None),
)

_PROVIDER_DEFAULT_GATES: Mapping[str, GateKind | None] = {
    "workday": GateKind.ACCOUNT_REQUIRED,
    "ukg": GateKind.ACCOUNT_REQUIRED,
    "icims": GateKind.ACCOUNT_REQUIRED,
    "oracle": GateKind.ACCOUNT_REQUIRED,
    "paycom": GateKind.ACCOUNT_REQUIRED,
    "successfactors": GateKind.ACCOUNT_REQUIRED,
    "rippling": GateKind.CONSENT_WALL,
    "pinpoint": GateKind.CONSENT_WALL,
}

RECOGNIZED_PROVIDERS: frozenset[str] = PUBLIC_API_PROVIDERS | frozenset(
    {
        "workday",
        "ukg",
        "icims",
        "oracle",
        "paycom",
        "successfactors",
        "rippling",
        "pinpoint",
        "smartrecruiters",
        "workable",
        "jobvite",
        "jazzhr",
        "bamboohr",
        "paylocity",
    }
)

_ATS_RELAY_DOMAINS: frozenset[str] = frozenset(
    {
        "ashbyhq.com",
        "greenhouse-mail.io",
        "greenhouse.io",
        "hire.lever.co",
        "lever.co",
        "myworkday.com",
        "smartrecruiters.com",
        "workablemail.com",
        "workable.com",
        "jobvite.com",
        "comeet-notifications.com",
        "icims.com",
        "applytojob.com",
        "bamboohr.com",
        "paylocity.com",
        "rippling.com",
        "pinpointhq.com",
        "indeed.com",
        "indeedemail.com",
        "talentnest.com",
    }
)

_COMPANY_ROLE_LOCAL_PARTS: frozenset[str] = frozenset(
    {
        "careers",
        "jobs",
        "talent",
        "recruiting",
        "recruitment",
        "hr",
        "hiring",
        "people",
        "no-reply",
        "noreply",
        "do-not-reply",
        "donotreply",
        "notifications",
        "notification",
        "team",
        "info",
        "apply",
    }
)

_NON_JOB_DOMAINS: frozenset[str] = frozenset(
    {
        "em.target.com",
        "targetnews",
        "nvidia.com",
        "concourseproject.com",
        "lowcostvet.org",
        "match.indeed.com",
        "militaryhire.com",
        "inceptionlabs.ai",
        "email.microsoft.com",
        "afvclub.com",
    }
)

_SUFFIX_PATTERNS: tuple[str, ...] = (
    "hiring team",
    "talent team",
    "recruiting team",
    "recruitment team",
    "talent acquisition",
    "recruiting",
    "recruitment",
    "careers",
    "talent",
    "hiring",
    "team",
    "corporation",
    "incorporated",
    "company",
    "limited",
    "inc",
    "llc",
    "llp",
    "corp",
    "ltd",
    "co",
)

_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_PHONE_RE = re.compile(r"(?<!\w)(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")
_OTP_RE = re.compile(
    r"\b((?:security\s+code|verification\s+code|one-time\s+(?:passcode|password|code)|passcode|otp|pin)"
    r"(?:\s*(?::|#|-|is)\s*|\s+(?=[A-Za-z0-9]*\d[A-Za-z0-9]{3,11}\b)))"
    r"([A-Za-z0-9]{4,12})\b",
    re.IGNORECASE,
)


def _short_hash(parts: list[str]) -> str:
    payload = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def normalize_employer_key(name: str) -> str:
    """Return a canonical lowercase company key, or '' when blank."""
    cleaned = re.sub(r"<[^>]+>", " ", name or "")
    cleaned = re.sub(r"[\"'`\u201c\u201d\u2018\u2019]+", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().lower()
    if not cleaned or ("@" in cleaned and " " not in cleaned):
        # Handle bare email local/domain fallback cleanly outside if needed.
        cleaned = cleaned.split("@", 1)[0] if "@" in cleaned else cleaned
    cleaned = re.sub(r"^the\s+", "", cleaned)
    cleaned = re.sub(r"^team\s+", "", cleaned)
    # Strip trailing parenthetical qualifiers like "(m/f/d)" or "(Austin)" if not the whole name.
    stripped = re.sub(r"[,.;:!?]+$", "", cleaned).strip()
    changed = True
    while changed and stripped:
        changed = False
        for suffix in _SUFFIX_PATTERNS:
            pattern = rf"(?:[\s,.-]+){re.escape(suffix)}\.?$"
            candidate = re.sub(pattern, "", stripped).strip(" ,.-")
            if candidate and candidate != stripped:
                stripped = candidate
                changed = True
                break
    stripped = re.sub(r"[^a-z0-9&/+.-]+", " ", stripped)
    stripped = re.sub(r"\s+", " ", stripped).strip(" .-")
    return stripped


def sanitize_role_url(url: str, provider: str = "") -> str:
    """Canonicalize an application URL and strip tracking/auth token query params."""
    raw = (url or "").strip()
    if not raw:
        return ""
    prov = provider.strip().lower()
    canonical = canonicalize_role_url(raw, prov)
    if not canonical:
        return ""
    try:
        parsed = urlsplit(canonical)
    except ValueError:
        return ""
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    is_gh_embed = any(k.lower() == "for" and v for k, v in pairs)
    kept: list[tuple[str, str]] = []
    for k, v in pairs:
        kl = k.lower()
        if kl in _SENSITIVE_QUERY_KEYS:
            if kl == "token" and is_gh_embed and v.isdigit():
                kept.append((k, v))
            continue
        kept.append((k, v))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(kept), ""))


def _host_matches(host: str, suffix: str) -> bool:
    return host == suffix or host.endswith(f".{suffix}")


def infer_ats_provider_and_gate(
    url: str,
    declared_provider: str = "",
    tenant: str = "",
    provider_job_id: str = "",
) -> tuple[str, str, str, GateKind | None]:
    """Infer ATS provider, tenant, job ID, and any structural portal gate.

    Sanitizes the URL before delegating to ``identify_role`` so sensitive tokens
    never enter RoleIdentity keys or downstream logs.
    """
    declared = (declared_provider or "").strip().lower()
    sanitized = sanitize_role_url(url, declared)
    ident: RoleIdentity = identify_role(sanitized, declared, tenant, provider_job_id)
    prov = ident.provider
    ten = ident.tenant
    job_id = ident.provider_job_id
    gate: GateKind | None = _PROVIDER_DEFAULT_GATES.get(prov)

    host = ""
    if sanitized:
        try:
            host = (urlsplit(sanitized).hostname or "").lower()
        except ValueError:
            host = ""

    if host:
        for suffixes, mapped_prov, mapped_gate in _PROVIDER_HOST_RULES:
            if any(_host_matches(host, s) for s in suffixes):
                prov = mapped_prov
                gate = mapped_gate
                break
        else:
            if not prov and "successfactors" in host:
                prov = "successfactors"
                gate = GateKind.ACCOUNT_REQUIRED

    if not prov and declared:
        prov = declared
        gate = _PROVIDER_DEFAULT_GATES.get(prov)

    # Extract tenant when recognized on Workable/Jobvite/JazzHR/Rippling/SmartRecruiters/etc.
    if sanitized and not ten:
        parts = [p for p in urlsplit(sanitized).path.split("/") if p]
        if prov in {"rippling", "jobvite", "smartrecruiters"} and parts:
            ten = parts[0].lower()
            if (
                prov == "smartrecruiters"
                and len(parts) >= 3
                and parts[0].lower() == "oneclick-ui"
                and parts[1].lower() == "company"
            ):
                ten = parts[2].lower()
        elif prov == "workable" and parts:
            ten = parts[0].lower()
        elif prov in {"workday", "jazzhr", "bamboohr", "pinpoint"} and host:
            ten = host.split(".")[0].lower()

    return prov, ten, job_id, gate


def build_role_key(
    *,
    employer_key: str,
    role_title: str = "",
    role_url: str = "",
    provider: str = "",
    tenant: str = "",
    provider_job_id: str = "",
    source_kind: str = "import",
) -> str:
    """Build a stable, sanitized role key even when role_url is empty."""
    sanitized = sanitize_role_url(role_url, provider)
    if sanitized:
        ident = identify_role(sanitized, provider, tenant, provider_job_id)
        if ident.key:
            return ident.key
    title_slug = _slug(role_title) or "role"
    src_slug = _slug(source_kind) or "import"
    return f"employer:{employer_key}:{title_slug}:{src_slug}"


def redact_sensitive_text(text: str) -> str:
    """Scrub emails, phone numbers, and OTP/security codes without mangling prose."""
    if not text:
        return ""
    out = _EMAIL_RE.sub("[redacted-email]", text)
    out = _PHONE_RE.sub("[redacted-phone]", out)
    out = _OTP_RE.sub(r"\1[redacted-code]", out)
    return out


_STANDARD_LABELS: frozenset[str] = frozenset(
    {
        "name",
        "full name",
        "first name",
        "last name",
        "preferred first name",
        "preferred name",
        "email",
        "email address",
        "phone",
        "phone number",
        "mobile phone",
        "resume",
        "resume/cv",
        "résumé or cv",
        "cv",
        "linkedin",
        "linkedin profile",
        "linkedin profile url",
        "linkedin url",
        "github",
        "github profile",
        "github url",
        "website",
        "portfolio url",
        "website / portfolio",
    }
)


def classify_requirement(
    label: str,
    field_type: str = "",
    requiredness: str = "unknown",
    *,
    category_hint: str = "",
) -> tuple[RequirementCategory, GateKind | None, bool]:
    """Classify one form question or portal condition.

    Returns ``(category, gate_kind, is_beyond_standard)``.
    """
    raw = (label or "").strip()
    low = raw.lower()
    ftype = (field_type or "").strip().lower()
    req = (requiredness or "unknown").strip().lower()

    # Exact category hint override when supplied from a trusted ledger (e.g., employer-asks.json).
    hint_map = {
        "identity": RequirementCategory.IDENTITY,
        "contact": RequirementCategory.CONTACT,
        "documents": RequirementCategory.DOCUMENTS,
        "professional_links": RequirementCategory.PROFESSIONAL_LINKS,
        "address": RequirementCategory.ADDRESS,
        "work_authorization": RequirementCategory.WORK_AUTHORIZATION,
        "citizenship": RequirementCategory.CITIZENSHIP_EXPORT,
        "location": RequirementCategory.LOCATION_SCHEDULE,
        "location_schedule": RequirementCategory.LOCATION_SCHEDULE,
        "compensation": RequirementCategory.COMPENSATION,
        "availability": RequirementCategory.AVAILABILITY,
        "experience": RequirementCategory.EXPERIENCE_TENURE,
        "performance_metrics": RequirementCategory.EXPERIENCE_TENURE,
        "technical_skills": RequirementCategory.TECHNICAL_SKILLS,
        "product_usage": RequirementCategory.TECHNICAL_SKILLS,
        "education": RequirementCategory.EDUCATION_GPA,
        "work_sample": RequirementCategory.WORK_SAMPLE_PORTFOLIO,
        "video": RequirementCategory.MEDIA_VOICE_VIDEO,
        "voicemail": RequirementCategory.MEDIA_VOICE_VIDEO,
        "motivation": RequirementCategory.BESPOKE_ESSAY,
        "personal_achievement": RequirementCategory.BESPOKE_ESSAY,
        "personal_history": RequirementCategory.BESPOKE_ESSAY,
        "personal_preference": RequirementCategory.BESPOKE_ESSAY,
        "ai_use": RequirementCategory.AI_POLICY,
        "ai_policy": RequirementCategory.AI_POLICY,
        "ai_restriction": RequirementCategory.AI_POLICY,
        "interview_recording": RequirementCategory.AI_POLICY,
        "interview_ai_note_taking": RequirementCategory.AI_POLICY,
        "privacy_acknowledgment": RequirementCategory.PRIVACY_LEGAL_CONSENT,
        "communications_consent": RequirementCategory.PRIVACY_LEGAL_CONSENT,
        "legal_attestation": RequirementCategory.PRIVACY_LEGAL_CONSENT,
        "legal_history": RequirementCategory.BACKGROUND_DRUG_MVR,
        "background_check": RequirementCategory.BACKGROUND_DRUG_MVR,
        "driving_eligibility": RequirementCategory.BACKGROUND_DRUG_MVR,
        "physical_conditions": RequirementCategory.PHYSICAL_REQUIREMENTS,
        "references": RequirementCategory.REFERENCES,
        "self_identification": RequirementCategory.DEMOGRAPHICS_EEO,
        "captcha": RequirementCategory.OTHER,
        "identity_verification": RequirementCategory.OTHER,
        "application_limit": RequirementCategory.OTHER,
    }

    if (
        "captcha" in low
        or "hcaptcha" in low
        or "recaptcha" in low
        or "datadome" in low
        or "human check" in low
        or "human verification" in low
        or "i'm not a robot" in low
        or "image verification" in low
        or "puzzle loop" in low
        or category_hint == "captcha"
    ):
        return RequirementCategory.OTHER, GateKind.CAPTCHA, True

    if (
        "security code" in low
        or "8-character" in low
        or "eight-character" in low
        or ("verification" in low and ("code" in low or "emailed" in low))
        or category_hint == "identity_verification"
    ):
        return RequirementCategory.OTHER, GateKind.EMAIL_VERIFICATION_CODE, True

    if (
        "workday" in low and ("account" in low or "sign in" in low or "sign-in" in low)
    ) or (
        (
            "create account" in low
            or "create an account" in low
            or "candidate sign-in" in low
            or "sign in" in low
            or "login" in low
        )
        and "linkedin" not in low
    ):
        return RequirementCategory.OTHER, GateKind.ACCOUNT_REQUIRED, True

    if (
        "application limit" in low
        or "application-limit" in low
        or category_hint == "application_limit"
    ):
        return RequirementCategory.OTHER, GateKind.APPLICATION_LIMIT, True

    # Standard fields (never beyond-standard unless special non-standard document/name ask).
    if low in _STANDARD_LABELS or low.rstrip("* ") in _STANDARD_LABELS:
        if "email" in low:
            return RequirementCategory.CONTACT, None, False
        if "phone" in low:
            return RequirementCategory.CONTACT, None, False
        if "resume" in low or "résumé" in low or low == "cv":
            return RequirementCategory.DOCUMENTS, None, False
        if (
            "linkedin" in low
            or "github" in low
            or "website" in low
            or "portfolio url" in low
        ):
            return RequirementCategory.PROFESSIONAL_LINKS, None, False
        return RequirementCategory.IDENTITY, None, False

    if "phonetic" in low and "name" in low:
        return RequirementCategory.IDENTITY, None, True
    if "preferred" in low and "name" in low:
        return RequirementCategory.IDENTITY, None, False
    if low in {"first name", "last name", "full name", "name"}:
        return RequirementCategory.IDENTITY, None, False
    if "cover letter" in low:
        return RequirementCategory.DOCUMENTS, None, req == "required"
    if (
        ("portfolio" in low and ("file" in low or ftype == "file"))
        or "code sample" in low
        or "work sample" in low
    ):
        return RequirementCategory.WORK_SAMPLE_PORTFOLIO, None, True
    if ftype == "file" or "attach" in low or "upload" in low:
        return (
            RequirementCategory.DOCUMENTS,
            None,
            "resume" not in low and req == "required",
        )
    if (
        "linkedin" in low
        or "github" in low
        or "twitter" in low
        or "x profile" in low
        or "website" in low
    ):
        return RequirementCategory.PROFESSIONAL_LINKS, None, False

    # Demographics / EEO.
    if (
        "gender" in low
        or "race" in low
        or "ethnicity" in low
        or "hispanic" in low
        or "veteran" in low
        or "disability" in low
        or "eeo" in low
        or "demographic" in low
    ):
        return RequirementCategory.DEMOGRAPHICS_EEO, None, False
    if "pronoun" in low:
        return RequirementCategory.DEMOGRAPHICS_EEO, None, req == "required"

    # Privacy, legal, AI consent.
    if (
        "privacy" in low
        or "terms of service" in low
        or "terms and conditions" in low
        or "gdpr" in low
        or "retain" in low
        or "texting consent" in low
        or "whatsapp" in low
        or "sms" in low
        or "nda" in low
        or "data processing" in low
        or "consent to processing" in low
    ):
        gate = (
            GateKind.CONSENT_WALL
            if req == "required"
            or "requires" in low
            or "consent" in low
            or "agree" in low
            else None
        )
        return RequirementCategory.PRIVACY_LEGAL_CONSENT, gate, True

    if (
        "ai " in low
        or "ai-" in low
        or "artificial intelligence" in low
        or "own words" in low
        or "note-taking" in low
        or "transcription" in low
        or ("interview" in low and "recording" in low)
    ):
        gate = (
            GateKind.CONSENT_WALL
            if (
                "consent" in low
                or "acknowledge" in low
                or "policy" in low
                or "recording" in low
            )
            else None
        )
        return RequirementCategory.AI_POLICY, gate, True

    if (
        "citizen" in low
        or "export" in low
        or "itar" in low
        or "sanction" in low
        or "nationality" in low
        or "security clearance" in low
    ):
        gate = (
            GateKind.CONSENT_WALL
            if ("export-control" in low or "statement" in low or "acknowledge" in low)
            else None
        )
        return RequirementCategory.CITIZENSHIP_EXPORT, gate, True

    if (
        "sponsor" in low
        or "authorized to work" in low
        or "work authorization" in low
        or "visa" in low
        or "eligible to work" in low
    ):
        return RequirementCategory.WORK_AUTHORIZATION, None, True

    if (
        "street address" in low
        or "mailing address" in low
        or "home address" in low
        or "postal code" in low
        or "zip code" in low
        or re.search(r"\b(address|zip|county)\b", low)
    ):
        return RequirementCategory.ADDRESS, None, True

    if (
        "salary" in low
        or "compensation" in low
        or "ote" in low
        or "hourly" in low
        or "desired pay" in low
        or "expected pay" in low
        or "pay range" in low
    ):
        return RequirementCategory.COMPENSATION, None, True

    if (
        "start date" in low
        or "earliest" in low
        or "notice period" in low
        or "available to start" in low
        or "availability" in low
    ):
        return RequirementCategory.AVAILABILITY, None, True

    if (
        "voicemail" in low
        or "audio recording" in low
        or "self-introduction video" in low
        or "record a video" in low
        or "loom" in low
        or ("video" in low and "interview" not in low)
    ):
        return RequirementCategory.MEDIA_VOICE_VIDEO, None, True

    if (
        "background check" in low
        or "drug" in low
        or "motor vehicle" in low
        or "driver" in low
        or "investigation authorization" in low
        or "release of claims" in low
        or "non-compete" in low
        or "noncompete" in low
        or "discharged" in low
        or "terminated" in low
    ):
        gate = (
            GateKind.CONSENT_WALL
            if ("authorization" in low or "release" in low or "agreement" in low)
            else None
        )
        return RequirementCategory.BACKGROUND_DRUG_MVR, gate, True

    if (
        "lift" in low
        or "50 lb" in low
        or "55 lb" in low
        or "cleanroom" in low
        or "ppe" in low
        or "heights" in low
        or "physical" in low
    ):
        return RequirementCategory.PHYSICAL_REQUIREMENTS, None, True

    if "reference" in low:
        return RequirementCategory.REFERENCES, None, True

    if (
        "gpa" in low
        or "graduation" in low
        or "degree" in low
        or "high school" in low
        or "ged" in low
        or "university" in low
        or "college" in low
    ):
        return RequirementCategory.EDUCATION_GPA, None, True

    if (
        "onsite" in low
        or "on-site" in low
        or "in-office" in low
        or "hybrid" in low
        or "relocat" in low
        or "commute" in low
        or "travel" in low
        or "shift" in low
        or "days a week" in low
        or "days per week" in low
        or "located in" in low
        or "currently reside" in low
        or "current location" in low
        or low == "location"
        or "workplace" in low
    ):
        return RequirementCategory.LOCATION_SCHEDULE, None, True

    if (
        "years of" in low
        or "how many years" in low
        or "quota" in low
        or "calls per day" in low
        or "call volume" in low
        or "tenure" in low
    ):
        return RequirementCategory.EXPERIENCE_TENURE, None, True

    if (
        "why " in low
        or "tell us" in low
        or "describe" in low
        or "favorite" in low
        or "dog" in low
        or ftype in {"longtext", "textarea"}
    ):
        return RequirementCategory.BESPOKE_ESSAY, None, True

    if category_hint and category_hint in hint_map:
        cat = hint_map[category_hint]
        beyond = cat not in {
            RequirementCategory.IDENTITY,
            RequirementCategory.CONTACT,
            RequirementCategory.PROFESSIONAL_LINKS,
            RequirementCategory.DEMOGRAPHICS_EEO,
        }
        return cat, None, beyond

    if (
        ftype in {"multiselect", "valueselect", "boolean", "date", "number"}
        or req == "required"
    ):
        return RequirementCategory.OTHER, None, True
    return RequirementCategory.OTHER, None, False


@dataclass(frozen=True)
class EmployerRecord:
    """Normalized company profile aggregated across boards, apps, and inbox."""

    employer_key: str
    display_name: str
    board_tokens: tuple[str, ...] = ()
    providers: tuple[str, ...] = ()
    domains: tuple[str, ...] = ()
    first_seen: str = ""
    last_seen: str = ""

    def __post_init__(self) -> None:
        key = normalize_employer_key(self.employer_key)
        if not key:
            raise ValueError("employer_key cannot be empty")
        tokens = tuple(
            sorted({t.strip().lower() for t in self.board_tokens if t and ":" in t})
        )
        provs = tuple(sorted({p.strip().lower() for p in self.providers if p}))
        doms = tuple(sorted({d.strip().lower() for d in self.domains if d}))
        object.__setattr__(self, "employer_key", key)
        object.__setattr__(self, "display_name", (self.display_name or key).strip())
        object.__setattr__(self, "board_tokens", tokens)
        object.__setattr__(self, "providers", provs)
        object.__setattr__(self, "domains", doms)


@dataclass(frozen=True)
class HrContact:
    """An HR, recruiting, or ATS sender observed in mail metadata."""

    employer_key: str
    email: str
    display_name: str = ""
    domain: str = ""
    channel_kind: ChannelKind = ChannelKind.ATS_RELAY
    first_seen: str = ""
    last_seen: str = ""
    message_count: int = 1
    sample_subject: str = ""
    contact_id: str = ""

    def __post_init__(self) -> None:
        key = normalize_employer_key(self.employer_key)
        if not key:
            raise ValueError("employer_key cannot be empty")
        addr = (self.email or "").strip().lower()
        if not addr or "@" not in addr:
            raise ValueError("HrContact requires a valid email")
        dom = (self.domain or addr.split("@", 1)[1]).strip().lower()
        cid = self.contact_id or _short_hash([key, addr])
        object.__setattr__(self, "employer_key", key)
        object.__setattr__(self, "email", addr)
        object.__setattr__(self, "domain", dom)
        object.__setattr__(self, "channel_kind", ChannelKind(self.channel_kind))
        object.__setattr__(
            self,
            "display_name",
            redact_sensitive_text((self.display_name or "").strip()),
        )
        object.__setattr__(
            self,
            "sample_subject",
            redact_sensitive_text((self.sample_subject or "").strip()),
        )
        object.__setattr__(self, "contact_id", cid)


@dataclass(frozen=True)
class RequirementObservation:
    """One observed pre-submit requirement, form field, or gate on a role."""

    employer_key: str
    employer_name: str
    role_key: str
    role_title: str = ""
    role_url: str = ""
    provider: str = ""
    category: RequirementCategory = RequirementCategory.OTHER
    label: str = ""
    field_type: str = ""
    requiredness: str = "unknown"
    gate_kind: GateKind | None = None
    is_beyond_standard: bool = False
    source_kind: str = ""
    source_ref: str = ""
    observed_at: str = ""
    observation_id: str = ""

    def __post_init__(self) -> None:
        key = normalize_employer_key(self.employer_key)
        if not key:
            raise ValueError("employer_key cannot be empty")
        clean_url = sanitize_role_url(self.role_url, self.provider)
        clean_label = redact_sensitive_text((self.label or "").strip())
        cat = RequirementCategory(self.category)
        gate = GateKind(self.gate_kind) if self.gate_kind else None
        rkey = (self.role_key or "").strip() or build_role_key(
            employer_key=key,
            role_title=self.role_title,
            role_url=clean_url,
            provider=self.provider,
            source_kind=self.source_kind or "import",
        )
        oid = self.observation_id or _short_hash(
            [
                key,
                rkey,
                cat.value,
                clean_label,
                gate.value if gate else "",
                self.source_kind,
            ]
        )
        object.__setattr__(self, "employer_key", key)
        object.__setattr__(self, "employer_name", (self.employer_name or key).strip())
        object.__setattr__(self, "role_key", rkey)
        object.__setattr__(self, "role_url", clean_url)
        object.__setattr__(self, "category", cat)
        object.__setattr__(self, "label", clean_label)
        object.__setattr__(self, "gate_kind", gate)
        object.__setattr__(self, "observation_id", oid)


@dataclass(frozen=True)
class FormSnapshot:
    """Result of checking one role's live or structurally inferred form schema."""

    role_key: str
    employer_key: str
    provider: str
    role_title: str
    role_url: str
    status: str
    error: str = ""
    field_count: int = 0
    beyond_standard_count: int = 0
    gates: tuple[GateKind, ...] = ()
    fetched_at: str = ""

    def __post_init__(self) -> None:
        key = normalize_employer_key(self.employer_key)
        if not key:
            raise ValueError("employer_key cannot be empty")
        clean_url = sanitize_role_url(self.role_url, self.provider)
        norm_gates = tuple(
            sorted({GateKind(g) for g in self.gates}, key=lambda g: g.value)
        )
        object.__setattr__(self, "employer_key", key)
        object.__setattr__(self, "role_url", clean_url)
        object.__setattr__(self, "gates", norm_gates)


@dataclass(frozen=True)
class EmployerSummary:
    """Aggregated view of one employer's gates, requirements, and HR channels."""

    employer: EmployerRecord
    gates: tuple[GateKind, ...]
    role_count: int
    observation_count: int
    beyond_standard_categories: tuple[str, ...]
    direct_contacts: tuple[HrContact, ...]
    team_contacts: tuple[HrContact, ...]
    ats_contacts: tuple[HrContact, ...]
    friction_tier: FrictionTier


def summarize_employer(
    employer: EmployerRecord,
    contacts: Iterable[HrContact],
    observations: Iterable[RequirementObservation],
    snapshots: Iterable[FormSnapshot],
) -> EmployerSummary:
    """Compute friction tier, gates, and grouped HR channels for an employer."""
    contact_list = sorted(contacts, key=lambda c: (-c.message_count, c.email))
    obs_list = list(observations)
    snap_list = list(snapshots)

    role_keys = {o.role_key for o in obs_list if o.role_key} | {
        s.role_key for s in snap_list if s.role_key
    }
    blocking_found: set[GateKind] = set()
    clean_snapshot_seen = False
    for snap in snap_list:
        if snap.status == "ok":
            clean_snapshot_seen = True
        for g in snap.gates:
            if g in BLOCKING_GATES:
                blocking_found.add(g)
            elif g is GateKind.NONE and snap.status == "ok":
                clean_snapshot_seen = True
    for obs in obs_list:
        if obs.gate_kind in BLOCKING_GATES:
            blocking_found.add(obs.gate_kind)
        elif obs.gate_kind is GateKind.NONE:
            clean_snapshot_seen = True

    if blocking_found:
        gates = tuple(sorted(blocking_found, key=lambda g: g.value))
    elif clean_snapshot_seen:
        gates = (GateKind.NONE,)
    else:
        gates = ()

    beyond_cats = tuple(
        sorted(
            {
                obs.category.value
                for obs in obs_list
                if obs.is_beyond_standard and obs.requiredness != "optional"
            }
        )
    )
    has_heavy_custom = any(
        obs.is_beyond_standard
        and obs.requiredness != "optional"
        and obs.category not in ROUTINE_POLICY_CATEGORIES
        for obs in obs_list
    )

    if blocking_found:
        tier = FrictionTier.GATED
    elif has_heavy_custom:
        tier = FrictionTier.CUSTOM_QUESTIONS
    else:
        tier = FrictionTier.LOW_FRICTION

    return EmployerSummary(
        employer=employer,
        gates=gates,
        role_count=len(role_keys),
        observation_count=len(obs_list),
        beyond_standard_categories=beyond_cats,
        direct_contacts=tuple(
            c for c in contact_list if c.channel_kind is ChannelKind.DIRECT_PERSON
        ),
        team_contacts=tuple(
            c for c in contact_list if c.channel_kind is ChannelKind.COMPANY_RECRUITING
        ),
        ats_contacts=tuple(
            c for c in contact_list if c.channel_kind is ChannelKind.ATS_RELAY
        ),
        friction_tier=tier,
    )


_GH_SEC_CODE_RE = re.compile(
    r"security code for your application to\s+(.+?)\s*$", re.IGNORECASE
)
_SUBJ_COMPANY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"security code for your application to\s+(.+)$", re.IGNORECASE),
    re.compile(r"thank you for your interest in\s+([A-Z0-9][^,!–|]+)", re.IGNORECASE),
    re.compile(
        r"(?:thank you|thanks)\s+for\s+(?:applying|your application)\s+(?:to|at|with)\s+([A-Z0-9][^,!–|]+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:your application|application update|follow up|update)\s+(?:from|at|to|with)\s+([A-Z0-9][^,|]+)",
        re.IGNORECASE,
    ),
    re.compile(r"invitation to interview with\s+([A-Z0-9][^,!–|]+)", re.IGNORECASE),
    re.compile(r"your application\s*[|–-]\s*([A-Z0-9][^,|]+)", re.IGNORECASE),
    re.compile(
        r"^([A-Z0-9][A-Za-z0-9 .&-]{1,30}?)\s+(?:Application Update|Hiring Team|Recruiting)",
        re.IGNORECASE,
    ),
)


def _match_alias(raw_name: str, alias_map: Mapping[str, str] | None) -> str:
    key = normalize_employer_key(raw_name)
    if not key:
        return ""
    if alias_map and key in alias_map:
        return alias_map[key]
    return key


def extract_hr_contact(
    from_header: str,
    *,
    reply_to_header: str = "",
    subject: str = "",
    date_iso: str = "",
    candidate_emails: frozenset[str] = frozenset(),
    alias_map: Mapping[str, str] | None = None,
) -> tuple[HrContact | None, GateKind | None, str]:
    """Extract normalized HrContact and optional Greenhouse verification gate.

    Only consumes sender headers, subject, and timestamp; never accepts body or snippet.
    """
    candidates = getaddresses([from_header or "", reply_to_header or ""])
    display_name, addr = ("", "")
    for disp, raw_addr in candidates:
        low = raw_addr.strip().lower()
        if low and "@" in low and low not in candidate_emails:
            display_name, addr = disp.strip(), low
            break
    if not addr:
        disp, raw_addr = parseaddr(from_header or "")
        addr = raw_addr.strip().lower()
        display_name = disp.strip()
    if not addr or "@" not in addr or addr in candidate_emails:
        return None, None, ""

    local, domain = addr.split("@", 1)
    if any(_host_matches(domain, bad) or bad in addr for bad in _NON_JOB_DOMAINS):
        return None, None, ""

    subj_clean = redact_sensitive_text((subject or "").strip())
    disp_clean = redact_sensitive_text(display_name)
    is_relay = any(_host_matches(domain, r) for r in _ATS_RELAY_DOMAINS)

    # Detect conditional Greenhouse security code gate without touching any OTP value.
    detected_gate: GateKind | None = None
    emp_raw = ""
    m_sec = _GH_SEC_CODE_RE.search(subj_clean)
    if m_sec and ("greenhouse" in domain or "security code" in subj_clean.lower()):
        detected_gate = GateKind.EMAIL_VERIFICATION_CODE
        emp_raw = m_sec.group(1).strip()

    is_role_sender = (
        local in _COMPANY_ROLE_LOCAL_PARTS
        or "+" in local
        or any(
            local.startswith(prefix)
            for prefix in (
                "careers",
                "jobs",
                "talent",
                "recruit",
                "no-reply",
                "noreply",
                "notifications",
                "hr-",
            )
        )
    )

    if is_relay:
        channel = ChannelKind.ATS_RELAY
    elif is_role_sender:
        channel = ChannelKind.COMPANY_RECRUITING
    else:
        channel = ChannelKind.DIRECT_PERSON

    if not emp_raw and alias_map and not is_relay and domain in alias_map:
        emp_raw = alias_map[domain]

    if not emp_raw:
        for pat in _SUBJ_COMPANY_PATTERNS:
            m = pat.search(subj_clean)
            if m:
                emp_raw = m.group(1).strip(" .:-!")
                break

    if (
        not emp_raw
        and (is_relay or is_role_sender)
        and disp_clean
        and not any(
            bad in disp_clean.lower()
            for bad in (
                "no-reply",
                "noreply",
                "greenhouse",
                "recruiting team at",
                "notifications",
            )
        )
    ):
        emp_raw = disp_clean

    if not emp_raw and not is_relay:
        dom_label = domain.split(".")[0]
        if dom_label not in {"gmail", "yahoo", "outlook", "hotmail", "icloud", "me"}:
            emp_raw = dom_label

    # Strip trailing ", Garo" or " - <Role>" noise from subject-extracted company names.
    emp_raw = re.split(r"\s+(?:as|for|to apply|,)\s+", emp_raw, maxsplit=1)[0].strip()
    employer_key = _match_alias(emp_raw, alias_map)
    if not employer_key and alias_map and not is_relay and domain in alias_map:
        employer_key = alias_map[domain]
    if not employer_key or employer_key in {
        "no-reply",
        "noreply",
        "donotreply",
        "notifications",
        "garo",
        "garo vartabedian",
    }:
        return None, detected_gate, ""

    contact = HrContact(
        employer_key=employer_key,
        email=addr,
        display_name=disp_clean,
        domain=domain,
        channel_kind=channel,
        first_seen=date_iso,
        last_seen=date_iso,
        message_count=1,
        sample_subject=subj_clean[:160],
    )
    return contact, detected_gate, employer_key
