"""JOBPILOT_DATA_DIR must relocate every piece of JobPilot state.

Paths are resolved at import time, so this probes a fresh interpreter rather
than reloading modules inside the test session.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

PROBE = """
import json
from jobpilot import cli
from jobpilot.core import (
    analytics, application_tracker, config, cover_letter_gen,
    interview_prep, logger, resume_tailor,
)
from jobpilot.gigs.core import paths

print(json.dumps({
    "config": str(config.DATA_DIR),
    "settings": str(config.SETTINGS_FILE),
    "log": str(logger.LOG_FILE),
    "tracker": str(application_tracker.DB_DIR),
    "analytics": str(analytics.DATA_DIR),
    "cover_letters": str(cover_letter_gen.DATA_DIR),
    "resumes": str(resume_tailor.OUTPUT_DIR),
    "prep_reports": str(interview_prep.OUTPUT_DIR),
    "cli_reports": str(cli.CLAUDE_VETTED_TARGETS_DIR),
    "gigs": str(paths.data_dir()),
}))
"""


def test_data_dir_env_relocates_all_state(tmp_path):
    root = tmp_path / "state"
    env = dict(os.environ)
    env["JOBPILOT_DATA_DIR"] = str(root)
    env.pop("GIGPILOT_DATA_DIR", None)
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(REPO.parent), env.get("PYTHONPATH", "")) if part
    )

    result = subprocess.run(
        [sys.executable, "-c", PROBE],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )

    resolved = json.loads(result.stdout.strip().splitlines()[-1])
    for name, value in resolved.items():
        assert Path(value).is_relative_to(root), (
            f"{name} escaped JOBPILOT_DATA_DIR: {value}"
        )
