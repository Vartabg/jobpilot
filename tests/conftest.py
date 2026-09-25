"""Keep the whole test suite away from real JobPilot data.

JobPilot resolves its data folder (profile, tracker, queue, settings, logs)
at import time, so JOBPILOT_DATA_DIR must point at a throwaway directory
before any jobpilot module is imported. pytest imports this root conftest
before collecting test modules, which is early enough.

The session guard fails the run if a test still writes into the checkout's
real data/ folder, so a module that builds its own path instead of using
config.DATA_DIR is caught immediately rather than silently overwriting the
user's files. Log files are excluded: a JobPilot service running from this
checkout may legitimately append to them while the tests run.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

_REAL_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_TMP = Path(tempfile.mkdtemp(prefix="jobpilot-tests-"))

os.environ["JOBPILOT_DATA_DIR"] = str(_TMP / "data")


def _snapshot(root: Path) -> dict[str, tuple[int, int]]:
    if not root.is_dir():
        return {}
    state = {}
    for path in root.rglob("*"):
        if path.is_file() and ".log" not in path.name:
            stat = path.stat()
            state[str(path.relative_to(root))] = (stat.st_mtime_ns, stat.st_size)
    return state


_BEFORE = _snapshot(_REAL_DATA_DIR)


def pytest_sessionfinish(session: pytest.Session) -> None:
    after = _snapshot(_REAL_DATA_DIR)
    touched = sorted(
        name
        for name in _BEFORE.keys() | after.keys()
        if _BEFORE.get(name) != after.get(name)
    )
    if not touched:
        return
    session.exitstatus = pytest.ExitCode.TESTS_FAILED
    message = "Tests modified the real data/ folder: " + ", ".join(touched)
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line(message, red=True, bold=True)
    else:
        print(message)
