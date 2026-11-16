import json

import pytest

from jobpilot.engine.adapters import ats_boards
from jobpilot.engine.adapters.ats_forms import HttpFormSchemas
from jobpilot.engine.domain import GateKind, Lane, Opportunity, RequirementCategory


class FakeResponse:
    def __init__(self, status=200, body=b"", chunks=None):
        self.status_code = status
        self._chunks = chunks if chunks is not None else [body]

    def iter_content(self, chunk_size):
        yield from self._chunks

    def close(self):
        pass


class FakeSession:
    def __init__(self, response=None, error=None):
        self.headers = {}
        self.response = response
        self.error = error
        self.calls = []

    def get(self, url, **options):
        self.calls.append(("GET", url, options))
        if self.error:
            raise self.error
        return self.response

    def post(self, url, **options):
        self.calls.append(("POST", url, options))
        if self.error:
            raise self.error
        return self.response


def _opp(url, *, company="Acme", title="Engineer", provider="", tenant="", job_id=""):
    return Opportunity(
        id="opp_1",
        identity_key="k1",
        lane=Lane.JOB,
        company=company,
        title=title,
        url=url,
        location="",
        provider=provider,
        tenant=tenant,
        provider_job_id=job_id,
        first_seen="",
        last_seen="",
    )


def test_greenhouse_questions_and_data_compliance():
    payload = {
        "questions": [
            {
                "label": "First Name",
                "required": True,
                "fields": [{"type": "input_text"}],
            },
            {
                "label": "Desired annual compensation",
                "required": True,
                "fields": [{"type": "input_text"}],
            },
        ],
        "location_questions": [
            {
                "label": "Current location",
                "required": True,
                "fields": [{"type": "input_text"}],
            }
        ],
        "data_compliance": [{"type": "gdpr", "requires_consent": True}],
    }
    session = FakeSession(FakeResponse(body=json.dumps(payload).encode()))
    snap, obs = HttpFormSchemas(
        session, now_fn=lambda: "2026-11-16T12:00:00+00:00"
    ).fetch(_opp("https://job-boards.greenhouse.io/acme/jobs/12345"))
    assert snap.status == "ok"
    assert snap.field_count == 4
    assert GateKind.CONSENT_WALL in snap.gates
    assert any(o.category is RequirementCategory.COMPENSATION for o in obs)
    assert (
        session.calls[0][1]
        == "https://boards-api.greenhouse.io/v1/boards/acme/jobs/12345?questions=true"
    )


def test_ashby_graphql_with_application_limit_and_survey():
    payload = {
        "data": {
            "jobPosting": {
                "id": "uuid-1",
                "title": "Partner Rep",
                "isListed": True,
                "applicationLimitCalloutHtml": "<p>You have reached the application limit.</p>",
                "shouldAskForTextingConsent": True,
                "shouldAskForWhatsAppConsent": False,
                "applicationForm": {
                    "sections": [
                        {
                            "isHidden": False,
                            "fieldEntries": [
                                {
                                    "isRequired": True,
                                    "isHidden": False,
                                    "field": {"title": "Email", "type": "Email"},
                                },
                                {
                                    "isRequired": True,
                                    "isHidden": True,
                                    "field": {"title": "Hidden", "type": "String"},
                                },
                            ],
                        }
                    ]
                },
                "surveyForms": [
                    {
                        "sections": [
                            {
                                "isHidden": False,
                                "fieldEntries": [
                                    {
                                        "isRequired": False,
                                        "isHidden": False,
                                        "field": {
                                            "title": "Veteran Status",
                                            "type": "ValueSelect",
                                        },
                                    }
                                ],
                            }
                        ]
                    }
                ],
            }
        }
    }
    session = FakeSession(FakeResponse(body=json.dumps(payload).encode()))
    snap, obs = HttpFormSchemas(
        session, now_fn=lambda: "2026-11-16T12:00:00+00:00"
    ).fetch(_opp("https://jobs.ashbyhq.com/ramp/uuid-1/application"))
    assert snap.status == "ok"
    assert GateKind.APPLICATION_LIMIT in snap.gates
    assert all(o.label != "Hidden" for o in obs)
    assert any(o.category is RequirementCategory.PRIVACY_LEGAL_CONSENT for o in obs)
    assert session.calls[0][0] == "POST"
    assert (
        session.calls[0][1]
        == "https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobPosting"
    )


def test_lever_html_parser_ignores_scripts_and_hidden_and_flags_captcha():
    html_body = """
    <html><body>
      <script>var secret = "ignored";</script>
      <form>
        <input type="hidden" name="csrf" value="abc" />
        <ul>
          <li class="application-question required">
            <div class="application-label">Full name *</div>
            <input type="text" name="name" />
          </li>
          <li class="application-question">
            <div class="application-label">What is your street address?</div>
            <input type="text" name="addr" required />
          </li>
        </ul>
        <div class="h-captcha" data-sitekey="x"></div>
      </form>
    </body></html>
    """
    session = FakeSession(FakeResponse(body=html_body.encode()))
    snap, obs = HttpFormSchemas(
        session, now_fn=lambda: "2026-11-16T12:00:00+00:00"
    ).fetch(_opp("https://jobs.lever.co/acme/abc-1/apply"))
    assert snap.status == "ok"
    assert GateKind.CAPTCHA in snap.gates
    assert any(o.category is RequirementCategory.ADDRESS for o in obs)
    assert all("csrf" not in o.label.lower() for o in obs)


def test_missing_ids_unsupported_and_enterprise_inferred_without_http():
    session = FakeSession()
    fetcher = HttpFormSchemas(session, now_fn=lambda: "2026-11-16T12:00:00+00:00")

    snap_wd, obs_wd = fetcher.fetch(
        _opp(
            "https://acme.wd1.myworkdayjobs.com/en-US/Careers/job/Austin/Tech_R1/apply"
        )
    )
    assert snap_wd.status == "inferred_gate"
    assert snap_wd.gates == (GateKind.ACCOUNT_REQUIRED,)
    assert len(obs_wd) == 1

    snap_miss, _ = fetcher.fetch(_opp("https://jobs.ashbyhq.com/acme"))
    assert snap_miss.status == "unsupported"
    assert "missing" in snap_miss.error

    snap_gh_root, _ = fetcher.fetch(_opp("https://acme.example.com/careers?gh_jid=999"))
    assert snap_gh_root.status == "unsupported"

    snap_ind, _ = fetcher.fetch(_opp("https://www.indeed.com/viewjob?jk=abc"))
    assert snap_ind.status == "unsupported"
    assert session.calls == []


@pytest.mark.parametrize(
    ("status", "body", "expected_status", "expected_gate", "expected_err"),
    [
        (
            403,
            b"<html>DataDome captcha challenge</html>",
            "inferred_gate",
            GateKind.CAPTCHA,
            "",
        ),
        (403, b"Forbidden", "error", None, "HTTP 403"),
        (404, b"Not found", "error", None, "HTTP 404 (closed)"),
        (429, b"Slow down", "error", None, "HTTP 429 (rate limited)"),
        (302, b"", "error", None, "redirected"),
    ],
)
def test_http_error_and_captcha_mapping(
    status, body, expected_status, expected_gate, expected_err
):
    session = FakeSession(FakeResponse(status=status, body=body))
    snap, _ = HttpFormSchemas(
        session, now_fn=lambda: "2026-11-16T12:00:00+00:00"
    ).fetch(_opp("https://boards.greenhouse.io/acme/jobs/42"))
    assert snap.status == expected_status
    if expected_gate:
        assert expected_gate in snap.gates
    if expected_err:
        assert expected_err in snap.error


def test_oversized_response_stopped(monkeypatch):
    monkeypatch.setattr(ats_boards, "MAX_BYTES", 8)
    session = FakeSession(FakeResponse(chunks=[b"12345", b"67890"]))
    snap, _ = HttpFormSchemas(
        session, now_fn=lambda: "2026-11-16T12:00:00+00:00"
    ).fetch(_opp("https://boards.greenhouse.io/acme/jobs/42"))
    assert snap.status == "error" and "too large" in snap.error
