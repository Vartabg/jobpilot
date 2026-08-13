"""Privacy and managed-restart regressions for the JobPilot boot script."""

from __future__ import annotations

import contextlib
import os
import subprocess
import time
from pathlib import Path

BOOT_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "boot.sh"
SWIPE_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "swipe_serve.sh"
SWIPE_INSTALLER = (
    Path(__file__).resolve().parents[1] / "scripts" / "install_swipe_launchd.sh"
)


def _source() -> str:
    return BOOT_SCRIPT.read_text()


def _write_executable(path: Path, source: str) -> None:
    path.write_text(source)
    path.chmod(0o755)


def _isolated_boot(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    root = tmp_path / "jobpilot"
    scripts = root / "scripts"
    bin_dir = root / ".venv" / "bin"
    home = tmp_path / "home"
    scripts.mkdir(parents=True)
    bin_dir.mkdir(parents=True)
    home.mkdir()

    boot = scripts / "boot.sh"
    _write_executable(boot, _source())
    (bin_dir / "activate").write_text(
        f'export PATH="{bin_dir}:$PATH"\n'
    )
    _write_executable(
        bin_dir / "curl",
        '#!/bin/sh\nprintf "200"\n',
    )
    _write_executable(
        bin_dir / "launchctl",
        "#!/bin/sh\nexit 1\n",
    )
    _write_executable(
        bin_dir / "tailscale",
        '#!/bin/sh\n[ "$1" = "ip" ] && printf "100.64.10.20\\n"\n',
    )
    _write_executable(
        bin_dir / "jobpilot",
        """#!/bin/bash
printf '%s\n' "$*" >>"$JOBPILOT_TEST_INVOCATIONS"
trap 'exit 0' TERM INT
while :; do sleep 0.2; done
""",
    )
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "PATH": f"{bin_dir}:{env['PATH']}",
            "JOBPILOT_TEST_INVOCATIONS": str(tmp_path / "invocations"),
        }
    )
    return boot, env


def _pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _wait_for_exit(pid: int) -> bool:
    for _ in range(40):
        if not _pid_is_alive(pid):
            return True
        time.sleep(0.1)
    return False


def _stop_pid_files(home: Path) -> None:
    for name in ("serve.pid", "swipe.pid"):
        pid_file = home / ".jobpilot" / name
        if not pid_file.exists():
            continue
        pid = int(pid_file.read_text().strip())
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, 15)
        _wait_for_exit(pid)


def test_dashboard_and_swipe_default_to_loopback():
    source = _source()

    assert 'REMOTE_ACCESS_MODE="${JOBPILOT_REMOTE_ACCESS:-local}"' in source
    assert 'SERVE_HOST="127.0.0.1"' in source
    assert 'SWIPE_BIND_HOST="127.0.0.1"' in source
    assert "if command -v tailscale" not in source


def test_non_loopback_binding_requires_exact_tailscale_opt_in():
    source = _source()
    opt_in_start = source.index("  tailscale)\n")
    opt_in_end = source.index("    ;;\n", opt_in_start)
    opt_in = source[opt_in_start:opt_in_end]

    assert 'SERVE_HOST="$TS_IP"' in opt_in
    assert 'SWIPE_BIND_HOST="$TS_IP"' in opt_in
    assert "JOBPILOT REMOTE ACCESS IS ENABLED" in opt_in
    assert "JOBPILOT_REMOTE_TOKEN" in opt_in
    assert "32 URL-safe random characters" in opt_in
    assert "must be 'local' or 'tailscale'" in source


def test_launch_agent_receives_the_fail_closed_privacy_mode():
    source = _source()

    assert (
        'launchctl setenv JOBPILOT_REMOTE_ACCESS "$REMOTE_ACCESS_MODE"'
        in source
    )
    assert 'launchctl kickstart -k' in source
    assert 'launchctl kickstart -k "gui/${UID}/${SWIPE_LABEL}"' in source
    assert 'launchctl setenv JOBPILOT_REMOTE_TOKEN "$REMOTE_TOKEN"' in source
    assert 'kickstart -k "gui/${UID}/${SWIPE_LABEL}" >/dev/null 2>&1 || true' not in source


def test_login_started_swipe_uses_the_same_explicit_opt_in():
    source = SWIPE_SCRIPT.read_text()

    assert 'REMOTE_ACCESS_MODE="${JOBPILOT_REMOTE_ACCESS:-local}"' in source
    assert 'HOST="127.0.0.1"' in source
    assert "  tailscale)" in source
    assert 'HOST="$(tailscale ip -4' in source
    assert "SWIPE REMOTE ACCESS IS ENABLED" in source
    assert "JOBPILOT_REMOTE_TOKEN" in source
    assert "32 URL-safe random characters" in source
    assert "JOBPILOT_SWIPE_HOST" not in source
    assert 'if [[ -z "$HOST" ]] && command -v tailscale' not in source


def test_launchd_installer_describes_loopback_default_and_phone_opt_in():
    source = SWIPE_INSTALLER.read_text()

    assert "loopback-only by default" in source
    assert "JOBPILOT_REMOTE_ACCESS=tailscale" in source
    assert "JOBPILOT_REMOTE_TOKEN" in source


def test_remote_health_check_authenticates_without_query_token_logging():
    source = _source()

    assert '-H "X-JobPilot-Token: ${REMOTE_TOKEN}"' in source
    assert "api/queue?token=" not in source
    assert "access_log" not in source


def test_boot_restarts_managed_services_when_access_mode_changes(tmp_path):
    boot, base_env = _isolated_boot(tmp_path)
    remote_env = base_env | {
        "JOBPILOT_REMOTE_ACCESS": "tailscale",
        "JOBPILOT_REMOTE_TOKEN": "a" * 40,
    }
    local_env = base_env | {"JOBPILOT_REMOTE_ACCESS": "local"}
    local_env.pop("JOBPILOT_REMOTE_TOKEN", None)
    home = Path(base_env["HOME"])

    first = subprocess.run(
        ["/bin/bash", str(boot), "--quiet"],
        env=remote_env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    try:
        assert first.returncode == 0, first.stderr
        old_dashboard = int(
            (home / ".jobpilot" / "serve.pid").read_text().strip()
        )
        old_swipe = int(
            (home / ".jobpilot" / "swipe.pid").read_text().strip()
        )

        second = subprocess.run(
            ["/bin/bash", str(boot), "--quiet"],
            env=local_env,
            capture_output=True,
            text=True,
            timeout=15,
        )

        assert second.returncode == 0, second.stderr
        assert _wait_for_exit(old_dashboard)
        assert _wait_for_exit(old_swipe)
        invocations = Path(base_env["JOBPILOT_TEST_INVOCATIONS"]).read_text()
        assert "serve --host 100.64.10.20 --port 8767" in invocations
        assert "serve --host 127.0.0.1 --port 8767" in invocations
        assert "gigs swipe --host 100.64.10.20 --port 8799" in invocations
        assert "gigs swipe --host 127.0.0.1 --port 8799" in invocations
    finally:
        _stop_pid_files(home)


def test_boot_refuses_to_kill_unrelated_live_pid(tmp_path):
    boot, env = _isolated_boot(tmp_path)
    home = Path(env["HOME"])
    pid_dir = home / ".jobpilot"
    pid_dir.mkdir()
    unrelated = subprocess.Popen(["/bin/sleep", "30"])
    (pid_dir / "serve.pid").write_text(f"{unrelated.pid}\n")

    try:
        result = subprocess.run(
            ["/bin/bash", str(boot), "--quiet"],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode != 0
        assert "unverified live process" in result.stderr
        assert _pid_is_alive(unrelated.pid)
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=5)
