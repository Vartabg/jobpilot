"""Safety checks for upgrading an existing JobPilot installation."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

INSTALLER = Path(__file__).parents[1] / "install.sh"


def _call_installer_function(function: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "/bin/bash",
            "-c",
            'source "$1"; shift; "$@"',
            "jobpilot-installer-test",
            str(INSTALLER),
            function,
            *arguments,
        ],
        capture_output=True,
        check=False,
        text=True,
    )


def _init_repo(path: Path, *, branch: str = "main", origin: str) -> None:
    subprocess.run(
        ["git", "init", "--quiet", "--initial-branch", branch, str(path)],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "remote", "add", "origin", origin],
        check=True,
    )


@pytest.mark.parametrize(
    "origin",
    [
        "https://github.com/Vartabg/jobpilot",
        "https://github.com/Vartabg/jobpilot.git",
        "https://github.com/Vartabg/jobpilot.git/",
        "git@github.com:Vartabg/jobpilot",
        "git@github.com:Vartabg/jobpilot.git",
        "ssh://git@github.com/Vartabg/jobpilot",
        "ssh://git@github.com/Vartabg/jobpilot.git",
    ],
)
def test_canonical_origin_allowlist_accepts_only_safe_supported_forms(origin: str) -> None:
    result = _call_installer_function("is_canonical_jobpilot_origin", origin)

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "origin",
    [
        "https://github.com.evil.test/Vartabg/jobpilot.git",
        "https://user@github.com/Vartabg/jobpilot.git",
        "https://github.com/attacker/jobpilot.git",
        "git://github.com/Vartabg/jobpilot.git",
        "../jobpilot",
    ],
)
def test_canonical_origin_allowlist_rejects_lookalikes(origin: str) -> None:
    result = _call_installer_function("is_canonical_jobpilot_origin", origin)

    assert result.returncode != 0


def test_existing_install_verification_accepts_clean_main_checkout(tmp_path: Path) -> None:
    repo = tmp_path / "jobpilot"
    _init_repo(repo, origin="https://github.com/Vartabg/jobpilot.git")

    result = _call_installer_function("verify_existing_install", str(repo))

    assert result.returncode == 0, result.stdout + result.stderr


def test_existing_install_verification_rejects_wrong_origin(tmp_path: Path) -> None:
    repo = tmp_path / "jobpilot"
    _init_repo(repo, origin="https://github.com/attacker/jobpilot.git")

    result = _call_installer_function("verify_existing_install", str(repo))

    assert result.returncode != 0
    assert "not the canonical Vartabg/jobpilot repository" in result.stdout


def test_existing_install_verification_rejects_dirty_checkout(tmp_path: Path) -> None:
    repo = tmp_path / "jobpilot"
    _init_repo(repo, origin="git@github.com:Vartabg/jobpilot.git")
    (repo / "local-notes.txt").write_text("do not overwrite\n")

    result = _call_installer_function("verify_existing_install", str(repo))

    assert result.returncode != 0
    assert "local changes or untracked files" in result.stdout


def test_existing_install_verification_rejects_unexpected_branch(tmp_path: Path) -> None:
    repo = tmp_path / "jobpilot"
    _init_repo(
        repo,
        branch="feature",
        origin="ssh://git@github.com/Vartabg/jobpilot.git",
    )

    result = _call_installer_function("verify_existing_install", str(repo))

    assert result.returncode != 0
    assert "not on 'main'" in result.stdout


def test_existing_checkout_is_verified_before_fast_forward_pull() -> None:
    script = INSTALLER.read_text()
    existing = script[script.index('if [[ -e "$INSTALL_DIR" ]]'):]

    first_verification = existing.index('verify_existing_install "$INSTALL_DIR"')
    pull = existing.index("pull --ff-only --quiet")
    second_verification = existing.index(
        'verify_existing_install "$INSTALL_DIR"',
        first_verification + 1,
    )
    install = existing.index("pip install -e .")
    assert first_verification < pull < second_verification < install


def test_piped_installer_still_executes_main(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / "jobpilot").mkdir(parents=True)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_uname = fake_bin / "uname"
    fake_uname.write_text("#!/usr/bin/env bash\nprintf 'Darwin\\n'\n")
    fake_uname.chmod(0o755)
    env = {
        **os.environ,
        "HOME": str(home),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "SHELL": "/bin/zsh",
    }

    result = subprocess.run(
        ["/bin/bash"],
        input=INSTALLER.read_text(),
        capture_output=True,
        check=False,
        env=env,
        text=True,
    )

    assert result.returncode != 0
    assert "is not a Git working tree" in result.stdout
