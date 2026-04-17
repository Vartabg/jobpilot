"""
Tests for analytics.py — CSV export path generation.

Note: daily_digest() is a Rich rendering function tested manually.
We test the pure-logic paths here.
"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from jobpilot.core.analytics import export_csv


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
