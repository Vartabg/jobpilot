"""The gigs lane (former GigPilot) is mounted on the main CLI.

GIGPILOT_DATA_DIR must point at a throwaway directory *before* jobpilot.cli
is imported: importing it pulls in jobpilot.gigs.cli, which seeds a default
preferences.json into the data dir at import time. If another test module
imported jobpilot.cli first this is a no-op, which is fine — these tests only
exercise --help and never read or write gig state.
"""

import os
import tempfile

os.environ.setdefault(
    "GIGPILOT_DATA_DIR",
    tempfile.mkdtemp(prefix="jobpilot-gigs-mount-test-"),
)

from typer.testing import CliRunner

from jobpilot.cli import app

runner = CliRunner()


def test_gigs_lane_is_mounted():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "gigs" in result.stdout


def test_gigs_help_lists_commands():
    result = runner.invoke(app, ["gigs", "--help"])
    assert result.exit_code == 0
    assert "digest" in result.stdout
    assert "scan" in result.stdout
