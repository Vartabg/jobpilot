"""Full-description and provenance contracts for portal scans."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from jobpilot.core.portal_scanner import PortalScanner, ScanTarget


def _response(payload: object) -> MagicMock:
    return MagicMock(raise_for_status=lambda: None, json=MagicMock(return_value=payload))


@patch("jobpilot.core.portal_scanner.requests.get")
def test_greenhouse_keeps_full_description_and_provider_identity(mock_get: MagicMock) -> None:
    mock_get.return_value = _response({"jobs": [{
        "id": 123,
        "title": "Field Engineer",
        "absolute_url": "https://example.test/wrapped",
        "location": {"name": "Austin, TX"},
        "content": "<p>Build systems.</p><p>Help customers.</p>",
        "updated_at": "2026-08-13T12:00:00-04:00",
        "metadata": [{"name": "Employment Type", "value": "Full-time"}],
    }]})

    job = PortalScanner().scan_greenhouse_board("acme", label="Acme")[0]

    assert mock_get.call_args.kwargs["params"] == {"content": "true"}
    assert job.description == "Build systems.\nHelp customers."
    assert job.provider_tenant == "acme"
    assert job.provider_job_id == "123"
    assert job.canonical_url == "https://boards.greenhouse.io/acme/jobs/123"
    assert job.listing_state == "listed"
    assert job.fetched_at
    assert job.posted_at == "2026-08-13T12:00:00-04:00"


@patch("jobpilot.core.portal_scanner.requests.get")
def test_greenhouse_html_keeps_sections_scoreable(mock_get: MagicMock) -> None:
    mock_get.return_value = _response({"jobs": [{
        "id": 124,
        "title": "Implementation Engineer",
        "location": {"name": "Austin, TX"},
        "content": """
            <h2><strong>Responsibilities</strong></h2>
            <ul><li>Lead customer discovery workshops.</li></ul>
            <h2><strong>Required qualifications</strong></h2>
            <ul><li>Systems troubleshooting experience.</li></ul>
            <h2><strong>Preferred qualifications</strong></h2>
            <ul><li>Python automation experience.</li></ul>
        """,
    }]})

    job = PortalScanner().scan_greenhouse_board("acme", label="Acme")[0]

    from jobpilot.core.requirement_matcher import RequirementMatcher

    requirements = RequirementMatcher.parse(job.description)
    assert requirements.is_sufficient
    assert "<" not in job.description
    assert "<" not in requirements.raw_text


@patch("jobpilot.core.portal_scanner.requests.get")
def test_lever_prefers_full_html_over_incomplete_plain_variant(mock_get: MagicMock) -> None:
    mock_get.return_value = _response([{
        "id": "lever-full-jd",
        "text": "Implementation Engineer",
        "hostedUrl": "https://jobs.lever.co/acme/lever-full-jd",
        "descriptionPlain": "Short introduction.",
        "description": (
            "<p>Short introduction.</p>"
            "<p>Partner with customers throughout complex deployments.</p>"
        ),
        "lists": [{
            "text": "<strong>Required qualifications</strong>",
            "content": (
                "<ul><li>Systems troubleshooting experience.</li>"
                "<li>Clear technical communication.</li></ul>"
            ),
        }],
        "additional": "<p>Travel to customer sites as needed.</p>",
    }])

    job = PortalScanner().scan_lever_board("acme")[0]

    assert "Partner with customers throughout complex deployments." in job.description
    assert "Systems troubleshooting experience." in job.description
    assert "Travel to customer sites as needed." in job.description
    assert "<strong>" not in job.description
    assert "<li>" not in job.description


@patch("jobpilot.core.portal_scanner.requests.get")
def test_lever_keeps_description_workplace_and_compensation(mock_get: MagicMock) -> None:
    mock_get.return_value = _response([{
        "id": "lever-1",
        "text": "Implementation Engineer",
        "hostedUrl": "https://jobs.lever.co/acme/lever-1?lever-source=feed",
        "categories": {"location": "Remote", "team": "Deployment"},
        "descriptionPlain": "Own deployments.",
        "lists": [{"text": "What you will do", "content": "<li>Train customers</li>"}],
        "additionalPlain": "Travel up to 25%.",
        "workplaceType": "remote",
        "createdAt": 1786622400000,
        "salaryRange": {"currency": "USD", "min": 90000, "max": 120000},
    }])

    job = PortalScanner().scan_lever_board("acme")[0]

    assert job.provider_job_id == "lever-1"
    assert job.provider_tenant == "acme"
    assert "Own deployments." in job.description
    assert "Train customers" in job.description
    assert "Travel up to 25%." in job.description
    assert job.workplace_type == "remote"
    assert job.compensation["min"] == 90000
    assert job.posted_at.startswith("2026-")


@patch("jobpilot.core.portal_scanner.requests.get")
def test_lever_requirement_heading_does_not_swallow_hard_logistics(
    mock_get: MagicMock,
) -> None:
    mock_get.return_value = _response([{
        "id": "lever-hard-gate",
        "text": "Implementation Engineer",
        "hostedUrl": "https://jobs.lever.co/acme/lever-hard-gate",
        "descriptionPlain": "Work with customers on complex deployments.",
        "lists": [
            {
                "text": "Requirements",
                "content": (
                    "<li>Python automation experience.</li>"
                    "<li>Systems troubleshooting experience.</li>"
                    "<li>Valid driver's license required.</li>"
                ),
            },
            {
                "text": "Responsibilities",
                "content": "<li>Lead customer deployment workshops.</li>",
            },
        ],
    }])

    job = PortalScanner().scan_lever_board("acme")[0]

    from jobpilot.core.requirement_matcher import RequirementMatcher

    requirements = RequirementMatcher.parse(job.description)
    assert any("driver's license" in item for item in requirements.mandatory)


@patch("jobpilot.core.portal_scanner.requests.get")
def test_ashby_keeps_listed_state_description_and_compensation(mock_get: MagicMock) -> None:
    mock_get.return_value = _response({"jobs": [{
        "id": "ashby-1",
        "title": "Customer Engineer",
        "location": "Austin",
        "jobUrl": "https://jobs.ashbyhq.com/acme/ashby-1",
        "descriptionHtml": "<p>Solve hard problems with customers.</p>",
        "publishedAt": "2026-08-12T09:30:00Z",
        "isListed": False,
        "compensation": {"compensationTierSummary": "$100K - $140K"},
    }]})

    job = PortalScanner().scan_ashby_board("acme")[0]

    assert job.provider_job_id == "ashby-1"
    assert job.description == "Solve hard problems with customers."
    assert job.listing_state == "closed"
    assert job.posted_at == "2026-08-12T09:30:00Z"
    assert job.compensation["compensationTierSummary"] == "$100K - $140K"


@patch("jobpilot.core.portal_scanner.requests.get")
def test_ashby_prefers_full_html_over_incomplete_plain_variant(mock_get: MagicMock) -> None:
    mock_get.return_value = _response({"jobs": [{
        "id": "ashby-full-jd",
        "title": "Customer Engineer",
        "jobUrl": "https://jobs.ashbyhq.com/acme/ashby-full-jd",
        "descriptionPlain": "Short introduction.",
        "descriptionHtml": (
            "<p>Short introduction.</p>"
            "<p><strong>Responsibilities</strong></p>"
            "<ul><li>Lead technical discovery with customers.</li>"
            "<li>Own complex product deployments.</li></ul>"
        ),
    }]})

    job = PortalScanner().scan_ashby_board("acme")[0]

    assert "Responsibilities" in job.description
    assert "Lead technical discovery with customers." in job.description
    assert "Own complex product deployments." in job.description
    assert "<strong>" not in job.description
    assert "<li>" not in job.description


@patch("jobpilot.core.portal_scanner.requests.get")
def test_target_runs_record_success_zero_and_failure(mock_get: MagicMock) -> None:
    mock_get.side_effect = [
        _response({"jobs": [{"id": 1, "title": "Engineer"}]}),
        _response({"jobs": []}),
        RuntimeError("service unavailable"),
    ]
    scanner = PortalScanner()

    jobs = scanner.scan_targets([
        ScanTarget("greenhouse", "one"),
        ScanTarget("greenhouse", "zero"),
        ScanTarget("greenhouse", "broken"),
    ])

    assert len(jobs) == 1
    assert [run.status for run in scanner.last_run_results] == ["success", "zero", "failure"]
    assert [run.result_count for run in scanner.last_run_results] == [1, 0, 0]
    assert scanner.last_run_results[-1].error_type == "RuntimeError"
    assert "service unavailable" not in scanner.last_run_results[-1].error


@patch("jobpilot.core.portal_scanner.requests.get")
def test_http_failures_retain_status_for_actionable_source_health(
    mock_get: MagicMock,
) -> None:
    response = requests.Response()
    response.status_code = 404
    failure = requests.HTTPError("unavailable", response=response)
    mock_get.return_value = MagicMock(
        raise_for_status=MagicMock(side_effect=failure),
    )

    scanner = PortalScanner()
    assert scanner.scan_targets([ScanTarget("greenhouse", "retired-board")]) == []

    assert scanner.last_run_results[0].status == "failure"
    assert scanner.last_run_results[0].error_type == "HTTP404"
    assert scanner.last_run_results[0].error == "scan failed"


@pytest.mark.parametrize("portal", ["greenhouse", "lever", "ashby"])
def test_direct_jobs_convert_to_normalized_observations(portal: str) -> None:
    from jobpilot.core.portal_scanner import PortalJob

    job = PortalJob(
        company="Acme", title="Engineer", url="https://example.test/jobs/1",
        portal=portal, provider_tenant="acme", provider_job_id="1",
        canonical_url="https://example.test/jobs/1", description="Complete JD",
        listing_state="listed",
    )

    observation = job.to_source_observation()

    assert observation.provider == portal
    assert observation.source_kind == "direct_ats"
    assert observation.description == "Complete JD"
