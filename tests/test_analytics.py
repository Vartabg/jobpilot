"""
Tests for analytics.py — CSV export and daily digest report.
"""

import csv
import pytest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock
from jobpilot.core.analytics import export_csv, daily_digest
from jobpilot.core.application_tracker import ApplicationTracker


class TestExportCsv:

    def test_export_creates_file(self, tmp_path: Path):
        output = tmp_path / "test_export.csv"

        # Mock the tracker to return some fake data
        mock_tracker = MagicMock()
        mock_tracker.get_recent.return_value = []
        mock_tracker.close.return_value = None

        with patch("jobpilot.core.analytics.get_application_tracker", return_value=mock_tracker):
            result = export_csv(days=30, output_path=output)

        assert result == output
        assert output.exists()
        content = output.read_text()
        assert "Job Title" in content  # header row

    def test_export_respects_output_path(self, tmp_path: Path):
        custom = tmp_path / "custom.csv"
        mock_tracker = MagicMock()
        mock_tracker.get_recent.return_value = []
        mock_tracker.close.return_value = None

        with patch("jobpilot.core.analytics.get_application_tracker", return_value=mock_tracker):
            result = export_csv(output_path=custom)

        assert result == custom


@pytest.fixture()
def tracker(tmp_path: Path) -> ApplicationTracker:
    return ApplicationTracker(data_dir=tmp_path)


class TestExportCsvWithRealData:
    """End-to-end: real rows in a real SQLite DB flow through to the CSV."""

    def test_export_writes_tracked_application(self, tracker: ApplicationTracker, tmp_path: Path):
        tracker.log_application(
            company="Acme Robotics",
            title="Software Engineer",
            url="https://jobs.example.com/swe-1",
            status="applied",
        )
        output = tmp_path / "export.csv"

        with patch("jobpilot.core.analytics.get_application_tracker", return_value=tracker):
            result = export_csv(days=30, output_path=output)

        with open(result, newline="") as f:
            rows = list(csv.reader(f))

        assert len(rows) == 2  # header + one application
        header, data = rows
        assert header[:5] == ["Job Title", "Company", "URL", "Status", "Applied At"]
        assert data[0] == "Software Engineer"
        assert data[1] == "Acme Robotics"
        assert data[2] == "https://jobs.example.com/swe-1"
        assert data[3] == "applied"
        assert data[4] == datetime.now().date().isoformat()

    def test_export_filters_out_old_applications(self, tracker: ApplicationTracker, tmp_path: Path):
        tracker.log_application(
            company="Old Corp",
            title="Archivist",
            status="applied",
            applied_at="2020-01-01",
        )
        tracker.log_application(
            company="New Corp",
            title="Engineer",
            status="applied",
        )
        output = tmp_path / "export.csv"

        with patch("jobpilot.core.analytics.get_application_tracker", return_value=tracker):
            export_csv(days=30, output_path=output)

        content = output.read_text()
        assert "New Corp" in content
        assert "Old Corp" not in content


class TestDailyDigest:
    """End-to-end: the Rich report renders from real DB rows without crashing."""

    def _mock_recorder(self):
        recorder = MagicMock()
        recorder.get_stats.return_value = {"fields_approved": 3, "fields_edited": 1}
        return recorder

    def test_digest_with_real_rows(self, tracker: ApplicationTracker, capsys):
        tracker.mark_started("https://jobs.example.com/2", "Data Analyst", "Globex")
        tracker.mark_submitted("https://jobs.example.com/2")
        tracker.mark_started("https://jobs.example.com/3", "QA Tester", "Initech")

        with patch("jobpilot.core.analytics.get_application_tracker", return_value=tracker), \
             patch("jobpilot.core.analytics.get_action_recorder", return_value=self._mock_recorder()):
            daily_digest(days=7)

        out = capsys.readouterr().out
        assert "Globex" in out
        assert "Initech" in out
        assert "Data Analyst" in out
        assert "submitted" in out

    def test_digest_empty_db(self, tracker: ApplicationTracker, capsys):
        with patch("jobpilot.core.analytics.get_application_tracker", return_value=tracker), \
             patch("jobpilot.core.analytics.get_action_recorder", return_value=self._mock_recorder()):
            daily_digest(days=7)

        out = capsys.readouterr().out
        assert "No applications recorded yet" in out
