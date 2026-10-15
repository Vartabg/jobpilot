"""Privacy findings describe observed metadata, never applicant answers or intent."""

import json

import pytest
import requests

from product import privacy
from product.privacy import PrivacyScanError, analyze_fields, scan_greenhouse_url


def field(label, required=None, options=None, description="", **extra):
    return {"label": label, "required": required, "options": options or [], "description": description, **extra}


def findings_for(*fields):
    return analyze_fields(list(fields))["findings"]


@pytest.mark.parametrize("decline", [
    "Prefer not to answer", "I don't wish to answer", "I don’t wish to answer",
    "Decline to self identify", "I do not want to disclose", "Choose not to say",
])
def test_required_response_can_allow_declining_disclosure(decline):
    finding = findings_for(field("Gender identity", True, ["Woman", "Man", decline]))[0]
    assert finding["required"] is True
    assert finding["optionality"] == "response_required_disclosure_optional"


def test_unknown_requiredness_stays_unknown_even_with_decline_choice():
    report = analyze_fields([{"label": "Sexual orientation", "options": ["Prefer not to answer"]}])
    assert report["findings"][0]["required"] is None
    assert report["findings"][0]["optionality"] == "unknown"
    assert any("Requiredness is unknown" in notice for notice in report["notices"])


@pytest.mark.parametrize("required, optionality", [(True, "required"), (False, "optional"), (None, "unknown")])
def test_requiredness_only_comes_from_metadata(required, optionality):
    assert findings_for(field("Legal Address", required))[0]["optionality"] == optionality


def test_question_wording_does_not_override_unknown_requiredness():
    finding = findings_for(field("Gender (optional)"))[0]
    assert finding["optionality"] == "unknown"


@pytest.mark.parametrize("label", ["Legal Address", "Full address", "Street Address", "Physical address", "Address Line 1", "Address", "Please enter your address", "Street"])
def test_full_address_is_a_review_finding(label):
    finding = findings_for(field(label, True))[0]
    assert finding["category"] == "precise_address"
    assert finding["concern_level"] == "review"
    assert finding["label"] == label


@pytest.mark.parametrize("label", ["Location (City)", "State or Province", "Country", "ZIP code", "Where do you live?", "Do you live in the following states: California or Rhode Island?"])
def test_coarse_location_is_distinguished_from_full_address(label):
    finding = findings_for(field(label, True))[0]
    assert finding["category"] == "coarse_location"
    assert finding["concern_level"] == "notice"


def test_contact_and_standard_profile_fields_are_not_confused_with_address_or_access():
    labels = ["Email address", "LinkedIn profile URL", "Personal website", "Desired salary", "First Name", "Resume", "Have you used video editing software?", "Describe your experience diagnosing networking issues", "Are you legally authorized to work in the United States?", "Will you require visa sponsorship?"]
    report = analyze_fields([field(label, True) for label in labels])
    assert report["findings"] == []
    assert report["complete"] is False
    assert "No findings does not establish" in report["coverage_note"]


def test_combined_question_can_have_multiple_categories_and_purpose_is_verbatim():
    label = "Please select your gender identity and sexual orientation"
    purpose = "We use this information for our stated diversity reporting."
    findings = findings_for(field(label, False, description=purpose))
    assert {finding["category"] for finding in findings} == {"gender_identity", "sexual_orientation"}
    assert all(finding["label"] == label and finding["employer_stated_purpose"] == purpose for finding in findings)
    assert all("illegal" not in finding["explanation"] for finding in findings)


@pytest.mark.parametrize("label, category", [
    ("Race / ethnicity", "race_ethnicity"), ("Sex", "gender_identity"),
    ("Disability status", "health_disability"), ("Protected veteran status", "veteran_status"),
    ("Social Security Number", "government_id"), ("Date of Birth", "date_of_birth"),
    ("Current salary", "salary_history"), ("Bank account details", "financial_credentials"),
    ("Are you married?", "marital_family"), ("Connect your LinkedIn account", "social_account_access"),
    ("Record a video introduction", "media_request"), ("Upload an audio response", "media_request"),
    ("Authorize a background check", "screening_authorization"),
    ("Are you willing to submit to a pre-employment background and employment verification screening?", "screening_authorization"),
    ("Sign in with LinkedIn", "social_account_access"), ("Create an account", "account_gate"),
    ("Latitude", "precise_location"),
    ("Religion", "religion_belief"), ("Political party affiliation", "political_affiliation"),
    ("Trade union membership", "union_membership"), ("Country of citizenship", "nationality_citizenship"),
])
def test_requested_categories(label, category):
    assert category in {finding["category"] for finding in findings_for(field(label))}


def test_only_field_labels_trigger_findings_not_options_descriptions_or_full_job_content():
    report = analyze_fields([field("Resume", description="We welcome all gender identities and sexual orientations.")])
    assert report["findings"] == []


def test_applicant_values_and_url_tokens_are_never_returned():
    report = analyze_fields([
        field("Legal Address", True, value="SECRET-HOME", answer="SECRET-ANSWER", selected="SECRET-SELECTED", files=["SECRET-FILE"])
    ], source_url="https://example.com/form?token=SECRET-TOKEN#SECRET-FRAGMENT")
    serialized = json.dumps(report)
    assert "SECRET" not in serialized
    assert report["source_url"] == "https://example.com/form"
    assert set(report["fields"][0]) == {"label", "required", "options", "description"}


@pytest.mark.parametrize("bad_field", [
    {"label": "Gender", "required": "false"},
    {"label": "Gender", "required": 0},
    {"label": "Gender", "options": [{"value": "SECRET"}]},
    {"label": "", "required": True},
])
def test_invalid_input_is_rejected_without_echoing_values(bad_field):
    with pytest.raises(PrivacyScanError) as exc:
        analyze_fields([bad_field])
    assert "SECRET" not in str(exc.value)


class Response:
    def __init__(self, payload, status=200, headers=None):
        self.status_code = status
        self.headers = headers or {}
        self.body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.closed = False

    def iter_content(self, chunk_size):
        for index in range(0, len(self.body), chunk_size):
            yield self.body[index:index + chunk_size]

    def close(self):
        self.closed = True


def serve(monkeypatch, response):
    calls = []
    def get(url, **kwargs):
        calls.append((url, kwargs))
        if isinstance(response, Exception):
            raise response
        return response
    monkeypatch.setattr(privacy._SESSION, "get", get)
    return calls


def test_public_greenhouse_collects_questions_locations_compliance_and_demographics(monkeypatch):
    response = Response({
        "title": "Software Engineer", "content": "Gender and disability job description is ignored",
        "questions": [{"label": "Legal Address", "required": True, "fields": [{"name": "question1", "type": "textarea", "value": "SECRET-ANSWER"}]}],
        "location_questions": [{"label": "Location", "fields": []}],
        "compliance": [{"label": "Veteran Status", "required": False, "fields": [{"values": [{"value": 3, "label": "I don't wish to answer"}]}]}],
        "demographic_questions": {
            "description": "<p>Employer-stated diversity reporting.</p>",
            "questions": [{"label": "Gender identity", "required": True, "answer_options": [{"id": 1, "label": "Prefer not to answer"}]}],
        },
        "data_compliance": [{"type": "gdpr", "requires_consent": True}],
        "candidate": {"name": "SECRET-NAME"},
    })
    calls = serve(monkeypatch, response)
    report = scan_greenhouse_url("https://job-boards.greenhouse.io/acme/jobs/123?gh_src=token")
    assert calls == [("https://boards-api.greenhouse.io/v1/boards/acme/jobs/123?questions=true", {"timeout": privacy.TIMEOUT, "allow_redirects": False, "stream": True})]
    assert report["coverage"] == "public_ats_form" and report["title"] == "Software Engineer"
    assert report["field_count"] == 4
    assert {finding["category"] for finding in report["findings"]} == {"precise_address", "coarse_location", "veteran_status", "gender_identity"}
    gender = next(finding for finding in report["findings"] if finding["category"] == "gender_identity")
    assert gender["optionality"] == "response_required_disclosure_optional"
    assert gender["employer_stated_purpose"] == "Employer-stated diversity reporting."
    assert "SECRET" not in json.dumps(report)
    assert response.closed
    assert report["complete"] is False


@pytest.mark.parametrize("url", [
    "https://boards.greenhouse.io/acme/jobs/123/",
    "https://job-boards.greenhouse.io/embed/job_app?for=acme&token=123&validityToken=SECRET",
])
def test_supported_greenhouse_urls_use_fixed_api_origin(monkeypatch, url):
    calls = serve(monkeypatch, Response({"questions": []}))
    report = scan_greenhouse_url(url)
    assert calls[0][0] == "https://boards-api.greenhouse.io/v1/boards/acme/jobs/123?questions=true"
    assert "SECRET" not in json.dumps(report)
    assert report["source_url"] == "https://job-boards.greenhouse.io/acme/jobs/123"


@pytest.mark.parametrize("url", [
    "https://www.ifit.com/careers?gh_jid=123", "https://evil.example/acme/jobs/123",
    "https://job-boards.greenhouse.io.evil.example/acme/jobs/123",
    "http://job-boards.greenhouse.io/acme/jobs/123",
    "https://user:password@job-boards.greenhouse.io/acme/jobs/123",
    "https://job-boards.greenhouse.io:444/acme/jobs/123",
    "https://job-boards.greenhouse.io/embed/job_app?for=../../evil&token=123",
    "https://job-boards.greenhouse.io/embed/job_app?for=acme&for=other&token=123",
    "https://job-boards.greenhouse.io/acme/jobs/123/confirmation",
    "https://job-boards.greenhouse.io/acme/jobs/123%2f..",
])
def test_unsupported_or_ambiguous_url_is_not_fetched(monkeypatch, url):
    calls = serve(monkeypatch, Response({"questions": []}))
    with pytest.raises(PrivacyScanError, match="direct HTTPS Greenhouse"):
        scan_greenhouse_url(url)
    assert calls == []


@pytest.mark.parametrize("response, message", [
    (Response({"secret": "SECRET-BODY"}, status=500), "incomplete"),
    (Response({}, status=302, headers={"Location": "https://evil.example"}), "redirected"),
    (Response(b"SECRET-NOT-JSON"), "unexpected format"),
    (Response({"error": "SECRET-NO-FIELDS"}), "no usable question"),
    (Response({"questions": {}}), "no usable question"),
    (Response({"questions": []}, headers={"Content-Length": str(privacy.MAX_BYTES + 1)}), "size limit"),
    (requests.ConnectionError("SECRET-SOCKET"), "unreachable"),
])
def test_failure_never_returns_clean_or_complete_report(monkeypatch, response, message):
    serve(monkeypatch, response)
    with pytest.raises(PrivacyScanError, match=message) as exc:
        scan_greenhouse_url("https://job-boards.greenhouse.io/acme/jobs/123")
    assert "SECRET" not in str(exc.value)
    if isinstance(response, Response):
        assert response.closed


def test_streamed_response_size_limit_is_enforced_without_content_length(monkeypatch):
    response = Response(b" " * (privacy.MAX_BYTES + 1))
    serve(monkeypatch, response)
    with pytest.raises(PrivacyScanError, match="size limit"):
        scan_greenhouse_url("https://job-boards.greenhouse.io/acme/jobs/123")
    assert response.closed


def test_greenhouse_derived_coordinates_are_not_applicant_disclosure_findings(monkeypatch):
    response = Response({
        "questions": [{"label": "Legal Address", "required": True, "fields": [{"type": "textarea"}]}],
        "location_questions": [
            {"label": "Longitude", "required": True, "fields": [{"name": "longitude", "type": "input_hidden", "values": []}]},
            {"label": "Latitude", "required": True, "fields": [{"name": "latitude", "type": "input_hidden", "values": []}]},
            {"label": "Location", "required": True, "fields": [{"name": "location", "type": "input_text", "values": []}]},
        ],
    })
    serve(monkeypatch, response)
    report = scan_greenhouse_url("https://job-boards.greenhouse.io/ifit/jobs/123")
    assert report["field_count"] == 2
    assert [(finding["label"], finding["category"]) for finding in report["findings"]] == [
        ("Legal Address", "precise_address"), ("Location", "coarse_location")
    ]
    assert any("technical latitude/longitude" in notice for notice in report["notices"])
    assert not any(finding["category"] == "precise_location" for finding in report["findings"])


def test_other_hidden_api_fields_are_not_direct_applicant_requests(monkeypatch):
    serve(monkeypatch, Response({"questions": [
        {"label": "Date of Birth", "required": True, "fields": [{"type": "input_hidden"}]},
        {"label": "Gender", "required": False, "fields": [{"type": "multi_value_single_select"}, {"type": "input_hidden"}]},
    ]}))
    report = scan_greenhouse_url("https://job-boards.greenhouse.io/acme/jobs/123")
    assert [finding["category"] for finding in report["findings"]] == ["gender_identity"]
    assert any("Hidden technical fields" in notice for notice in report["notices"])


@pytest.mark.parametrize("label", [
    "Do you have experience working with children?",
    "Describe your experience supporting applicants with disabilities.",
    "What is your experience treating patients with medical conditions?",
    "Describe your background helping clients with mental health conditions.",
])
def test_occupational_experience_with_others_is_not_applicant_disclosure(label):
    assert findings_for(field(label, True)) == []


@pytest.mark.parametrize("label, category", [
    ("Do you have experience working with children, and do you have children?", "marital_family"),
    ("Describe your experience working with children and your childcare arrangements.", "marital_family"),
    ("Describe your experience supporting applicants with disabilities and your medical history.", "health_disability"),
    ("Do you have medical history working with applicants with disabilities?", "health_disability"),
    ("Describe your medical history working with children.", "health_disability"),
    ("Are you disabled?", "health_disability"),
    ("Please provide your preferred pronouns.", "gender_identity"),
    ("What are your pronouns?", "gender_identity"),
])
def test_occupational_words_do_not_hide_personal_disclosures_and_pronouns_are_flagged(label, category):
    assert category in {finding["category"] for finding in findings_for(field(label, True))}



def test_native_country_options_do_not_discard_sensitive_fields_in_mixed_form(monkeypatch):
    country_options = [f"Country {index}" for index in range(249)]
    fields = [
        field("Country", True, country_options),
        field("Legal Address", True),
        field("Gender identity", True, ["Woman", "Man", "Prefer not to answer"]),
    ]
    visible = analyze_fields(fields)
    assert visible["field_count"] == 3
    assert len(visible["fields"][0]["options"]) == 249
    assert {(finding["category"], finding["optionality"]) for finding in visible["findings"]} == {
        ("coarse_location", "required"), ("precise_address", "required"),
        ("gender_identity", "response_required_disclosure_optional"),
    }

    serve(monkeypatch, Response({"questions": [
        {"label": entry["label"], "required": entry["required"], "fields": [
            {"type": "multi_value_single_select" if entry["options"] else "input_text",
             "values": [{"label": label, "value": index} for index, label in enumerate(entry["options"])]}
        ]} for entry in fields
    ]}))
    public = scan_greenhouse_url("https://job-boards.greenhouse.io/acme/jobs/123")
    assert public["findings"] == visible["findings"]
    assert len(public["fields"][0]["options"]) == 249


def test_option_limit_matches_route_and_collector_boundary():
    assert len(analyze_fields([field("Country", True, [str(index) for index in range(500)])])["fields"][0]["options"]) == 500
    with pytest.raises(PrivacyScanError, match="options exceed"):
        analyze_fields([field("Country", True, [str(index) for index in range(501)])])
