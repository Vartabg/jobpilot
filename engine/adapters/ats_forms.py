"""Read-only public ATS application form-schema inspector (Greenhouse, Ashby, Lever).

Reads only allowlisted public ATS endpoints (never arbitrary company career sites,
never fills or submits forms, never attempts login or CAPTCHA bypass).
"""

from __future__ import annotations

import html
import json
import time
from collections.abc import Callable
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote, unquote

import requests

from jobpilot.engine.adapters import ats_boards
from jobpilot.engine.adapters.ats_boards import (
    BoardError,
    html_to_text,
    read_bounded_response,
)
from jobpilot.engine.domain import (
    PUBLIC_API_PROVIDERS,
    RECOGNIZED_PROVIDERS,
    FormSnapshot,
    GateKind,
    Opportunity,
    RequirementCategory,
    RequirementObservation,
    build_role_key,
    classify_requirement,
    infer_ats_provider_and_gate,
    normalize_employer_key,
    utc_now,
)

FORM_MAX_BYTES = 4 * 1024 * 1024
GREENHOUSE_JOB_URL = (
    "https://boards-api.greenhouse.io/v1/boards/{}/jobs/{}?questions=true"
)
ASHBY_GRAPHQL_URL = "https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobPosting"
LEVER_APPLY_URL = "https://jobs.lever.co/{}/{}/apply"

_ASHBY_QUERY = (
    "query ApiJobPosting($organizationHostedJobsPageName: String!, $jobPostingId: String!) {\n"
    "  jobPosting(organizationHostedJobsPageName: $organizationHostedJobsPageName, jobPostingId: $jobPostingId) {\n"
    "    id\n"
    "    title\n"
    "    isListed\n"
    "    applicationLimitCalloutHtml\n"
    "    shouldAskForTextingConsent\n"
    "    shouldAskForWhatsAppConsent\n"
    "    applicationForm { sections { isHidden fieldEntries { isRequired isHidden field { title type } } } }\n"
    "    surveyForms { sections { isHidden fieldEntries { isRequired isHidden field { title type } } } }\n"
    "  }\n"
    "}"
)


def parse_greenhouse_form(
    payload: Any,
    *,
    employer_key: str,
    employer_name: str,
    role_key: str,
    role_title: str,
    role_url: str,
    fetched_at: str,
) -> tuple[FormSnapshot, tuple[RequirementObservation, ...]]:
    """Parse Greenhouse ``?questions=true`` JSON into a snapshot and observations."""
    if not isinstance(payload, dict):
        return (
            FormSnapshot(
                role_key=role_key,
                employer_key=employer_key,
                provider="greenhouse",
                role_title=role_title,
                role_url=role_url,
                status="error",
                error="unexpected Greenhouse form payload",
                fetched_at=fetched_at,
            ),
            (),
        )
    observations: list[RequirementObservation] = []
    gates: set[GateKind] = set()
    raw_questions: list[dict[str, Any]] = []
    for key in ("questions", "location_questions"):
        items = payload.get(key)
        if isinstance(items, list):
            raw_questions.extend(q for q in items if isinstance(q, dict))

    for q in raw_questions:
        label = str(q.get("label") or "").strip()
        if not label:
            continue
        req = (
            "required"
            if q.get("required") is True
            else "optional"
            if q.get("required") is False
            else "unknown"
        )
        fields = q.get("fields")
        ftype = ""
        if isinstance(fields, list) and fields and isinstance(fields[0], dict):
            ftype = str(fields[0].get("type") or "").strip()
        cat, gate, beyond = classify_requirement(label, ftype, req)
        if gate:
            gates.add(gate)
        observations.append(
            RequirementObservation(
                employer_key=employer_key,
                employer_name=employer_name,
                role_key=role_key,
                role_title=role_title,
                role_url=role_url,
                provider="greenhouse",
                category=cat,
                label=label,
                field_type=ftype,
                requiredness=req,
                gate_kind=gate,
                is_beyond_standard=beyond,
                source_kind="live_ats_form",
                source_ref="greenhouse:questions",
                observed_at=fetched_at,
            )
        )

    for comp in payload.get("data_compliance") or []:
        if not isinstance(comp, dict):
            continue
        needs_consent = any(
            comp.get(k) is True
            for k in (
                "requires_consent",
                "requires_processing_consent",
                "requires_retention_consent",
            )
        )
        if needs_consent:
            comp_type = str(comp.get("type") or "privacy").strip()
            gates.add(GateKind.CONSENT_WALL)
            observations.append(
                RequirementObservation(
                    employer_key=employer_key,
                    employer_name=employer_name,
                    role_key=role_key,
                    role_title=role_title,
                    role_url=role_url,
                    provider="greenhouse",
                    category=RequirementCategory.PRIVACY_LEGAL_CONSENT,
                    label=f"Mandatory data compliance consent ({comp_type})",
                    field_type="checkbox",
                    requiredness="required",
                    gate_kind=GateKind.CONSENT_WALL,
                    is_beyond_standard=True,
                    source_kind="live_ats_form",
                    source_ref="greenhouse:data_compliance",
                    observed_at=fetched_at,
                )
            )

    beyond_count = sum(
        1 for o in observations if o.is_beyond_standard and o.requiredness != "optional"
    )
    return (
        FormSnapshot(
            role_key=role_key,
            employer_key=employer_key,
            provider="greenhouse",
            role_title=role_title,
            role_url=role_url,
            status="ok",
            field_count=len(observations),
            beyond_standard_count=beyond_count,
            gates=tuple(sorted(gates, key=lambda g: g.value)),
            fetched_at=fetched_at,
        ),
        tuple(observations),
    )


def parse_ashby_form(
    payload: Any,
    *,
    employer_key: str,
    employer_name: str,
    role_key: str,
    role_title: str,
    role_url: str,
    fetched_at: str,
) -> tuple[FormSnapshot, tuple[RequirementObservation, ...]]:
    """Parse Ashby ``ApiJobPosting`` GraphQL response into snapshot & observations."""
    posting = (
        payload.get("data", {}).get("jobPosting") if isinstance(payload, dict) else None
    )
    if not isinstance(posting, dict):
        return (
            FormSnapshot(
                role_key=role_key,
                employer_key=employer_key,
                provider="ashby",
                role_title=role_title,
                role_url=role_url,
                status="error",
                error="Ashby jobPosting unavailable or closed",
                fetched_at=fetched_at,
            ),
            (),
        )

    observations: list[RequirementObservation] = []
    gates: set[GateKind] = set()

    def _consume_form(form_obj: Any, source_ref: str) -> None:
        if not isinstance(form_obj, dict):
            return
        for sec in form_obj.get("sections") or []:
            if not isinstance(sec, dict) or sec.get("isHidden") is True:
                continue
            for entry in sec.get("fieldEntries") or []:
                if not isinstance(entry, dict) or entry.get("isHidden") is True:
                    continue
                field_obj = entry.get("field")
                if not isinstance(field_obj, dict):
                    continue
                label = str(field_obj.get("title") or "").strip()
                if not label:
                    continue
                ftype = str(field_obj.get("type") or "").strip()
                req = (
                    "required"
                    if entry.get("isRequired") is True
                    else "optional"
                    if entry.get("isRequired") is False
                    else "unknown"
                )
                cat, gate, beyond = classify_requirement(label, ftype, req)
                if gate:
                    gates.add(gate)
                observations.append(
                    RequirementObservation(
                        employer_key=employer_key,
                        employer_name=employer_name,
                        role_key=role_key,
                        role_title=role_title,
                        role_url=role_url,
                        provider="ashby",
                        category=cat,
                        label=label,
                        field_type=ftype,
                        requiredness=req,
                        gate_kind=gate,
                        is_beyond_standard=beyond,
                        source_kind="live_ats_form",
                        source_ref=source_ref,
                        observed_at=fetched_at,
                    )
                )

    _consume_form(posting.get("applicationForm"), "ashby:applicationForm")
    survey_forms = posting.get("surveyForms")
    if isinstance(survey_forms, list):
        for sf in survey_forms:
            _consume_form(sf, "ashby:surveyForms")
    elif isinstance(survey_forms, dict):
        _consume_form(survey_forms, "ashby:surveyForms")

    callout = html_to_text(str(posting.get("applicationLimitCalloutHtml") or ""))
    if callout:
        gates.add(GateKind.APPLICATION_LIMIT)
        observations.append(
            RequirementObservation(
                employer_key=employer_key,
                employer_name=employer_name,
                role_key=role_key,
                role_title=role_title,
                role_url=role_url,
                provider="ashby",
                category=RequirementCategory.OTHER,
                label=callout[:200],
                field_type="callout",
                requiredness="required",
                gate_kind=GateKind.APPLICATION_LIMIT,
                is_beyond_standard=True,
                source_kind="live_ats_form",
                source_ref="ashby:applicationLimitCalloutHtml",
                observed_at=fetched_at,
            )
        )

    for consent_key, label_text in (
        ("shouldAskForTextingConsent", "SMS / texting consent prompt"),
        ("shouldAskForWhatsAppConsent", "WhatsApp messaging consent prompt"),
    ):
        if posting.get(consent_key) is True:
            observations.append(
                RequirementObservation(
                    employer_key=employer_key,
                    employer_name=employer_name,
                    role_key=role_key,
                    role_title=role_title,
                    role_url=role_url,
                    provider="ashby",
                    category=RequirementCategory.PRIVACY_LEGAL_CONSENT,
                    label=label_text,
                    field_type="consent",
                    requiredness="optional",
                    gate_kind=None,
                    is_beyond_standard=True,
                    source_kind="live_ats_form",
                    source_ref=f"ashby:{consent_key}",
                    observed_at=fetched_at,
                )
            )

    beyond_count = sum(
        1 for o in observations if o.is_beyond_standard and o.requiredness != "optional"
    )
    return (
        FormSnapshot(
            role_key=role_key,
            employer_key=employer_key,
            provider="ashby",
            role_title=role_title,
            role_url=role_url,
            status="ok",
            field_count=len(observations),
            beyond_standard_count=beyond_count,
            gates=tuple(sorted(gates, key=lambda g: g.value)),
            fetched_at=fetched_at,
        ),
        tuple(observations),
    )


class _LeverApplyParser(HTMLParser):
    """Extract Lever application questions and CAPTCHA markers while ignoring scripts/hidden inputs."""

    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self._li_depth = 0
        self._in_question_li = False
        self._in_label = False
        self._label_parts: list[str] = []
        self._required = False
        self._field_types: list[str] = []
        self.questions: list[tuple[str, bool, str]] = []
        self.has_captcha = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        t = tag.lower()
        if t in {"script", "style"}:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        cls = attr_map.get("class", "").lower()
        if "h-captcha" in cls or "g-recaptcha" in cls:
            self.has_captcha = True

        if t == "li":
            if self._in_question_li:
                self._li_depth += 1
            elif "application-question" in cls:
                self._in_question_li = True
                self._li_depth = 1
                self._label_parts = []
                self._required = "required" in cls
                self._field_types = []
            return

        if not self._in_question_li:
            return

        if "required" in attr_map or "required" in cls:
            self._required = True

        if (t == "div" and "application-label" in cls) or (
            t == "label" and not self._label_parts
        ):
            self._in_label = True
        elif t == "input":
            itype = attr_map.get("type", "text").lower()
            if itype not in {"hidden", "submit", "button"}:
                self._field_types.append(itype)
        elif t in {"textarea", "select"}:
            self._field_types.append(t)

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in {"script", "style"} and self._skip_depth:
            self._skip_depth -= 1
            return
        if self._skip_depth or not self._in_question_li:
            return
        if t in {"div", "label"} and self._in_label:
            self._in_label = False
        if t == "li":
            self._li_depth -= 1
            if self._li_depth <= 0:
                text = html.unescape(
                    " ".join("".join(self._label_parts).split())
                ).strip()
                if "*" in text:
                    self._required = True
                    text = text.rstrip("* ").strip()
                if text:
                    ftype = self._field_types[0] if self._field_types else "text"
                    self.questions.append((text, self._required, ftype))
                self._in_question_li = False
                self._in_label = False

    def handle_data(self, data: str) -> None:
        if self._skip_depth or not self._in_question_li or not self._in_label:
            return
        self._label_parts.append(data)


def parse_lever_apply_html(
    html_text: str,
    *,
    employer_key: str,
    employer_name: str,
    role_key: str,
    role_title: str,
    role_url: str,
    fetched_at: str,
) -> tuple[FormSnapshot, tuple[RequirementObservation, ...]]:
    """Parse Lever ``/apply`` HTML into a FormSnapshot and RequirementObservations."""
    parser = _LeverApplyParser()
    parser.feed(html_text)
    parser.close()

    observations: list[RequirementObservation] = []
    gates: set[GateKind] = set()
    if (
        parser.has_captcha
        or "h-captcha" in html_text.lower()
        or "g-recaptcha" in html_text.lower()
    ):
        gates.add(GateKind.CAPTCHA)
        observations.append(
            RequirementObservation(
                employer_key=employer_key,
                employer_name=employer_name,
                role_key=role_key,
                role_title=role_title,
                role_url=role_url,
                provider="lever",
                category=RequirementCategory.OTHER,
                label="Interactive CAPTCHA verification widget on Lever form",
                field_type="captcha",
                requiredness="required",
                gate_kind=GateKind.CAPTCHA,
                is_beyond_standard=True,
                source_kind="live_ats_form",
                source_ref="lever:apply_html",
                observed_at=fetched_at,
            )
        )

    for text, is_req, ftype in parser.questions:
        req = "required" if is_req else "optional"
        cat, gate, beyond = classify_requirement(text, ftype, req)
        if gate:
            gates.add(gate)
        observations.append(
            RequirementObservation(
                employer_key=employer_key,
                employer_name=employer_name,
                role_key=role_key,
                role_title=role_title,
                role_url=role_url,
                provider="lever",
                category=cat,
                label=text,
                field_type=ftype,
                requiredness=req,
                gate_kind=gate,
                is_beyond_standard=beyond,
                source_kind="live_ats_form",
                source_ref="lever:apply_html",
                observed_at=fetched_at,
            )
        )

    beyond_count = sum(
        1 for o in observations if o.is_beyond_standard and o.requiredness != "optional"
    )
    return (
        FormSnapshot(
            role_key=role_key,
            employer_key=employer_key,
            provider="lever",
            role_title=role_title,
            role_url=role_url,
            status="ok",
            field_count=len(observations),
            beyond_standard_count=beyond_count,
            gates=tuple(sorted(gates, key=lambda g: g.value)),
            fetched_at=fetched_at,
        ),
        tuple(observations),
    )


class HttpFormSchemas:
    """Bounded read-only fetcher for Greenhouse, Ashby, and Lever form schemas."""

    def __init__(
        self,
        session: requests.Session | None = None,
        *,
        delay_seconds: float = 0.0,
        sleep_fn: Callable[[float], None] = time.sleep,
        now_fn: Callable[[], str] = utc_now,
    ) -> None:
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json, text/html",
                "User-Agent": "JobPilot (+https://github.com/Vartabg/jobpilot)",
            }
        )
        self.delay_seconds = max(0.0, float(delay_seconds))
        self.sleep_fn = sleep_fn
        self.now_fn = now_fn
        self._did_http = False

    def _pace(self) -> None:
        if self._did_http and self.delay_seconds > 0:
            self.sleep_fn(self.delay_seconds)
        self._did_http = True

    def fetch(
        self, opportunity: Opportunity
    ) -> tuple[FormSnapshot, tuple[RequirementObservation, ...]]:
        fetched_at = self.now_fn()
        emp_key = normalize_employer_key(opportunity.company) or "unknown"
        emp_name = (opportunity.company or emp_key).strip()
        prov, tenant, job_id, default_gate = infer_ats_provider_and_gate(
            opportunity.url,
            opportunity.provider,
            opportunity.tenant,
            opportunity.provider_job_id,
        )
        role_key = build_role_key(
            employer_key=emp_key,
            role_title=opportunity.title,
            role_url=opportunity.url,
            provider=prov,
            tenant=tenant,
            provider_job_id=job_id,
            source_kind="live_ats_form",
        )

        if not prov or prov not in RECOGNIZED_PROVIDERS:
            return (
                FormSnapshot(
                    role_key=role_key,
                    employer_key=emp_key,
                    provider=prov,
                    role_title=opportunity.title,
                    role_url=opportunity.url,
                    status="unsupported",
                    error="unsupported provider",
                    fetched_at=fetched_at,
                ),
                (),
            )

        if prov not in PUBLIC_API_PROVIDERS:
            gates = (default_gate,) if default_gate else ()
            obs: tuple[RequirementObservation, ...] = ()
            if default_gate in {GateKind.ACCOUNT_REQUIRED, GateKind.CONSENT_WALL}:
                label = (
                    f"{prov} portal requires candidate account / sign-in"
                    if default_gate is GateKind.ACCOUNT_REQUIRED
                    else f"{prov} portal requires privacy/terms consent gate"
                )
                obs = (
                    RequirementObservation(
                        employer_key=emp_key,
                        employer_name=emp_name,
                        role_key=role_key,
                        role_title=opportunity.title,
                        role_url=opportunity.url,
                        provider=prov,
                        category=(
                            RequirementCategory.OTHER
                            if default_gate is GateKind.ACCOUNT_REQUIRED
                            else RequirementCategory.PRIVACY_LEGAL_CONSENT
                        ),
                        label=label,
                        field_type="portal_gate",
                        requiredness="required",
                        gate_kind=default_gate,
                        is_beyond_standard=True,
                        source_kind="live_ats_form",
                        source_ref=f"inferred:{prov}",
                        observed_at=fetched_at,
                    ),
                )
            return (
                FormSnapshot(
                    role_key=role_key,
                    employer_key=emp_key,
                    provider=prov,
                    role_title=opportunity.title,
                    role_url=opportunity.url,
                    status="inferred_gate",
                    field_count=len(obs),
                    beyond_standard_count=len(obs),
                    gates=gates,
                    fetched_at=fetched_at,
                ),
                obs,
            )

        if not tenant or not job_id:
            return (
                FormSnapshot(
                    role_key=role_key,
                    employer_key=emp_key,
                    provider=prov,
                    role_title=opportunity.title,
                    role_url=opportunity.url,
                    status="unsupported",
                    error="missing board token or job id",
                    fetched_at=fetched_at,
                ),
                (),
            )

        safe_tenant = quote(unquote(tenant), safe="")
        safe_job_id = quote(unquote(job_id), safe="")
        max_bytes = min(ats_boards.MAX_BYTES, FORM_MAX_BYTES)
        self._pace()

        try:
            if prov == "greenhouse":
                endpoint = GREENHOUSE_JOB_URL.format(safe_tenant, safe_job_id)
                status_code, body = read_bounded_response(
                    self.session,
                    endpoint,
                    max_bytes=max_bytes,
                    allow_status=lambda code: code in {200, 403},
                )
            elif prov == "ashby":
                body_json = {
                    "operationName": "ApiJobPosting",
                    "variables": {
                        "organizationHostedJobsPageName": unquote(tenant),
                        "jobPostingId": unquote(job_id),
                    },
                    "query": _ASHBY_QUERY,
                }
                status_code, body = read_bounded_response(
                    self.session,
                    ASHBY_GRAPHQL_URL,
                    method="POST",
                    json_body=body_json,
                    max_bytes=max_bytes,
                    allow_status=lambda code: code in {200, 403},
                )
            else:
                endpoint = LEVER_APPLY_URL.format(safe_tenant, safe_job_id)
                status_code, body = read_bounded_response(
                    self.session,
                    endpoint,
                    max_bytes=max_bytes,
                    allow_status=lambda code: code in {200, 403},
                )
        except BoardError as exc:
            err = str(exc)
            if "HTTP 404" in err:
                err = "HTTP 404 (closed)"
            elif "HTTP 429" in err:
                err = "HTTP 429 (rate limited)"
            return (
                FormSnapshot(
                    role_key=role_key,
                    employer_key=emp_key,
                    provider=prov,
                    role_title=opportunity.title,
                    role_url=opportunity.url,
                    status="error",
                    error=err,
                    fetched_at=fetched_at,
                ),
                (),
            )

        if status_code == 403:
            text_low = body.decode("utf-8", "ignore").lower()
            if any(
                k in text_low
                for k in ("captcha", "datadome", "cloudflare", "challenge")
            ):
                obs = (
                    RequirementObservation(
                        employer_key=emp_key,
                        employer_name=emp_name,
                        role_key=role_key,
                        role_title=opportunity.title,
                        role_url=opportunity.url,
                        provider=prov,
                        category=RequirementCategory.OTHER,
                        label=f"Anti-bot / CAPTCHA challenge returned by {prov} (HTTP 403)",
                        field_type="captcha",
                        requiredness="required",
                        gate_kind=GateKind.CAPTCHA,
                        is_beyond_standard=True,
                        source_kind="live_ats_form",
                        source_ref=f"http_403:{prov}",
                        observed_at=fetched_at,
                    ),
                )
                return (
                    FormSnapshot(
                        role_key=role_key,
                        employer_key=emp_key,
                        provider=prov,
                        role_title=opportunity.title,
                        role_url=opportunity.url,
                        status="inferred_gate",
                        field_count=1,
                        beyond_standard_count=1,
                        gates=(GateKind.CAPTCHA,),
                        fetched_at=fetched_at,
                    ),
                    obs,
                )
            return (
                FormSnapshot(
                    role_key=role_key,
                    employer_key=emp_key,
                    provider=prov,
                    role_title=opportunity.title,
                    role_url=opportunity.url,
                    status="error",
                    error="HTTP 403",
                    fetched_at=fetched_at,
                ),
                (),
            )

        if prov == "lever":
            return parse_lever_apply_html(
                body.decode("utf-8", "ignore"),
                employer_key=emp_key,
                employer_name=emp_name,
                role_key=role_key,
                role_title=opportunity.title,
                role_url=opportunity.url,
                fetched_at=fetched_at,
            )

        try:
            parsed = json.loads(body)
        except ValueError:
            return (
                FormSnapshot(
                    role_key=role_key,
                    employer_key=emp_key,
                    provider=prov,
                    role_title=opportunity.title,
                    role_url=opportunity.url,
                    status="error",
                    error="response was not valid JSON",
                    fetched_at=fetched_at,
                ),
                (),
            )

        if prov == "greenhouse":
            return parse_greenhouse_form(
                parsed,
                employer_key=emp_key,
                employer_name=emp_name,
                role_key=role_key,
                role_title=opportunity.title,
                role_url=opportunity.url,
                fetched_at=fetched_at,
            )
        return parse_ashby_form(
            parsed,
            employer_key=emp_key,
            employer_name=emp_name,
            role_key=role_key,
            role_title=opportunity.title,
            role_url=opportunity.url,
            fetched_at=fetched_at,
        )
