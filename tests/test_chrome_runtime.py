"""Process-boundary tests for detached JobPilot Chrome."""

from unittest.mock import MagicMock, patch

from jobpilot.core import chrome_runtime as runtime


def test_spawn_debug_chrome_uses_detached_open_on_macos(tmp_path):
    app = MagicMock(exists=MagicMock(return_value=True))
    with (
        patch.object(runtime, "SYSTEM_CHROME_APP", app),
        patch.object(runtime.sys, "platform", "darwin"),
        patch.object(runtime.subprocess, "Popen") as popen,
        patch.object(runtime, "cdp_ready", return_value=False),
        patch.object(runtime, "profile_in_use", return_value=False),
        patch.object(runtime, "clear_stale_locks"),
    ):
        ok = runtime.spawn_debug_chrome(9222, tmp_path)

    assert ok is True
    command = popen.call_args.args[0]
    assert command[0] == "open"
    assert "-na" in command
    assert any("remote-debugging-port=9222" in str(arg) for arg in command)
    assert "--enable-automation" in command
    assert not any("remote-allow-origins" in str(arg) for arg in command)
    assert popen.call_args.kwargs["start_new_session"] is True


def test_spawn_does_not_second_instance_when_profile_in_use(tmp_path):
    with (
        patch.object(runtime, "cdp_ready", return_value=False),
        patch.object(runtime, "profile_in_use", return_value=True),
        patch.object(runtime, "wait_for_cdp", return_value=True) as wait,
        patch.object(runtime.subprocess, "Popen") as popen,
    ):
        ok = runtime.spawn_debug_chrome(9222, tmp_path)

    assert ok is True
    popen.assert_not_called()
    wait.assert_called_once()


def test_stale_lock_cleanup_refuses_live_profile(tmp_path):
    lock = tmp_path / "SingletonLock"
    lock.write_text("sentinel", encoding="utf-8")
    with patch.object(runtime, "profile_in_use", return_value=True):
        runtime.clear_stale_locks(tmp_path)
    assert lock.read_text(encoding="utf-8") == "sentinel"
