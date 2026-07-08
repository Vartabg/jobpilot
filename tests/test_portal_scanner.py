"""Tests for core/portal_scanner.py — job-board scanning and filtering."""

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

from jobpilot.core.portal_scanner import PortalJob, PortalScanner, ScanTarget


class TestPortalScanner:
    @patch("jobpilot.core.portal_scanner.requests.get")
    def test_scans_greenhouse_jobs_and_filters_by_keyword(self, mock_get: MagicMock):
        payload: dict[str, list[dict[str, object]]] = {
            "jobs": [
                {
                    "title": "Senior Frontend Engineer",
                    "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
                    "location": {"name": "Remote"},
                },
                {
                    "title": "Finance Manager",
                    "absolute_url": "https://boards.greenhouse.io/acme/jobs/2",
                    "location": {"name": "Austin"},
                },
            ]
        }
        mock_get.return_value = MagicMock(
            raise_for_status=lambda: None,
            json=MagicMock(return_value=payload),
        )

        scanner = PortalScanner(keywords=["frontend", "react"])
        jobs = scanner.scan_greenhouse_board("acme")

        assert len(jobs) == 1
        assert jobs[0].title == "Senior Frontend Engineer"
        assert jobs[0].portal == "greenhouse"
        assert "frontend" in jobs[0].matched_keywords

    @patch("jobpilot.core.portal_scanner.requests.get")
    def test_scans_lever_jobs(self, mock_get: MagicMock):
        payload: list[dict[str, object]] = [
            {
                "text": "Platform Engineer",
                "hostedUrl": "https://jobs.lever.co/acme/123",
                "categories": {"location": "Remote US"},
                "company": "Acme",
            }
        ]
        mock_get.return_value = MagicMock(
            raise_for_status=lambda: None,
            json=MagicMock(return_value=payload),
        )

        scanner = PortalScanner(keywords=["platform"])
        jobs = scanner.scan_lever_board("acme")

        assert len(jobs) == 1
        assert jobs[0].company == "Acme"
        assert jobs[0].location == "Remote US"

    @patch("jobpilot.core.portal_scanner.requests.get")
    def test_scans_ashby_uses_location_and_secondary_locations(self, mock_get: MagicMock):
        payload = {
            "jobs": [
                {
                    "id": "123",
                    "title": "Forward Deployed Engineer",
                    "location": "New York City",
                    "secondaryLocations": [
                        {"location": "Remote (United States)"},
                        {"location": "Remote (Canada)"},
                    ],
                    "jobUrl": "https://jobs.ashbyhq.com/acme/123",
                },
                {
                    "id": "456",
                    "title": "Forward Deployed Engineer",
                    "location": "London",
                    "jobUrl": "https://jobs.ashbyhq.com/acme/456",
                },
            ]
        }
        mock_get.return_value = MagicMock(
            raise_for_status=lambda: None,
            json=MagicMock(return_value=payload),
        )

        scanner = PortalScanner(keywords=["forward deployed"])
        jobs = scanner.scan_ashby_board("acme")

        assert len(jobs) == 2
        assert jobs[0].location == "New York City; Remote (United States); Remote (Canada)"
        assert jobs[1].location == "London"

    @patch("jobpilot.core.portal_scanner.ADZUNA_APP_ID", "test_app_id")
    @patch("jobpilot.core.portal_scanner.ADZUNA_API_KEY", "secret_api_key")
    @patch("jobpilot.core.portal_scanner.requests.get")
    def test_adzuna_http_error_logs_without_credentials(self, mock_get: MagicMock, caplog):
        response = requests.Response()
        response.status_code = 429
        response.url = "https://api.adzuna.com/jobs?app_id=test_app_id&app_key=secret_api_key"
        error = requests.HTTPError(
            "429 Client Error: Too Many Requests for url: "
            "https://api.adzuna.com/jobs?app_id=test_app_id&app_key=secret_api_key",
            response=response,
        )
        mock_get.return_value = MagicMock(raise_for_status=MagicMock(side_effect=error))

        scanner = PortalScanner()
        with caplog.at_level(logging.WARNING, logger="jobpilot.jobpilot.core.portal_scanner"):
            jobs = scanner.scan_adzuna("field service technician")

        assert jobs == []
        assert "HTTP 429" in caplog.text
        assert "secret_api_key" not in caplog.text
        assert "app_key" not in caplog.text

    def test_save_report_writes_json(self, tmp_path: Path):
        scanner = PortalScanner(keywords=["python"])
        jobs = scanner.scan_targets([
            ScanTarget(portal="manual", value="https://example.com", label="Example")
        ])
        assert jobs == []

        sample_jobs: list[PortalJob] = []
        report = scanner.save_report(sample_jobs, directory=tmp_path)

        assert report.exists()
        assert report.suffix == ".json"
        assert report.read_text().strip().startswith("[")
