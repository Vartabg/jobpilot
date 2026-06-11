"""Lock placement — lock files must never sit beside iCloud-synced files."""
from __future__ import annotations

from pathlib import Path

from jobpilot.gigs.core import io_lock
from jobpilot.gigs.core.paths import data_dir


def test_lock_file_lives_under_data_locks_not_beside_target(
    tmp_path: Path, monkeypatch,
) -> None:
    locks_dir = tmp_path / "locks"
    monkeypatch.setattr(io_lock, "LOCKS_DIR", locks_dir)

    icloud = tmp_path / "icloud"
    icloud.mkdir()
    target = icloud / "pipeline.md"

    with io_lock.file_lock(target):
        assert (locks_dir / "pipeline.md.lock").exists()
        # Nothing appears next to the target — a sibling .lock would sync
        # to every device via iCloud.
        assert list(icloud.iterdir()) == []


def test_default_locks_dir_is_inside_repo_data() -> None:
    # LOCKS_DIR must live under the repo-local data dir (env-overridable via
    # GIGPILOT_DATA_DIR — the tests point it at a tmp dir), never beside
    # iCloud-synced targets.
    assert io_lock.LOCKS_DIR == data_dir() / "locks"
