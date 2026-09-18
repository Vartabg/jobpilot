"""Long-lived Chrome process lifecycle for JobPilot's CDP bridge."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import requests

from jobpilot.core.config import (
    CHROME_EXECUTABLE,
    SYSTEM_CHROME,
    SYSTEM_CHROME_APP,
)
from jobpilot.core.logger import get_logger

log = get_logger(__name__)

LINKEDIN_JOBS = "https://www.linkedin.com/jobs/"
CDP_READY_TIMEOUT_S = 20.0
CDP_POLL_S = 0.25


def chrome_executable() -> str | None:
    """Prefer an approved override, then system Chrome."""
    if CHROME_EXECUTABLE:
        candidate = Path(CHROME_EXECUTABLE).expanduser()
        if candidate.exists():
            return str(candidate)
        log.warning("JOBPILOT_CHROME_EXECUTABLE does not exist: %s", candidate)
    return str(SYSTEM_CHROME) if SYSTEM_CHROME.exists() else None


def cdp_ready(debug_url: str, timeout: float = 1.0) -> bool:
    try:
        response = requests.get(f"{debug_url}/json/version", timeout=timeout)
        return response.status_code == 200
    except requests.exceptions.RequestException:
        return False


def wait_for_cdp(debug_url: str, timeout_s: float = CDP_READY_TIMEOUT_S) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if cdp_ready(debug_url, timeout=0.5):
            return True
        time.sleep(CDP_POLL_S)
    return False


def profile_in_use(profile_dir: Path) -> bool:
    """Return whether a live Chrome process owns this user-data directory."""
    needle = str(profile_dir.resolve())
    try:
        result = subprocess.run(
            ["pgrep", "-fl", "Google Chrome"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if any(
        needle in line and "user-data-dir=" in line
        for line in (result.stdout or "").splitlines()
    ):
        return True
    lock = profile_dir / "SingletonLock"
    if not (lock.is_symlink() or lock.exists()):
        return False
    try:
        target = os.readlink(lock) if lock.is_symlink() else ""
        maybe_pid = target.rsplit("-", 1)[-1]
        if "-" in target and maybe_pid.isdigit():
            os.kill(int(maybe_pid), 0)
            return True
    except OSError:
        pass
    return False


def clear_stale_locks(profile_dir: Path) -> None:
    """Remove Singleton locks only after proving the profile is unused."""
    if profile_in_use(profile_dir):
        log.info("[Chrome] Profile already in use → locks preserved: %s", profile_dir)
        return
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        path = profile_dir / name
        if not (path.is_symlink() or path.exists()):
            continue
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            log.warning("[Chrome] Stale lock cleanup failed → %s: %s", path, exc)


def _launch_command(debug_port: int, profile_dir: Path) -> list[str] | None:
    args = [
        f"--remote-debugging-port={debug_port}",
        "--enable-automation",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=ChromeWhatsNewUI",
        LINKEDIN_JOBS,
    ]
    if SYSTEM_CHROME_APP.exists() and sys.platform == "darwin":
        return ["open", "-na", str(SYSTEM_CHROME_APP), "--args", *args]
    executable = chrome_executable()
    return [executable, *args] if executable else None


def spawn_debug_chrome(debug_port: int, profile_dir: Path) -> bool:
    """Spawn detached Chrome without opening a second copy of the profile."""
    profile_dir.mkdir(parents=True, exist_ok=True)
    debug_url = f"http://127.0.0.1:{debug_port}"
    if cdp_ready(debug_url, timeout=0.8):
        return True
    if profile_in_use(profile_dir):
        log.warning(
            "[Chrome] Profile busy → waiting for existing CDP at %s",
            debug_url,
        )
        return wait_for_cdp(debug_url, timeout_s=12.0)
    clear_stale_locks(profile_dir)
    command = _launch_command(debug_port, profile_dir)
    if not command:
        log.error("[Chrome] Launch failed → no executable found")
        return False
    try:
        subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        log.info(
            "[Chrome] Detached launch requested → port %s, profile %s",
            debug_port,
            profile_dir,
        )
        return True
    except OSError as exc:
        log.error("[Chrome] Launch failed → %s", exc)
        return False
