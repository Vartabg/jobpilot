"""Inspect application-question metadata without reading or sending applicant answers.

The public scanner only performs a bounded GET against Greenhouse's documented
Job Board API. It cannot inspect later steps, sign-in gates, or employer use of
information. Findings describe requests, not legality or employer intent.
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Mapping, Sequence
from html.parser import HTMLParser
from typing import Literal, TypedDict
from urllib.parse import parse_qs, urlsplit

import requests

Coverage = Literal["visible_form", "public_ats_form"]
Optionality = Literal[
    "required", "optional", "unknown", "response_required_disclosure_optional"
]


class FieldMetadata(TypedDict):
    label: str
    required: bool | None
    options: list[str]
    description: str


class Finding(TypedDict):
    label: str
    category: str
    required: bool | None
    options: list[str]
    optionality: Optionality
    concern_level: Literal["review", "notice"]
    employer_stated_purpose: str
    explanation: str


class PrivacyReport(TypedDict):
    source_url: str
    coverage: Coverage
    complete: bool
    field_count: int
    fields: list[FieldMetadata]
    findings: list[Finding]
    notices: list[str]
    coverage_note: str
    title: str


class PrivacyScanError(ValueError):
    """A safe, actionable scan error; never contains response bodies or answers."""


TIMEOUT = 10
MAX_BYTES = 2 * 1024 * 1024
MAX_FIELDS = 500
MAX_OPTIONS = 500
_SESSION = requests.Session()
_SESSION.headers.update({"Accept": "application/json", "User-Agent": "JobPilot/1.0"})
_HOSTS = {"boards.greenhouse.io", "job-boards.greenhouse.io"}
_SLUG = re.compile(r"[A-Za-z0-9_-]{1,100}\Z")
_JOB_ID = re.compile(r"[0-9]{1,20}\Z")
_DECLINE = re.compile(
    r"\b(?:prefer\s+not\s+to|decline\s+(?:to\s+)?(?:answer|state|self|disclose)|"
    r"(?:do\s+not|don['’]t)\s+(?:wish|want)\s+to|choose\s+not\s+to|rather\s+not\s+(?:say|answer))\b",
    re.I,
)
_LIMITATION = (
    "Only the available application-field metadata was inspected. Later steps, "
    "sign-in screens, embedded or conditional questions, and employer data use may not be visible. "
    "No findings does not establish that a job requests no personal information."
)


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _plain(value: str) -> str:
    parser = _Text()
    parser.feed(html.unescape(value))
    return " ".join(" ".join(parser.parts).split())


def _text(value: object, limit: int, name: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > limit:
        raise PrivacyScanError(f"{name} must be text within the scan size limit.")
    return value.strip()


def _source_url(url: object) -> str:
    """Retain context, dropping query strings/fragments that may contain tokens."""
    value = _text(url, 4096, "Source URL")
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
            return ""
        # Deliberately do not retain arbitrary URL parameters, even on known ATS hosts.
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    except ValueError:
        return ""


def _normalize_field(raw: object) -> FieldMetadata:
    if not isinstance(raw, Mapping):
        raise PrivacyScanError("Each field must contain question metadata.")
    label = _text(raw.get("label"), 4000, "Field label")
    if not label:
        raise PrivacyScanError("Each field needs a non-empty label.")
    required = raw.get("required")
    if required is not None and not isinstance(required, bool):
        raise PrivacyScanError("Field requiredness must be true, false, or unknown.")
    options = raw.get("options", [])
    if not isinstance(options, list) or len(options) > MAX_OPTIONS:
        raise PrivacyScanError("Field options exceed the scan size limit or are not a list.")
    # Only the explicit metadata allowlist is copied. Keys such as value, answer,
    # selected, checked, files, and defaults are never read or returned.
    return FieldMetadata(
        label=label,
        required=required,
        options=[_text(option, 4000, "Option label") for option in options],
        description=_text(raw.get("description", ""), 12000, "Field description"),
    )


def _optionality(field: FieldMetadata) -> Optionality:
    if field["required"] is None:
        return "unknown"
    if field["required"] is False:
        return "optional"
    if any(_DECLINE.search(option) for option in field["options"]):
        return "response_required_disclosure_optional"
    return "required"


# Detection is confined to question labels. A job description or a purpose
# mentioning demographic information does not create a request on its own.
_RULES: tuple[tuple[str, str, str, str], ...] = (
    ("gender_identity", r"\b(?:gender|sex|transgender|nonbinary|non-binary|(?:your|preferred) pronouns)\b|^pronouns\s*[:*]?$", "review", "Requests sex, gender, gender identity, or pronoun information."),
    ("sexual_orientation", r"\b(?:sexual (?:orientation|preferences?)|sexuality|lgbtq?\+?)\b", "review", "Requests sexual orientation information."),
    ("race_ethnicity", r"\b(?:race|racial|ethnicity|ethnic|hispanic|latino|latina)\b", "review", "Requests race or ethnicity information."),
    ("health_disability", r"\b(?:disabilit\w*|disabled|medical (?:condition|history|record)|health (?:condition|history|record)|mental health|pregnan\w*|diagnosis|diagnoses)\b", "review", "Requests health or disability information."),
    ("religion_belief", r"\b(?:religion|religious|faith affiliation|spiritual beliefs?|belief system)\b", "review", "Requests religious affiliation or beliefs."),
    ("political_affiliation", r"\b(?:political (?:affiliation|party|belief|view)|party affiliation|voting history|who did you vote)\b", "review", "Requests political affiliation, beliefs, or voting information."),
    ("union_membership", r"\b(?:(?:trade|labor|labour) union|union (?:membership|member|affiliation)|member of (?:a |an |any )?union)\b", "review", "Requests trade or labor union affiliation."),
    ("nationality_citizenship", r"\b(?:nationality|citizenship|citizen|country of birth|place of birth)\b", "review", "Requests nationality, citizenship, or birthplace information; work-authorization eligibility is a separate question."),
    ("veteran_status", r"\b(?:veteran|military status|military service|armed forces)\b", "notice", "Requests veteran or military-service information."),
    ("government_id", r"\b(?:social security|ssn|national (?:id|identity|insurance)|government (?:id|identification)|passport|driver'?s? licen[cs]e|tax (?:id|identification))\b", "review", "Asks about a government identifier or identity document; inspect whether it requests a number, copy, or eligibility only."),
    ("date_of_birth", r"\b(?:date of birth|birth ?date|dob|how old|your age|age range)\b|^age\s*[:*]?$", "review", "Requests age or date-of-birth information."),
    ("salary_history", r"\b(?:(?:current|previous|prior|past|last|historical) (?:base )?(?:salary|pay|wage|compensation)|(?:salary|pay|wage|compensation) history)\b", "review", "Requests current or past compensation rather than a desired range."),
    ("financial_credentials", r"\b(?:bank (?:account|details)|routing number|credit card|debit card|financial (?:account|credential)|payment details)\b", "review", "Requests financial account details or credentials."),
    ("marital_family", r"\b(?:marital|married|spouse|family (?:status|plan)|children|dependents|childcare|plan(?:ning)? to (?:have|start) (?:children|a family))\b", "review", "Requests marital, family, or caregiving information."),
    ("social_account_access", r"(?:\b(?:social (?:media|account)|facebook|instagram|linkedin|twitter|tiktok)\b.{0,60}\b(?:password|credentials|access|authoriz\w*|connect|login|log in|sign in)\b|\b(?:password|credentials|access|authoriz\w*|connect|login|log in|sign in)\b.{0,60}\b(?:social (?:media|account)|facebook|instagram|linkedin|twitter|tiktok)\b)", "review", "Requests access or authentication to a social account; review the requested permissions."),
    ("media_request", r"(?:\b(?:submit|upload|provide|attach|send)\b.{0,40}\b(?:video|audio|voice)\b|^(?:video|audio|voice)\s*[:*]?$|\b(?:photograph|headshot|selfie|photo|video (?:intro\w*|interview|record\w*|response|submission)|record(?:ed|ing)? (?:a |an |your )?(?:video|audio|voice)|audio (?:record\w*|response|submission)|voice (?:record\w*|sample))\b)", "review", "Requests a photo or recorded video, audio, or voice material."),
    ("screening_authorization", r"\b(?:background(?: and employment verification)? (?:check|screen\w*)|pre[- ]employment background|employment verification(?: screening)?|criminal (?:record|history|check)|credit check|drug (?:test|screen\w*)|reference checks?)\b", "notice", "Mentions a screening or reference check; inspect any authorization wording before agreeing."),
    ("account_gate", r"\b(?:create (?:an? |your )?account|sign ?in|log ?in|register (?:an? |your )?account|account registration|password)\b", "notice", "An account or sign-in step is present; later form questions may be hidden behind it."),
)
_COMPILED_RULES = tuple((category, re.compile(pattern, re.I), level, explanation) for category, pattern, level, explanation in _RULES)
_PRECISE_ADDRESS = re.compile(r"\b(?:(?:street|physical|legal|residential|home|mailing|full|current|permanent) address|address line\s*[12]|address\s*[12]|your address)\b|^(?:address|street)\s*[:*]?$", re.I)
_COARSE_LOCATION = re.compile(r"\b(?:location|city|states?|provinces?|country|countries|postal code|zip code|zip)\b|\bwhere (?:do you (?:live|reside)|are you (?:based|located))\b", re.I)
_COORDINATES = re.compile(r"\b(?:latitude|longitude|precise (?:geolocation|location)|gps coordinates)\b", re.I)


# Occupational questions about supporting other people are not requests for the
# applicant's own family/medical details. Keep this exception narrow and retain
# a finding when the same label also asks for a personal disclosure.
_OCCUPATIONAL_PEOPLE = re.compile(
    r"\b(?:experience|background|experienced)\b.{0,45}"
    r"\b(?:working with|supporting|serving|teaching|treating|caring for|assisting|helping)\b"
    r".{0,30}\b(?:children|families|parents|dependents|customers|patients|applicants|people|individuals|clients|students)\b",
    re.I,
)
_PERSONAL_FAMILY = re.compile(
    r"\b(?:your|own) (?:children|dependents|spouse|marital|family|childcare)\b|"
    r"\b(?:have|having) (?:any |a |your own )?(?:children|dependents)\b|"
    r"\b(?:married|pregnan\w*|family planning)\b", re.I,
)
_PERSONAL_HEALTH = re.compile(
    r"\b(?:your|own|personal) (?:medical|health|disabilit\w*|diagnos\w*)\b|"
    r"\byou (?:have|had|have had) (?:a |an |any |a history of )?"
    r"(?:medical|health|disabilit\w*|diagnos\w*)\b|"
    r"\b(?:are you|were you|you are|you were) (?:disabled|pregnant|diagnosed)\b",
    re.I,
)


def _occupational_reference(category: str, label: str) -> bool:
    if category not in {"marital_family", "health_disability"}:
        return False
    context = _OCCUPATIONAL_PEOPLE.search(label)
    if context is None:
        return False
    if category == "marital_family":
        return not _PERSONAL_FAMILY.search(label)
    # Only suppress health terms that describe the people receiving support.
    # "medical history working with ..." cannot satisfy this placement check.
    describes_other_people = re.search(
        r"\b(?:customers|patients|applicants|people|individuals|clients|students|children) "
        r"(?:with|who have|living with) (?:a |an |any )?"
        r"(?:disabilit\w*|medical conditions?|health conditions?|mental health|diagnos\w*)\b",
        label[context.start():], re.I,
    )
    return bool(describes_other_people and not _PERSONAL_HEALTH.search(label))


def analyze_fields(
    fields: Sequence[Mapping[str, object]],
    source_url: str = "",
    coverage: Coverage = "visible_form",
    notices: Sequence[str] | None = None,
) -> PrivacyReport:
    """Analyze only labels, requiredness, option labels, and employer help text.

    Unknown requiredness remains unknown, even when a decline option is visible.
    The report never promises completeness or establishes an employer's purpose.
    """
    if coverage not in {"visible_form", "public_ats_form"}:
        raise PrivacyScanError("Unsupported scan coverage.")
    if not isinstance(fields, (list, tuple)) or len(fields) > MAX_FIELDS:
        raise PrivacyScanError(f"Provide at most {MAX_FIELDS} application fields.")
    normalized = [_normalize_field(field) for field in fields]
    findings: list[Finding] = []
    for field in normalized:
        label = field["label"]
        matches: list[tuple[str, str, str]] = []
        if _PRECISE_ADDRESS.search(label) and not re.search(r"\b(?:e-?mail|website|ip) address\b", label, re.I):
            matches.append(("precise_address", "review", "Requests a street, legal, home, or full address rather than only a work location."))
        elif _COORDINATES.search(label):
            matches.append(("precise_location", "review", "Requests precise location coordinates."))
        elif _COARSE_LOCATION.search(re.sub(r"\bunited states(?: of america)?\b", "", label, flags=re.I)) and not re.search(r"\b(?:state your|country of (?:citizenship|birth))\b", label, re.I):
            matches.append(("coarse_location", "notice", "Requests a city, region, postal code, country, or general location; no street address is established by this label."))
        matches.extend((category, level, explanation) for category, pattern, level, explanation in _COMPILED_RULES if pattern.search(label) and not _occupational_reference(category, label))
        for category, level, explanation in matches:
            findings.append(Finding(
                label=label, category=category, required=field["required"],
                options=list(field["options"]), optionality=_optionality(field),
                concern_level=level, employer_stated_purpose=field["description"],
                explanation=explanation,
            ))
    if notices is not None and (not isinstance(notices, (list, tuple)) or len(notices) > 30):
        raise PrivacyScanError("Scan notices exceed the size limit or are not a list.")
    report_notices = [_text(notice, 12000, "Scan notice") for notice in notices or []]
    if any(finding["optionality"] == "unknown" for finding in findings):
        report_notices.append("Requiredness is unknown for one or more flagged fields; verify it in the application form.")
    report_notices.append(_LIMITATION)
    return PrivacyReport(
        source_url=_source_url(source_url), coverage=coverage, complete=False,
        field_count=len(normalized), fields=normalized, findings=findings,
        notices=list(dict.fromkeys(report_notices)), coverage_note=_LIMITATION, title="",
    )


def _greenhouse_target(url: str) -> tuple[str, str]:
    value = _text(url, 4096, "Job URL")
    try:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or parsed.hostname not in _HOSTS or parsed.username or parsed.password or parsed.port not in {None, 443}:
            raise ValueError
        path = parsed.path.rstrip("/")
        match = re.fullmatch(r"/([A-Za-z0-9_-]+)/jobs/([0-9]+)", path)
        if match:
            board, job_id = match.groups()
        elif path == "/embed/job_app":
            query = parse_qs(parsed.query, max_num_fields=30)
            if len(query.get("for", [])) != 1 or len(query.get("token", [])) != 1:
                raise ValueError
            board, job_id = query["for"][0], query["token"][0]
        else:
            raise ValueError
        if not _SLUG.fullmatch(board) or not _JOB_ID.fullmatch(job_id):
            raise ValueError
        return board, job_id
    except ValueError:
        raise PrivacyScanError(
            "Use a direct HTTPS Greenhouse job URL or its Greenhouse embedded application URL. "
            "Other ATS sites and employer career-page wrappers need a visible-form scan; their board is not guessed."
        ) from None


def _fetch_job(board: str, job_id: str) -> object:
    # No user-provided origin or path can reach the network layer.
    url = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}?questions=true"
    try:
        response = _SESSION.get(url, timeout=TIMEOUT, allow_redirects=False, stream=True)
    except requests.RequestException:
        raise PrivacyScanError("The public Greenhouse form is unreachable. Try again later.") from None
    try:
        if 300 <= response.status_code < 400:
            raise PrivacyScanError("The public Greenhouse endpoint redirected and was not followed. The scan is incomplete.")
        if response.status_code != 200:
            raise PrivacyScanError(f"The public Greenhouse form is unavailable (HTTP {response.status_code}). The scan is incomplete.")
        declared = response.headers.get("Content-Length", "")
        if declared.strip().isdigit() and int(declared) > MAX_BYTES:
            raise PrivacyScanError("The public Greenhouse response exceeds the scan size limit.")
        buffer = bytearray()
        try:
            for chunk in response.iter_content(chunk_size=65536):
                if chunk:
                    buffer.extend(chunk)
                    if len(buffer) > MAX_BYTES:
                        raise PrivacyScanError("The public Greenhouse response exceeds the scan size limit.")
        except requests.RequestException:
            raise PrivacyScanError("The public Greenhouse response was interrupted. The scan is incomplete.") from None
        try:
            return json.loads(buffer)
        except (ValueError, UnicodeDecodeError):
            raise PrivacyScanError("The public Greenhouse form returned an unexpected format. The scan is incomplete.") from None
    finally:
        response.close()


def _question_metadata(question: object, purpose: str = "") -> FieldMetadata:
    if not isinstance(question, Mapping) or not isinstance(question.get("label"), str) or not question["label"].strip():
        raise PrivacyScanError("The public Greenhouse questions have an unexpected format. The scan is incomplete.")
    options: list[str] = []
    api_fields = question.get("fields", [])
    if not isinstance(api_fields, list):
        raise PrivacyScanError("The public Greenhouse fields have an unexpected format. The scan is incomplete.")
    for api_field in api_fields:
        if not isinstance(api_field, Mapping):
            raise PrivacyScanError("The public Greenhouse fields have an unexpected format. The scan is incomplete.")
        # API 'values' are option definitions, never applicant responses. Only
        # their label is read; IDs/defaults/selected values are not retained.
        values = api_field.get("values", [])
        if not isinstance(values, list):
            raise PrivacyScanError("The public Greenhouse options have an unexpected format. The scan is incomplete.")
        for option in values:
            if isinstance(option, Mapping) and isinstance(option.get("label"), str):
                options.append(_plain(option["label"]))
    answer_options = question.get("answer_options", [])
    if not isinstance(answer_options, list):
        raise PrivacyScanError("The public Greenhouse options have an unexpected format. The scan is incomplete.")
    for option in answer_options:
        if isinstance(option, Mapping) and isinstance(option.get("label"), str):
            options.append(_plain(option["label"]))
    description = question.get("description", purpose)
    if not isinstance(description, str):
        description = purpose
    return _normalize_field({
        "label": _plain(question["label"]), "required": question.get("required"),
        "options": list(dict.fromkeys(options)), "description": _plain(description),
    })


def scan_greenhouse_url(url: str) -> PrivacyReport:
    """Read public Greenhouse question metadata; never submit an application."""
    board, job_id = _greenhouse_target(url)
    payload = _fetch_job(board, job_id)
    if not isinstance(payload, Mapping) or not isinstance(payload.get("questions"), list):
        raise PrivacyScanError("The public Greenhouse response has no usable question list. The scan is incomplete.")
    fields: list[FieldMetadata] = []
    notices = ["Public Greenhouse question metadata was read without submitting an application or sending applicant data."]
    omitted_location_coordinates = False
    omitted_hidden_fields = False
    for group in ("questions", "location_questions", "compliance"):
        questions = payload.get(group, [])
        if questions is None:
            continue
        if not isinstance(questions, list):
            raise PrivacyScanError("The public Greenhouse questions have an unexpected format. The scan is incomplete.")
        for question in questions:
            metadata = _question_metadata(question)
            api_fields = question.get("fields", [])
            all_hidden = bool(api_fields) and all(api_field.get("type") == "input_hidden" for api_field in api_fields)
            if group == "location_questions" and _COORDINATES.search(metadata["label"]):
                # Greenhouse's documented location widget represents geocoded
                # coordinates as required technical fields. That is not evidence
                # of a separate applicant-facing question or location permission.
                omitted_location_coordinates = True
                continue
            if all_hidden:
                omitted_hidden_fields = True
                continue
            fields.append(metadata)
    demographic = payload.get("demographic_questions")
    if demographic is not None:
        if not isinstance(demographic, Mapping) or not isinstance(demographic.get("questions", []), list):
            raise PrivacyScanError("The public Greenhouse demographic questions have an unexpected format. The scan is incomplete.")
        purpose = _plain(demographic.get("description", "")) if isinstance(demographic.get("description", ""), str) else ""
        fields.extend(_question_metadata(question, purpose) for question in demographic.get("questions", []))
    # Data compliance is policy metadata, not a field label. Keep it a coverage
    # notice rather than inventing a question or claiming a purpose/consent.
    if payload.get("data_compliance"):
        notices.append("The ATS also reports data-consent configuration. Review the exact processing, retention, and consent wording on the application page.")
    if omitted_location_coordinates:
        notices.append("The ATS includes technical latitude/longitude fields for its location widget. These are excluded from direct applicant requests because the metadata does not establish a separate visible question or permission for precise device location.")
    if omitted_hidden_fields:
        notices.append("Hidden technical fields are excluded from direct applicant requests. Review the visible form for its actual questions and permission prompts.")
    if not fields:
        notices.append("The public ATS returned no application fields. This does not establish that the application has no questions.")
    report = analyze_fields(
        fields, source_url=f"https://job-boards.greenhouse.io/{board}/jobs/{job_id}",
        coverage="public_ats_form", notices=notices,
    )
    report["title"] = _plain(_text(payload.get("title", ""), 4000, "Job title"))
    return report
