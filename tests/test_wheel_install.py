"""Release smoke test for the built wheel, not the repository import path."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    return subprocess.run(
        args,
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=180,
    )


def test_built_wheel_installs_and_runs_cli_outside_repository(tmp_path: Path) -> None:
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is required for the isolated wheel-install smoke test")

    dist_dir = tmp_path / "dist"
    venv_dir = tmp_path / "venv"
    dist_dir.mkdir()

    _run(uv, "build", "--wheel", "--out-dir", str(dist_dir), cwd=REPO_ROOT)
    wheel = next(dist_dir.glob("jobpilot-*.whl"))

    with ZipFile(wheel) as archive:
        payload = {name for name in archive.namelist() if ".dist-info/" not in name}
    assert payload
    assert all(name.startswith("jobpilot/") for name in payload)
    assert "jobpilot/cli.py" in payload
    assert "jobpilot/ui/dashboard.html" in payload
    assert "jobpilot/gigs/swipe.html" in payload
    assert not any("/tests/" in name or "/docs/" in name for name in payload)

    _run(uv, "venv", "--python", sys.executable, str(venv_dir), cwd=tmp_path)
    python = venv_dir / "bin" / "python"
    command = venv_dir / "bin" / "jobpilot"
    _run(uv, "pip", "install", "--python", str(python), str(wheel), cwd=tmp_path)

    help_result = _run(str(command), "--help", cwd=tmp_path)
    assert "Evidence-led job search" in help_result.stdout

    probe = _run(
        str(python),
        "-c",
        (
            "import jobpilot; "
            "from jobpilot import cli; "
            "from jobpilot.core.config import DATA_DIR; "
            "from jobpilot.core.server import DASHBOARD_PATH; "
            "from jobpilot.gigs.server import _PAGE; "
            "assert cli.app and DASHBOARD_PATH.is_file() and _PAGE.is_file(); "
            "assert 'site-packages' not in DATA_DIR.parts; "
            "print(jobpilot.__file__, DATA_DIR)"
        ),
        cwd=tmp_path,
    )
    assert str(venv_dir) in probe.stdout
    assert str(REPO_ROOT) not in probe.stdout

    logging_probe = _run(
        str(python),
        "-c",
        (
            "from jobpilot.core.logger import get_logger; "
            "get_logger('release-smoke').info('diagnostic-only')"
        ),
        cwd=tmp_path,
    )
    assert "diagnostic-only" not in logging_probe.stdout
    assert "diagnostic-only" in logging_probe.stderr
